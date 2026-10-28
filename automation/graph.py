"""
graph.py — True Multi-Agent LangGraph State Machine (Supervisor Pattern)

Architecture
------------
Each persona is a separate LangGraph node with its own system prompt and
scoped tool set. The configured orchestrator routes between specialists using
structured output decisions. Token use is optimised by injecting only the
relevant context into each agent call rather than broadcasting the full
message history or all persona files to every node.

Graph topology
--------------
  START → orchestrator ─┬→ specialist_A ──────────────────────────┐
                         ├→ [specialist_B + specialist_C] (parallel)┤ → orchestrator
                         ├→ ...                                    ┘
                         └→ Journalist → reviewer ─→ END
                                  ↑              ↘ Journalist (on rejection)

  With HITL enabled, checkpoint nodes sit on these edges:
    orchestrator → hitl_post_plan → (specialists | hitl_pre_journalist | Journalist)
    orchestrator → hitl_pre_journalist → Journalist
    reviewer → hitl_on_rejection → (Journalist | END)

Token strategy (per call)
--------------------------
  Orchestrator  : its persona prompt + findings capped at max_findings_chars each
                  (+ a quick-check tool loop when orchestrator.allow_tools is true)
  Specialist    : its persona prompt + task + per-agent orchestrator instructions
                  + peer findings capped at max_peer_chars each
  Journalist    : its persona prompt + task + full uncompressed findings
                  + orchestrator instructions + user framing + journalist.brief
  Reviewer      : reviewer prompt + journalist draft only (not full history)

Model caching
-------------
  All LLM instances are created once in build_graph() and closed over in
  node closures. This avoids re-instantiating API clients on every node call.

Per-agent instructions
----------------------
  OrchestratorDecision carries an `assignments` list of AgentAssignment objects,
  each with a per-agent instruction string. When the orchestrator dispatches
  multiple specialists in parallel, each receives differentiated guidance.
  A shared `instructions` field is kept as the single-agent fallback.

Failure handling
----------------
  orchestrator.on_agent_failure (default "continue"): a specialist or Journalist
  whose model call raises records an "[AGENT FAILED]" note instead of aborting
  the run, so parallel siblings keep their findings. A routing reply that cannot
  be parsed is retried once; if it fails again the collected findings go to the
  Journalist. "abort" restores fail-fast behaviour.

  When max_tool_rounds_per_agent runs out while an agent is still calling tools,
  one extra call with tools disabled asks it to report what it found.

Human-in-the-Loop (HITL)
-------------------------
  When hitl.enabled = true in swarm_config.yml, the graph is compiled with a
  checkpointer (MemorySaver by default; see the top-level `checkpointer` key for
  a SQLite file that survives the process) and each configured checkpoint is its
  own node:

    post_plan      — after the orchestrator's first routing decision
    pre_journalist — before the Journalist node (final framing chance)
    on_rejection   — when Reviewer-2 rejects (REVISE / OVERRIDE / custom)

  Checkpoint nodes make no LLM calls. LangGraph re-runs an interrupted node from
  its start on resume, so keeping interrupt() out of the LLM nodes guarantees
  that the plan or verdict the user answered is the one that is executed.

  The caller (main.py) detects the __interrupt__ event in the stream, prompts
  the user, and resumes with Command(resume=answer).
"""

import asyncio
import contextlib
import re
import weakref
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import run_in_executor
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, Send, interrupt  # noqa: F401 (Command re-exported for main.py)
from pydantic import BaseModel

from automation.config import (
    AGENT_METADATA_KEY,
    build_agent_system_prompt,
    build_journalist_brief,
    build_reviewer_prompt,
    create_model,
    create_reviewer_model,
    get_checkpointer_config,
    get_hitl_config,
    get_persona_config,
    load_config,
    load_tools_for_persona,
    persona_model_config,
    resolve_swarm_path,
)

# Characters retained from each peer agent's output when shown to another
# specialist. Keeps cross-agent context lean without losing key facts.
# Override with orchestrator.max_peer_chars in swarm_config.yml.
MAX_PEER_CHARS = 600

# Characters retained from each specialist's output in the orchestrator's
# routing prompt. Override with orchestrator.max_findings_chars.
MAX_FINDINGS_CHARS = 1500

# Reviewer verdicts: the prompt asks for a bare 'APPROVED', but models often
# wrap it ('**APPROVED**', 'APPROVED.'). Rejections start with 'REJECTED:'.
_APPROVED_RE = re.compile(r"^\W*APPROVED\b")

# Prefix of the note recorded in agent_outputs when an agent's model call fails.
FAILED_PREFIX = "[AGENT FAILED]"

FAILURE_POLICIES = ("continue", "abort")

EMPTY_DRAFT_FEEDBACK = (
    "REJECTED: The Journalist produced no draft text. Write the complete output now "
    "and return it as the text of your final reply, in addition to saving it with your tools."
)


# ── State ──────────────────────────────────────────────────────────────────

def _merge_agent_outputs(existing: dict, update: dict) -> dict:
    """Reducer: merge agent-output dicts; newer values win on key collision.

    Specialists fold their earlier findings into the value they write (see
    _combine_findings), so "newer wins" never discards a previous round.
    """
    return {**existing, **update}


def _merge_token_usage(existing: dict, update: dict) -> dict:
    """Reducer: accumulate token counts across all node invocations."""
    merged = dict(existing)
    for k, v in update.items():
        if isinstance(v, (int, float)):
            merged[k] = merged.get(k, 0) + v
    return merged


class GraphState(TypedDict):
    """Shared state for the multi-agent supervisor swarm.

    task                    Original user prompt — never mutated after initialisation.
    messages                Append-only audit log of all agent/reviewer messages.
    agent_outputs           {persona_name: findings text, newest round first}.
    agent_assignments       {persona_name: specific_instructions} — set by orchestrator.
    next_agents             Routing targets set by the orchestrator.
    next_instructions       Shared fallback instruction for the next agent batch;
                            Reviewer-2 feedback during revisions.
    journalist_instructions The orchestrator's instructions for the Journalist.
    user_framing            Framing typed by the user at the pre_journalist checkpoint.
    routing_reasoning       The orchestrator's reasoning for its latest decision.
    plan_reviewed           True once the post_plan checkpoint has been answered.
    agent_call_count        Count of specialist invocations only (safety valve).
    specialist_rounds       Orchestrator passes that dispatched at least one specialist.
    reviewer_approved       Set True once Reviewer-2 approves the Journalist draft.
    revision_count          Number of Journalist revision cycles completed.
    reviewer_feedback       Latest rejection reasons, shown to the Journalist on revision.
    reviewer_requests       Evidence requests from the latest structured rejection.
    review_route            Where the latest rejection goes: journalist or orchestrator.
    evidence_loop_count     Reviewer-triggered orchestrator round-trips (reviewer.max_evidence_loops).
    final_status            approved | force_approved | overridden | completed | failed.
    token_usage             Accumulated token counts across all LLM calls in the run.
    """
    task: str
    messages: Annotated[list, add_messages]
    agent_outputs: Annotated[dict, _merge_agent_outputs]
    agent_assignments: dict    # per-agent instructions; replaced on every orchestrator pass
    next_agents: list          # list[str]: specialist names | ["Journalist"]
    next_instructions: str     # fallback shared instructions
    journalist_instructions: str
    user_framing: str
    routing_reasoning: str
    plan_reviewed: bool
    agent_call_count: int      # only incremented for specialist dispatches
    specialist_rounds: int     # orchestrator passes that dispatched specialists (early stop)
    reviewer_approved: bool
    revision_count: int
    reviewer_feedback: str     # the latest rejection reasons (survive an orchestrator round)
    reviewer_requests: list    # list[dict]: EvidenceRequest objects from the latest rejection
    review_route: str          # "journalist" | "orchestrator": where the latest rejection goes
    evidence_loop_count: int   # reviewer-triggered orchestrator round-trips so far
    final_status: str
    token_usage: Annotated[dict, _merge_token_usage]


# ── Orchestrator routing schema ────────────────────────────────────────────

class AgentAssignment(BaseModel):
    """Differentiated instruction for a single specialist in a parallel dispatch."""
    agent: str
    instructions: str


class OrchestratorDecision(BaseModel):
    reasoning: str
    next_agents: list[str]            # one name (sequential) or multiple (parallel)
    instructions: str                 # shared fallback used for single-agent routing
    assignments: list[AgentAssignment] = []  # per-agent instructions for parallel dispatch
    # Read only with orchestrator.early_stop.enabled; the defaults keep older replies parsing.
    sufficient_evidence: bool = False      # the findings already answer the task
    coverage: str = ""                     # what the findings cover and what is still missing


# ── Reviewer verdict schema (reviewer.on_rejection: orchestrator | auto) ───

class EvidenceRequest(BaseModel):
    """A claim in the draft that needs more evidence from the research team."""
    claim: str
    reason: str
    suggested_agent: str | None = None     # ignored unless it names a specialist


class ReviewerVerdict(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"]
    style_issues: list[str] = []                    # the Journalist can fix these alone
    missing_evidence: list[EvidenceRequest] = []    # these need a specialist


REVIEWER_ROUTES = ("journalist", "orchestrator", "auto")


# ── Shared helpers ─────────────────────────────────────────────────────────

def _message_text(message) -> str:
    """Return the text of a message whose content may be a str or a list of blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _format_findings(agent_outputs: dict, compress: bool = False,
                     exclude: str = None,
                     max_chars: int = MAX_PEER_CHARS) -> str:
    """Serialise agent_outputs into a prompt-ready string."""
    if not agent_outputs:
        return "No findings collected yet."
    parts = []
    for agent, output in agent_outputs.items():
        if agent == exclude:
            continue
        body = _truncate(output, max_chars) if compress else output
        parts.append(f"### [{agent}]\n{body}")
    return "\n\n".join(parts) if parts else "No findings from colleagues yet."


_SENTENCE_END = re.compile(r"[.!?…][\"')\]]*(?=\s)")
_WHITESPACE = re.compile(r"\s")


def _truncate(text: str, max_chars: int) -> str:
    """Shorten ``text`` to at most ``max_chars`` (plus "…"), ending at a boundary.

    Prefers the last paragraph break, then the last sentence end, then the last
    whitespace, as long as that keeps at least half of the budget; otherwise
    (e.g. one very long token) it cuts at ``max_chars`` exactly, as before.
    """
    if len(text) <= max_chars:
        return text
    text = text.replace("\r\n", "\n")
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    floor = max(1, max_chars // 2)
    # One character of look-ahead, so a sentence ending exactly at the limit counts.
    sentence_end = max((m.end() for m in _SENTENCE_END.finditer(text[:max_chars + 1])), default=-1)
    for cut in (head.rfind("\n\n"),
                sentence_end,
                max((m.start() for m in _WHITESPACE.finditer(head)), default=-1)):
        if cut >= floor:
            return head[:cut].rstrip() + " …"
    return head + "…"


def _combine_findings(persona_name: str, previous: str, new: str) -> str:
    """Fold a specialist's earlier findings under its new ones (newest first).

    Newest-first keeps the latest round visible when the orchestrator and peers
    read a truncated view; the Journalist reads the full text. A previous
    failure note is dropped once the agent has produced something new.
    """
    if not previous or previous.startswith(FAILED_PREFIX):
        return new
    return f"{new}\n\n[Earlier findings from {persona_name}]\n{previous}"


def _failure_note(agent_name: str, error: Exception) -> str:
    return f"{FAILED_PREFIX} {agent_name}: {type(error).__name__}: {error}"


def _extract_token_usage(response) -> dict:
    """Extract token counts from a model response.

    Tries usage_metadata (LangChain ≥ 0.2) first, then response_metadata.
    Returns zero-valued dict if neither is available.
    """
    um = getattr(response, "usage_metadata", None)
    if um:
        return {
            "input_tokens": um.get("input_tokens", 0),
            "output_tokens": um.get("output_tokens", 0),
            "total_tokens": um.get("total_tokens", 0),
        }
    meta = getattr(response, "response_metadata", {}) or {}
    usage = meta.get("token_usage", {})
    return {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def _add_usage(total: dict, response) -> None:
    for k, v in _extract_token_usage(response).items():
        total[k] = total.get(k, 0) + v


def _digest_tool_transcript(transcript: list) -> str:
    """Plain-text view of an agent's tool calls and results.

    Used for the final no-tools call after the round budget runs out. Plain text
    (rather than replaying tool-call messages) works for every provider, including
    ones that refuse tool messages in a request that defines no tools.
    """
    parts = []
    for msg in transcript:
        if isinstance(msg, ToolMessage):
            parts.append(f"[Result of {msg.name or 'tool'}]\n{_message_text(msg)}")
        elif isinstance(msg, AIMessage):
            text = _message_text(msg).strip()
            if text:
                parts.append(f"[Your notes]\n{text}")
            for call in getattr(msg, "tool_calls", None) or []:
                parts.append(f"[You called {call.get('name')} with {call.get('args')}]")
    return "\n\n".join(parts) if parts else "(no tool results were returned)"


def _tool_run_config(parent: dict | None, agent_name: str | None) -> dict | None:
    """Config for a ToolNode call: the node's own config plus the calling persona.

    Passed explicitly because on Python < 3.11 the node's config does not reach
    nested runnables through context variables. A config given to ``ainvoke``
    replaces the parent's ``metadata`` mapping rather than merging it, so the
    parent's metadata is merged here. ``run_id`` belongs to the node's own run.
    """
    if parent is None and not agent_name:
        return None
    cfg = {k: v for k, v in (parent or {}).items() if k != "run_id"}
    if agent_name:
        cfg["metadata"] = {**(cfg.get("metadata") or {}), AGENT_METADATA_KEY: agent_name}
    return cfg


async def _run_tool_loop_async(model, tools: list, messages: list, max_rounds: int,
                               summary_model=None, agent_name: str = None, config: dict = None):
    """Run the agent ↔ tools loop until the model stops calling tools.

    Uses model.ainvoke and ToolNode.ainvoke so that multiple tool calls issued
    in a single model response are executed concurrently (ToolNode.ainvoke
    dispatches all tool_calls via asyncio.gather). Synchronous tool functions
    are run in a thread-pool executor automatically by LangChain's async layer.
    Tool exceptions are returned to the model as error messages.

    If the model is still calling tools when ``max_rounds`` is spent, and a
    ``summary_model`` (the same model without tools bound) is given, one more
    call asks for the final answer from the tool results gathered so far.
    Without this the agent's last message is a bare tool call with no text.

    ``agent_name`` is passed to the tools in the RunnableConfig metadata (see
    _tool_run_config) so that, e.g., traceability rows record which persona
    logged them. ``config`` is the calling node's RunnableConfig.

    Returns (final_response, accumulated_token_usage).
    """
    tool_executor = ToolNode(tools, handle_tool_errors=True) if tools else None
    tool_config = _tool_run_config(config, agent_name)
    base = list(messages)
    transcript: list = []
    response = None
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for _ in range(max_rounds):
        response = await model.ainvoke(base + transcript)
        transcript.append(response)
        _add_usage(usage, response)
        if not getattr(response, "tool_calls", None) or tool_executor is None:
            return response, usage
        tool_result = await tool_executor.ainvoke({"messages": base + transcript}, config=tool_config)
        transcript.extend(tool_result["messages"])

    if summary_model is None:
        return response, usage

    final_prompt = (
        f"{_message_text(base[-1])}\n\n"
        f"TOOL-CALL LIMIT REACHED: you have used all {max_rounds} tool rounds and cannot call "
        "any more tools. Using only the tool results below, write your complete final answer now.\n\n"
        f"{_digest_tool_transcript(transcript)}"
    )
    response = await summary_model.ainvoke(base[:-1] + [HumanMessage(content=final_prompt)])
    _add_usage(usage, response)
    return response, usage


def _unpack_routing_result(result) -> tuple:
    """Normalise the structured-output routing call to (decision, token_usage).

    With ``with_structured_output(..., include_raw=True)`` the model returns
    ``{"raw": AIMessage, "parsed": OrchestratorDecision, "parsing_error": ...}``,
    which lets us count the orchestrator's tokens. A bare decision object (older
    call sites, tests) is accepted too and counted as zero tokens.
    """
    if isinstance(result, dict) and "parsed" in result:
        if result.get("parsing_error") is not None or result["parsed"] is None:
            raise ValueError(
                f"Orchestrator returned an unparseable routing decision: {result.get('parsing_error')}"
            )
        return result["parsed"], _extract_token_usage(result.get("raw"))
    return result, _extract_token_usage(result)


def _parse_plan_override(answer: str, known_names: list) -> tuple:
    """Parse a HITL post_plan reply into (agent_names, free_text).

    Agent names must be given exactly (case-insensitive), separated by commas,
    semicolons or newlines. Anything that is not an exact name is returned as
    free text so it can be appended to the instructions instead of being
    silently dropped or, worse, substring-matched.
    """
    lookup = {name.lower(): name for name in known_names}
    agents, leftovers = [], []
    for token in re.split(r"[,;\n]+", answer):
        token = token.strip().strip(".")
        if not token:
            continue
        match = lookup.get(token.lower())
        if match and match not in agents:
            agents.append(match)
        else:
            leftovers.append(token)
    return agents, ", ".join(leftovers)


def compute_recursion_limit(config: dict) -> int:
    """Upper bound on graph steps implied by the configured safety valves.

    Worst case: every specialist dispatch is sequential (orchestrator + specialist
    = 2 steps each), one safety-valve routing step, the Journalist, and a full
    set of reviewer rejections (Journalist + reviewer per loop, plus the final
    force-approval). HITL checkpoint nodes add one step each. A small margin
    covers resume bookkeeping.
    """
    orch = config.get("orchestrator", {})
    rev = config.get("reviewer", {})
    hitl = config.get("hitl", {}) or {}
    max_calls = max(1, int(orch.get("max_agent_calls", 8)))
    max_rev = max(0, int(rev.get("max_revision_loops", 3))) if rev.get("enabled", True) else 0
    limit = 2 * max_calls + 2 * (max_rev + 1) + 4
    if rev.get("enabled", True) and rev.get("on_rejection", "journalist") != "journalist":
        # Each reviewer evidence round adds an orchestrator pass that hands back to the
        # Journalist (specialist dispatches stay bounded by max_agent_calls).
        limit += 2 * max(0, int(rev.get("max_evidence_loops", 1)))
    if hitl.get("enabled", False):
        checkpoints = set(hitl.get("checkpoints", []))
        limit += ("post_plan" in checkpoints) + ("pre_journalist" in checkpoints)
        limit += max_rev if "on_rejection" in checkpoints else 0
    return limit


def _failure_policy(config: dict) -> str:
    return config.get("orchestrator", {}).get("on_agent_failure", "continue")


class _LoopLocalSemaphore:
    """An asyncio.Semaphore per running event loop.

    A semaphore binds to the first loop that waits on it, and a compiled graph
    may be run from several loops (one asyncio.run() per run or resume), so one
    shared instance would raise "bound to a different event loop".
    """

    def __init__(self, limit: int):
        self.limit = limit
        self._by_loop = weakref.WeakKeyDictionary()

    def get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = self._by_loop.get(loop)
        if sem is None:
            sem = self._by_loop[loop] = asyncio.Semaphore(self.limit)
        return sem


# ── Orchestrator node (Dr. Nexus) ──────────────────────────────────────────

def make_orchestrator_node(config: dict, specialist_names: list, routing_model,
                           hitl_cfg: dict = None, tool_model=None, tools: list = None,
                           summary_model=None):
    """Build the orchestrator node that routes work between specialists.

    The node only decides; HITL checkpoints are separate nodes (see
    make_post_plan_node / make_pre_journalist_node). ``hitl_cfg`` is accepted
    for backwards compatibility and ignored.

    Without ``tools`` (the default, ``orchestrator.allow_tools: false``) this is
    a plain synchronous node. With ``tools`` and a ``tool_model`` bound to them,
    the node is async: before each routing decision (not on the safety-valve
    pass) the orchestrator persona runs a bounded tool loop for quick checks,
    and its short report is added to the routing prompt.
    """
    orchestrator_name = config["orchestrator"]["agent"]
    nexus_cfg = get_persona_config(config, orchestrator_name)
    system_prompt = build_agent_system_prompt(nexus_cfg)
    max_calls = config["orchestrator"].get("max_agent_calls", 8)
    max_findings_chars = config["orchestrator"].get("max_findings_chars", MAX_FINDINGS_CHARS)
    early_stop_cfg = config["orchestrator"].get("early_stop") or {}
    early_stop = early_stop_cfg.get("enabled", False) is True
    min_rounds = early_stop_cfg.get("min_rounds", 1)
    # Added to the routing prompt only when early stop is on, so the default prompt is unchanged.
    early_stop_block = (
        "\n\nEARLY STOP: if the findings already answer the task well enough to write it up, set\n"
        "`sufficient_evidence` to true and summarise in `coverage` what the evidence covers and\n"
        f"what is still missing. This is honoured once at least {min_rounds} specialist round(s) are done."
        if early_stop else ""
    )
    journalist_name = config["orchestrator"]["journalist"]
    failure_policy = _failure_policy(config)
    # 'END' is deliberately not a target: the orchestrator never runs after the
    # Journalist, so the only way to finish a run is to route to the Journalist.
    valid_targets = set(specialist_names) | {journalist_name, "Journalist"}
    specialist_set = set(specialist_names)

    def routing_context(state: GraphState) -> tuple:
        agent_outputs = state.get("agent_outputs", {})
        already_consulted = list(agent_outputs.keys())
        findings = _format_findings(agent_outputs, compress=True, max_chars=max_findings_chars)
        consulted_note = (
            f"SPECIALISTS ALREADY CONSULTED: {', '.join(already_consulted)}"
            if already_consulted else "No specialists consulted yet."
        )
        return findings, consulted_note

    def route(state: GraphState, quick_checks: str = "", check_usage: dict = None,
              check_notes: list = ()):
        call_count = state.get("agent_call_count", 0)
        agent_outputs = state.get("agent_outputs", {})

        if call_count >= max_calls:
            instructions = (
                "Synthesise all collected findings into a final structured report. "
                "The orchestrator has reached its maximum specialist call count."
            )
            return {
                "next_agents": ["Journalist"],
                "next_instructions": instructions,
                "journalist_instructions": instructions,
                "agent_assignments": {},
                "routing_reasoning": f"safety valve: {max_calls} specialist calls reached",
                **({"review_route": "journalist", "reviewer_requests": []}
                   if state.get("review_route") == "orchestrator" else {}),
                "messages": [AIMessage(content=(
                    f"[Orchestrator safety valve: {max_calls} specialist calls reached. "
                    "Routing directly to Journalist.]"
                ))],
                "token_usage": {},
            }

        findings, consulted_note = routing_context(state)
        rounds_done = state.get("specialist_rounds", 0)
        # Only present after a reviewer rejection routed back here (reviewer.on_rejection).
        requests_block = (
            _reviewer_requests_block(state, specialist_set) if state.get("review_route") == "orchestrator" else ""
        )
        # Empty unless orchestrator.allow_tools is on, so the default prompt is unchanged.
        checks_block = (
            f"YOUR QUICK CHECKS (your own tool results, gathered before this decision):\n{quick_checks}\n\n"
            if quick_checks else ""
        )

        routing_prompt = (
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"{consulted_note}\n\n"
            f"FINDINGS COLLECTED SO FAR:\n{findings}\n\n"
            f"{checks_block}"
            f"{requests_block}"
            f"ALL AVAILABLE SPECIALISTS: {', '.join(specialist_names)}\n\n"
            "Decide the next step:\n"
            "  • Route to one specialist for sequential deep investigation.\n"
            "  • Route to TWO OR MORE specialists simultaneously for independent sub-questions.\n"
            "    When routing multiple specialists, populate `assignments` with a specific\n"
            "    `instructions` string for EACH agent.\n"
            "  • Route to ['Journalist'] when sufficient evidence is ready to write.\n"
            "  • Do NOT re-route to a specialist already in ALREADY CONSULTED unless a\n"
            "    specific new sub-question requires it.\n"
            "Return: reasoning, next_agents (list), instructions (single-agent fallback),\n"
            "assignments (list of {agent, instructions} for parallel dispatch)."
            f"{early_stop_block}"
        )

        routing_notes = list(check_notes)
        try:
            decision, round_usage = _unpack_routing_result(routing_model.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=routing_prompt),
            ]))
        except GraphBubbleUp:
            raise
        except Exception as first_error:
            if failure_policy == "abort":
                raise
            try:
                decision, round_usage = _unpack_routing_result(routing_model.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=(
                        f"{routing_prompt}\n\nYour previous reply could not be used "
                        f"({type(first_error).__name__}: {first_error}). "
                        "Reply again with a valid routing decision."
                    )),
                ]))
                routing_notes.append("routing retried after an unusable reply")
            except GraphBubbleUp:
                raise
            except Exception as second_error:
                if not agent_outputs:
                    raise second_error from first_error
                decision = OrchestratorDecision(
                    reasoning=(
                        f"routing failed twice ({type(second_error).__name__}: {second_error}); "
                        "writing up the findings collected so far"
                    ),
                    next_agents=["Journalist"],
                    instructions="Synthesise all collected findings into a final structured report.",
                )
                round_usage = {}

        valid_next = list(dict.fromkeys(
            "Journalist" if t == journalist_name else t
            for t in decision.next_agents if t in valid_targets
        ))
        ignored_targets = [t for t in decision.next_agents if t not in valid_targets]
        if not valid_next:
            valid_next = ["Journalist"]

        specialist_dispatches = [t for t in valid_next if t in specialist_set]
        assignments_dict = {
            ("Journalist" if a.agent == journalist_name else a.agent): a.instructions
            for a in (decision.assignments or [])
            if a.agent in valid_targets
        }

        early_stopped = False
        # During a reviewer evidence round the reviewer has already judged the evidence
        # insufficient, so a "sufficient" claim must not cancel the requested dispatches.
        if early_stop and decision.sufficient_evidence and state.get("review_route") != "orchestrator":
            if rounds_done >= min_rounds:
                if specialist_dispatches:
                    routing_notes.append(f"early stop: evidence judged sufficient after {rounds_done} round(s); "
                                         f"skipped {specialist_dispatches}")
                    early_stopped = True
                valid_next, specialist_dispatches = ["Journalist"], []
            elif specialist_dispatches:
                routing_notes.append(
                    f"sufficient evidence claimed after {rounds_done} round(s); "
                    f"early_stop.min_rounds is {min_rounds}, so continuing"
                )

        if ignored_targets:
            routing_notes.append(f"ignored unknown targets {ignored_targets}")
        if specialist_dispatches and "Journalist" in valid_next:
            routing_notes.append("Journalist deferred until the specialists return")
        notes = f" [{'; '.join(routing_notes)}]" if routing_notes else ""

        update = {
            "next_agents": valid_next,
            "next_instructions": decision.instructions,
            "agent_assignments": assignments_dict,
            "agent_call_count": call_count + len(specialist_dispatches),
            "routing_reasoning": decision.reasoning,
            "messages": [AIMessage(content=(
                f"[Orchestrator → {valid_next}]: {decision.reasoning}{notes}"
            ))],
            "token_usage": _merge_token_usage(round_usage, check_usage) if check_usage else round_usage,
        }
        if specialist_dispatches:
            update["specialist_rounds"] = rounds_done + 1
        else:
            # After an early stop the decision's instructions were written for the skipped
            # specialists, so the Journalist gets the generic synthesis brief instead.
            instructions = (
                "Synthesise all collected findings into a final structured report." if early_stopped
                else assignments_dict.get("Journalist") or decision.instructions
            )
            if early_stop and decision.coverage:
                instructions += f"\n\nEVIDENCE COVERAGE (orchestrator's assessment):\n{decision.coverage}"
            update["journalist_instructions"] = instructions
            if state.get("review_route") == "orchestrator":
                update.update(review_route="journalist", reviewer_requests=[])   # evidence round done
        return update

    if not tools:
        def orchestrator_node(state: GraphState):
            return route(state)

        return orchestrator_node

    # ── orchestrator.allow_tools: a bounded tool loop before each routing decision ──
    checks_system_prompt = build_agent_system_prompt(
        nexus_cfg,
        epistemic_tags=config.get("epistemic_tags"),
        tool_names=[getattr(t, "name", "") for t in tools],
    )
    max_rounds = config["orchestrator"].get("max_tool_rounds_per_agent", 5)
    summary_model = summary_model or tool_model

    async def orchestrator_with_tools_node(state: GraphState, config: RunnableConfig = None):
        if state.get("agent_call_count", 0) >= max_calls:
            return route(state)   # safety valve: no checks, straight to the Journalist

        findings, consulted_note = routing_context(state)
        request = HumanMessage(content=(
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"{consulted_note}\n\n"
            f"FINDINGS COLLECTED SO FAR:\n{findings}\n\n"
            f"ALL AVAILABLE SPECIALISTS: {', '.join(specialist_names)}\n\n"
            "Before you decide the next step, run a few quick checks with your tools that help decide "
            "which specialists to consult next, or whether the evidence is already sufficient. Do not do "
            "the specialists' work. Then reply with a short report of what you checked and found."
        ))
        quick_checks, usage, notes = "", {}, []
        try:
            response, usage = await _run_tool_loop_async(
                tool_model, tools,
                [SystemMessage(content=checks_system_prompt), request],
                max_rounds,
                summary_model=summary_model,
                agent_name=orchestrator_name,
                config=config,
            )
            quick_checks = _message_text(response).strip()
        except GraphBubbleUp:
            raise
        except Exception as e:
            if failure_policy == "abort":
                raise
            notes.append(f"quick checks failed ({type(e).__name__}: {e}); routed without them")
        # The routing call is synchronous (as in the default node); keep it off the event loop.
        # run_in_executor(config, …) carries the node's callbacks, which a bare thread loses on
        # Python 3.10 (no context-var propagation into async nodes there).
        return await run_in_executor(config, route, state, quick_checks, usage, notes)

    return orchestrator_with_tools_node


# ── HITL checkpoint nodes (no LLM calls) ───────────────────────────────────

def make_post_plan_node(config: dict, specialist_names: list):
    """Pause after the first routing decision so the user can approve or redirect it.

    Reply APPROVE (default), exact persona names separated by commas to replace
    the plan, or free text, which is passed on as a USER REDIRECT note.
    """
    journalist_name = config["orchestrator"]["journalist"]
    specialist_set = set(specialist_names)

    def post_plan_node(state: GraphState):
        plan = list(state.get("next_agents", []))
        answer = interrupt({
            "checkpoint": "post_plan",
            "question": (
                f"Orchestrator's routing plan:\n"
                f"  Agents selected : {plan}\n"
                f"  Reasoning       : {state.get('routing_reasoning', '')}\n\n"
                "Type APPROVE to proceed, exact agent names (comma-separated) to replace the plan,\n"
                "or any other text to pass on as redirect instructions:"
            ),
            "default": "APPROVE",
        })
        answer = (answer or "APPROVE").strip()
        update = {"plan_reviewed": True}
        if not answer or answer.upper() == "APPROVE":
            update["messages"] = [AIMessage(content=f"[HITL post_plan]: plan {plan} approved")]
            return update

        override_agents, redirect_text = _parse_plan_override(
            answer, specialist_names + ["Journalist", journalist_name],
        )
        override_agents = list(dict.fromkeys(
            "Journalist" if a == journalist_name else a for a in override_agents
        ))
        instructions = state.get("next_instructions", "")
        assignments = dict(state.get("agent_assignments", {}))
        if override_agents:
            old_dispatches = [t for t in plan if t in specialist_set]
            new_dispatches = [t for t in override_agents if t in specialist_set]
            plan = override_agents
            assignments = {}
            update["next_agents"] = plan
            update["agent_call_count"] = (
                state.get("agent_call_count", 0) - len(old_dispatches) + len(new_dispatches)
            )
            # The orchestrator counted a specialist round for its own plan (orchestrator.early_stop).
            update["specialist_rounds"] = max(
                0, state.get("specialist_rounds", 0) - bool(old_dispatches) + bool(new_dispatches)
            )
        if redirect_text:
            note = f"\n\nUSER REDIRECT: {redirect_text}"
            instructions += note
            assignments = {name: text + note for name, text in assignments.items()}
        update["next_instructions"] = instructions
        update["agent_assignments"] = assignments
        if not any(t in specialist_set for t in plan):
            update["journalist_instructions"] = instructions
        update["messages"] = [AIMessage(content=(
            f"[HITL post_plan]: plan set to {plan}" + (f"; redirect: {redirect_text}" if redirect_text else "")
        ))]
        return update

    return post_plan_node


def make_pre_journalist_node():
    """Pause before the Journalist writes so the user can add framing instructions."""

    def pre_journalist_node(state: GraphState):
        answer = interrupt({
            "checkpoint": "pre_journalist",
            "question": (
                "The swarm is ready to write the final report.\n\n"
                "Specialist findings are in. Add final framing instructions\n"
                "(audience, length, emphasis), or press Enter to continue:"
            ),
            "default": "",
        })
        framing = (answer or "").strip()
        if not framing:
            return {"messages": [AIMessage(content="[HITL pre_journalist]: no extra framing")]}
        return {
            "user_framing": framing,
            "messages": [AIMessage(content=f"[HITL pre_journalist]: framing added: {framing[:150]}")],
        }

    return pre_journalist_node


def make_on_rejection_node():
    """Pause after a rejection: REVISE (default), OVERRIDE, or custom revision instructions."""

    def on_rejection_node(state: GraphState):
        feedback = state.get("reviewer_feedback") or state.get("next_instructions", "")
        to_orchestrator = state.get("review_route") == "orchestrator"
        target = (
            "the orchestrator, which gathers the missing evidence before the Journalist revises"
            if to_orchestrator else "Journalist"
        )
        answer = interrupt({
            "checkpoint": "on_rejection",
            "question": (
                f"Reviewer-2 REJECTED the draft.\n\n"
                f"Reasons:\n{feedback[:500]}\n\n"
                "Options:\n"
                f"  REVISE   — send back to {target} with reviewer feedback (default)\n"
                "  OVERRIDE — force-approve this draft and end the run\n"
                f"  Or type your own revision instructions{'' if to_orchestrator else ' for the Journalist'}:"
            ),
            "default": "REVISE",
        })
        answer = (answer or "REVISE").strip()
        if answer.upper() == "OVERRIDE":
            return {
                "reviewer_approved": True,
                "final_status": "overridden",
                "messages": [AIMessage(content="[Reviewer-2]: OVERRIDE by user — force-approved.")],
            }
        if answer and answer.upper() != "REVISE":
            return {
                "next_instructions": answer,
                "reviewer_feedback": answer,
                "messages": [AIMessage(content="[HITL on_rejection]: custom revision instructions")],
            }
        return {"messages": [AIMessage(content="[HITL on_rejection]: REVISE")]}

    return on_rejection_node


# ── Specialist node factory ────────────────────────────────────────────────

def make_specialist_node(persona_name: str, config: dict, model, tools: list, summary_model=None,
                         limiter=None):
    """Return an async LangGraph node function for a specialist persona.

    The node uses _run_tool_loop_async so that multiple tool calls issued in a
    single model response (e.g., search_pubmed + search_semantic_scholar) are
    dispatched concurrently via ToolNode.ainvoke / asyncio.gather. Synchronous
    tool functions are transparently run in a thread-pool executor by LangChain.

    ``summary_model`` is the same model without tools; it writes the final answer
    if the tool-round budget runs out (defaults to ``model``). ``limiter`` (a
    _LoopLocalSemaphore) bounds how many specialists run at once.
    """
    persona_cfg = get_persona_config(config, persona_name)
    system_prompt = build_agent_system_prompt(
        persona_cfg,
        epistemic_tags=config.get("epistemic_tags"),
        tool_names=[getattr(t, "name", "") for t in tools],
    )
    max_rounds = config["orchestrator"].get("max_tool_rounds_per_agent", 5)
    max_peer_chars = config["orchestrator"].get("max_peer_chars", MAX_PEER_CHARS)
    failure_policy = _failure_policy(config)
    summary_model = summary_model or model

    async def specialist_node(state: GraphState, config: RunnableConfig = None):
        agent_instructions = (
            state.get("agent_assignments", {}).get(persona_name)
            or state.get("next_instructions")
            or "Provide your expert analysis."
        )
        peer_findings = _format_findings(
            state.get("agent_outputs", {}),
            compress=True,
            exclude=persona_name,
            max_chars=max_peer_chars,
        )
        context = HumanMessage(content=(
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"YOUR SPECIFIC INSTRUCTIONS:\n{agent_instructions}\n\n"
            f"RELEVANT FINDINGS FROM COLLEAGUES:\n{peer_findings}"
        ))
        try:
            async with limiter.get() if limiter else contextlib.nullcontext():
                response, usage = await _run_tool_loop_async(
                    model, tools,
                    [SystemMessage(content=system_prompt), context],
                    max_rounds,
                    summary_model=summary_model,
                    agent_name=persona_name,
                    config=config,
                )
            final_text = _message_text(response)
            brief = final_text[:150] + "…" if len(final_text) > 150 else final_text
            status = f"[{persona_name}]: {brief}"
        except GraphBubbleUp:
            raise
        except Exception as e:
            if failure_policy == "abort":
                raise
            final_text = _failure_note(persona_name, e)
            status = final_text
            usage = {}

        previous = state.get("agent_outputs", {}).get(persona_name, "")
        return {
            "messages": [AIMessage(content=status)],
            "agent_outputs": {persona_name: _combine_findings(persona_name, previous, final_text)},
            "token_usage": usage,
        }

    return specialist_node


# ── Journalist node ────────────────────────────────────────────────────────

def make_journalist_node(config: dict, model, tools: list, summary_model=None):
    """Journalist receives full findings and writes the structured final output.

    Uses _run_tool_loop_async so that write_section + git_snapshot tool calls
    can run concurrently if the model issues them together.
    """
    journalist_name = config["orchestrator"]["journalist"]
    journalist_cfg = get_persona_config(config, journalist_name)
    system_prompt = build_agent_system_prompt(journalist_cfg)
    max_rounds = config["orchestrator"].get("max_tool_rounds_per_agent", 5)
    failure_policy = _failure_policy(config)
    reviewer_enabled = config["reviewer"].get("enabled", True)
    summary_model = summary_model or model
    # Rendered once at build time: a broken template fails before any API spend.
    brief = build_journalist_brief(config, [getattr(t, "name", "") for t in tools])

    async def journalist_node(state: GraphState, config: RunnableConfig = None):
        agent_outputs = state.get("agent_outputs", {})
        sections = [
            f"RESEARCH TASK:\n{state['task']}",
            f"ALL SPECIALIST FINDINGS:\n{_format_findings(agent_outputs, exclude=journalist_name)}",
        ]
        if state.get("journalist_instructions"):
            sections.append(f"INSTRUCTIONS FROM THE ORCHESTRATOR:\n{state['journalist_instructions']}")
        if state.get("user_framing"):
            sections.append(f"FRAMING FROM THE USER (apply to every draft):\n{state['user_framing']}")
        if state.get("revision_count", 0) > 0:
            sections.append(f"PREVIOUS DRAFT (rejected):\n{agent_outputs.get(journalist_name, '')}")
            sections.append(
                f"REVISION INSTRUCTIONS FROM REVIEWER-2:\n"
                f"{state.get('reviewer_feedback') or state.get('next_instructions', '')}\n"
                "Address all reviewer feedback in this revised version."
            )
        sections.append(brief)
        context = HumanMessage(content="\n\n".join(sections))

        try:
            response, usage = await _run_tool_loop_async(
                model, tools,
                [SystemMessage(content=system_prompt), context],
                max_rounds,
                summary_model=summary_model,
                agent_name=journalist_name,
                config=config,
            )
            final_text = _message_text(response)
            status = f"[Journalist draft — {len(final_text)} chars]"
        except GraphBubbleUp:
            raise
        except Exception as e:
            if failure_policy == "abort":
                raise
            final_text, usage = "", {}
            status = _failure_note("Journalist", e)

        update = {
            "messages": [AIMessage(content=status)],
            "agent_outputs": {journalist_name: final_text},
            "token_usage": usage,
        }
        if not reviewer_enabled:
            update["final_status"] = "completed" if final_text.strip() else "failed"
        return update

    return journalist_node


# ── Reviewer node ──────────────────────────────────────────────────────────

def make_reviewer_node(config: dict, reviewer_model, hitl_cfg: dict = None, specialist_names: list = None):
    """Adversarial reviewer.

    An empty draft is rejected without an LLM call. After max_revision_loops
    rejections the next draft is force-approved (reviewer.on_max_revisions:
    flag, the default) or reviewed once more and the run fails if it is still
    rejected (fail); an empty draft at that point always fails the run. The
    on_rejection checkpoint is a separate node; ``hitl_cfg`` is accepted for
    backwards compatibility.

    With reviewer.on_rejection orchestrator or auto, the verdict is structured
    (ReviewerVerdict) so that missing evidence can be sent back to the
    orchestrator; an unparseable reply falls back to the APPROVED / REJECTED:
    text protocol. The default (journalist) keeps the text protocol only.
    """
    reviewer_cfg = config["reviewer"]
    reviewer_prompt = build_reviewer_prompt(config)
    max_revisions = reviewer_cfg.get("max_revision_loops", 3)
    journalist_name = config["orchestrator"]["journalist"]
    on_rejection = reviewer_cfg.get("on_rejection", "journalist")
    max_evidence_loops = reviewer_cfg.get("max_evidence_loops", 1)
    fail_at_max = reviewer_cfg.get("on_max_revisions", "flag") == "fail"
    context = list(reviewer_cfg.get("context") or ["draft"])
    excerpt_rows = reviewer_cfg.get("matrix_excerpt_rows", 50)
    max_agent_calls = config["orchestrator"].get("max_agent_calls", 8)
    specialists = list(specialist_names or [])
    structured_model = None
    if on_rejection != "journalist":
        structured_model = reviewer_model.with_structured_output(ReviewerVerdict, include_raw=True)
        structured_prompt = (
            f"{reviewer_prompt}\n\n"
            "Return your verdict as structured output: `verdict` is APPROVED or REJECTED; "
            "`style_issues` lists problems the writer can fix alone; `missing_evidence` lists claims "
            "that need more evidence from the research team, each with `claim`, `reason` and, if one "
            f"fits, `suggested_agent` (one of: {', '.join(specialists) or 'none'})."
        )

    def review_input(state: GraphState, draft: str) -> str:
        if [c for c in context if c != "draft"] == []:
            return draft                     # default: the draft only, as before
        sections = []
        if "task" in context:
            sections.append(f"RESEARCH TASK:\n{state.get('task', '')}")
        sections.append(f"DRAFT TO REVIEW:\n{draft}")
        if "matrix" in context:
            sections.append(
                f"TRACEABILITY MATRIX (up to {excerpt_rows} rows logged in this run). Check that the "
                f"draft's cited claims are recorded here:\n{_matrix_excerpt(config, excerpt_rows)}"
            )
        return "\n\n".join(sections)

    def judge(state: GraphState, draft: str) -> tuple:
        """Return (approved, feedback_text, evidence_requests, token_usage)."""
        human = HumanMessage(content=review_input(state, draft))
        usage: dict = {}
        if structured_model is not None:
            parsed = None
            try:
                result = structured_model.invoke([SystemMessage(content=structured_prompt), human])
                if isinstance(result, dict) and "parsed" in result:
                    usage = _extract_token_usage(result.get("raw"))
                    parsed = result["parsed"] if result.get("parsing_error") is None else None
                    raw_text = _message_text(result.get("raw")).strip() if result.get("raw") is not None else ""
                else:
                    parsed, raw_text = result, ""
            except GraphBubbleUp:
                raise
            except Exception:
                parsed, raw_text = None, ""
            if isinstance(parsed, ReviewerVerdict):
                return (parsed.verdict == "APPROVED", _verdict_feedback(parsed),
                        _evidence_requests(parsed, specialists), usage)
            # A reply that ignored the schema but follows the text protocol needs no second call.
            if _APPROVED_RE.match(raw_text) or raw_text.startswith("REJECTED"):
                return bool(_APPROVED_RE.match(raw_text)), raw_text, [], usage
        # Text protocol: today's behaviour, and the fallback for an unparseable structured reply.
        response = reviewer_model.invoke([SystemMessage(content=reviewer_prompt), human])
        usage = _merge_token_usage(usage, _extract_token_usage(response))
        verdict = _message_text(response).strip()
        return bool(_APPROVED_RE.match(verdict)), verdict, [], usage

    def reviewer_node(state: GraphState):
        revision_count = state.get("revision_count", 0)
        draft = state.get("agent_outputs", {}).get(journalist_name, "")
        final_review = False

        if revision_count >= max_revisions:
            if not draft.strip():
                return {
                    "reviewer_approved": False,
                    "final_status": "failed",
                    "messages": [AIMessage(content=(
                        f"[Reviewer-2]: no draft text after {revision_count} revision loop(s) — run failed."
                    ))],
                    "token_usage": {},
                }
            if not fail_at_max:
                return {
                    "reviewer_approved": True,
                    "final_status": "force_approved",
                    "messages": [AIMessage(content=(
                        f"[Reviewer-2 safety valve: {max_revisions} revision loops reached. "
                        "Force-approved.]"
                    ))],
                    "token_usage": {},
                }
            final_review = True              # on_max_revisions: fail — read the last draft once more

        if not draft.strip():
            return {
                "reviewer_approved": False,
                "revision_count": revision_count + 1,
                "next_instructions": EMPTY_DRAFT_FEEDBACK,
                "reviewer_feedback": EMPTY_DRAFT_FEEDBACK,
                "review_route": "journalist",
                "messages": [AIMessage(content="[Reviewer-2 REJECTED]: empty draft")],
                "token_usage": {},
            }

        approved, verdict, requests, usage = judge(state, draft)

        if approved:
            return {
                "reviewer_approved": True,
                "final_status": "approved",
                "messages": [AIMessage(content="[Reviewer-2]: APPROVED")],
                "token_usage": usage,
            }

        if final_review:
            return {
                "reviewer_approved": False,
                "final_status": "failed",
                "reviewer_feedback": verdict,
                "messages": [AIMessage(content=(
                    f"[Reviewer-2 REJECTED the final draft after {max_revisions} revision loops — "
                    f"run failed (reviewer.on_max_revisions: fail)]: {verdict[:200]}…"
                ))],
                "token_usage": usage,
            }

        evidence_loops = state.get("evidence_loop_count", 0)
        wants_evidence = on_rejection == "orchestrator" or (on_rejection == "auto" and bool(requests))
        # With the specialist budget spent the orchestrator could only hand straight back.
        wants_evidence = wants_evidence and state.get("agent_call_count", 0) < max_agent_calls
        route = "orchestrator" if wants_evidence and evidence_loops < max_evidence_loops else "journalist"
        update = {
            "reviewer_approved": False,
            "revision_count": revision_count + 1,
            "next_instructions": verdict,
            "reviewer_feedback": verdict,
            "reviewer_requests": requests,
            "review_route": route,
            "messages": [AIMessage(content=f"[Reviewer-2 REJECTED]: {verdict[:200]}…")],
            "token_usage": usage,
        }
        if route == "orchestrator":
            update["evidence_loop_count"] = evidence_loops + 1
            update["messages"] = [AIMessage(content=(
                f"[Reviewer-2 REJECTED → orchestrator for more evidence]: {verdict[:200]}…"
            ))]
        return update

    return reviewer_node


def _verdict_feedback(verdict: ReviewerVerdict) -> str:
    """Render a structured rejection in the text form the Journalist and HITL prompt expect."""
    if verdict.verdict == "APPROVED":
        return "APPROVED"
    lines = ["REJECTED:"]
    lines += [f"- {issue}" for issue in verdict.style_issues]
    lines += [f"- Missing evidence: {req.claim} ({req.reason})" for req in verdict.missing_evidence]
    return "\n".join(lines) if len(lines) > 1 else "REJECTED: no reasons given."


def _evidence_requests(verdict: ReviewerVerdict, specialists: list) -> list:
    """EvidenceRequests as plain dicts; a suggested agent that is not a specialist is dropped."""
    return [
        {"claim": req.claim, "reason": req.reason,
         "suggested_agent": req.suggested_agent if req.suggested_agent in specialists else None}
        for req in verdict.missing_evidence
    ]


def _reviewer_requests_block(state: GraphState, specialist_set: set) -> str:
    """The "REVIEWER REQUESTS" section of the routing prompt during an evidence round."""
    lines = ["REVIEWER REQUESTS (Reviewer-2 rejected the draft and needs more evidence before the",
             "Journalist revises it). Dispatch the specialists who can close these gaps, then route",
             "to ['Journalist']:"]
    for req in state.get("reviewer_requests") or []:
        agent = req.get("suggested_agent")
        hint = f" (suggested: {agent})" if agent in specialist_set else ""
        lines.append(f"  • {req.get('claim', '')} — {req.get('reason', '')}{hint}")
    feedback = state.get("reviewer_feedback", "")
    if feedback:
        lines.append(f"Reviewer feedback:\n{feedback[:1500]}")
    return "\n".join(lines) + "\n\n"


# Only "### Run" starts a run; "### Resumed run" continues one, so its earlier rows still count.
_MATRIX_SECTION_RE = re.compile(r"^### Run\b")


def _matrix_excerpt(config: dict, max_rows: int) -> str:
    """Rows of the traceability matrix logged in the current run, newest last.

    The run starts at the last "### Run" separator; "### Resumed run" separators
    written by --resume are skipped over, so rows logged before a crash are kept.
    """
    path = resolve_swarm_path(config, "traceability_matrix")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "(the traceability matrix could not be read)"
    start = max((i for i, line in enumerate(lines) if _MATRIX_SECTION_RE.match(line)), default=-1)
    rows = [line for line in lines[start + 1:]
            if line.startswith("|") and not line.startswith(("| Source |", "|--"))]
    return "\n".join(rows[-max_rows:]) if rows else "(no rows logged in this run)"


# ── Graph builder ──────────────────────────────────────────────────────────

_NO_CHECKPOINTER = object()


def build_graph(config: dict = None, checkpointer=_NO_CHECKPOINTER):
    """Build and compile the multi-agent supervisor LangGraph.

    When hitl.enabled = true in swarm_config.yml, each configured checkpoint
    becomes its own node so that interrupt() can pause execution and resume
    after the user responds. ``checkpointer`` is the saver to compile with
    (None for none); when omitted, a MemorySaver is used if HITL is on or
    ``checkpointer.type`` is memory, and no checkpointer otherwise.

    All LLM model instances are created once here and closed over in node
    closures, eliminating repeated client instantiation during execution.
    """
    if config is None:
        config = load_config()

    reviewer_enabled = config["reviewer"].get("enabled", True)
    orchestrator_name = config["orchestrator"]["agent"]
    journalist_name = config["orchestrator"]["journalist"]
    hitl_cfg = get_hitl_config(config)
    hitl_on = hitl_cfg.get("enabled", False)
    checkpoints = set(hitl_cfg.get("checkpoints", [])) if hitl_on else set()
    post_plan_on = "post_plan" in checkpoints
    pre_journalist_on = "pre_journalist" in checkpoints
    on_rejection_on = reviewer_enabled and "on_rejection" in checkpoints

    specialist_names = [
        p["name"] for p in config["personas"]
        if p["name"] not in (orchestrator_name, journalist_name)
    ]

    # ── Build models once ────────────────────────────────────────────────
    base_model = create_model(config)
    reviewer_model_inst = create_reviewer_model(config)
    persona_models: dict = {}

    def model_for(persona_name: str):
        """personas[].model merged over model (like reviewer.model); one client per distinct spec."""
        merged = persona_model_config(config, get_persona_config(config, persona_name))
        if merged is None:
            return base_model
        key = repr(sorted(merged.items()))
        if key not in persona_models:
            persona_models[key] = create_model({**config, "model": merged})
        return persona_models[key]

    orchestrator_model = model_for(orchestrator_name)
    # include_raw=True keeps the raw AIMessage so the orchestrator's token usage
    # can be counted; _unpack_routing_result() handles both result shapes.
    orchestrator_routing_model = orchestrator_model.with_structured_output(OrchestratorDecision, include_raw=True)

    workflow = StateGraph(GraphState)

    # ── Register orchestrator ────────────────────────────────────────────
    # The orchestrator persona's tools are only loaded with orchestrator.allow_tools: true;
    # by default the orchestrator only routes (audit B11).
    orchestrator_tools, orchestrator_tool_model = [], None
    if config["orchestrator"].get("allow_tools", False) is True:
        orchestrator_tools = load_tools_for_persona(config, get_persona_config(config, orchestrator_name))
        if orchestrator_tools:
            orchestrator_tool_model = orchestrator_model.bind_tools(orchestrator_tools)
    workflow.add_node(
        "orchestrator",
        make_orchestrator_node(
            config, specialist_names, orchestrator_routing_model,
            tool_model=orchestrator_tool_model, tools=orchestrator_tools, summary_model=orchestrator_model,
        ),
    )

    # ── Register specialists (each with pre-bound tool set) ──────────────
    # orchestrator.max_parallel_agents caps how many specialists run their model/tool
    # loop at once when the orchestrator fans out; the others wait their turn.
    max_parallel = config["orchestrator"].get("max_parallel_agents")
    limiter = _LoopLocalSemaphore(max_parallel) if max_parallel else None
    for name in specialist_names:
        persona_cfg = get_persona_config(config, name)
        tools = load_tools_for_persona(config, persona_cfg)
        own_model = model_for(name)
        specialist_model = own_model.bind_tools(tools) if tools else own_model
        workflow.add_node(
            name, make_specialist_node(name, config, specialist_model, tools, own_model, limiter=limiter),
        )

    # ── Register journalist ──────────────────────────────────────────────
    journalist_cfg = get_persona_config(config, journalist_name)
    journalist_tools = load_tools_for_persona(config, journalist_cfg)
    journalist_own_model = model_for(journalist_name)
    journalist_model = (
        journalist_own_model.bind_tools(journalist_tools) if journalist_tools else journalist_own_model
    )
    workflow.add_node(
        "Journalist", make_journalist_node(config, journalist_model, journalist_tools, journalist_own_model),
    )

    # ── Register reviewer and HITL checkpoint nodes ──────────────────────
    if reviewer_enabled:
        workflow.add_node("reviewer", make_reviewer_node(config, reviewer_model_inst,
                                                         specialist_names=specialist_names))
    if post_plan_on:
        workflow.add_node("hitl_post_plan", make_post_plan_node(config, specialist_names))
    if pre_journalist_on:
        workflow.add_node("hitl_pre_journalist", make_pre_journalist_node())
    if on_rejection_on:
        workflow.add_node("hitl_on_rejection", make_on_rejection_node())

    # ── Wire edges ──────────────────────────────────────────────────────
    workflow.add_edge(START, "orchestrator")

    for name in specialist_names:
        workflow.add_edge(name, "orchestrator")

    specialist_set = set(specialist_names)
    to_journalist = "hitl_pre_journalist" if pre_journalist_on else "Journalist"

    def dispatch_router(state: GraphState):
        targets = state.get("next_agents", [])
        specialist_targets = [t for t in targets if t in specialist_set]
        if len(specialist_targets) > 1:
            return [Send(name, state) for name in specialist_targets]
        elif len(specialist_targets) == 1:
            return specialist_targets[0]
        # Anything else (including an empty or unknown target list) means the
        # evidence phase is over: the Journalist is the only way to finish a run.
        # pre_journalist is asked once; a reviewer evidence round goes straight back.
        if state.get("revision_count", 0) > 0:
            return "Journalist"
        return to_journalist

    def orchestrator_router(state: GraphState):
        if post_plan_on and not state.get("plan_reviewed", False):
            return "hitl_post_plan"
        return dispatch_router(state)

    dispatch_map = {name: name for name in specialist_names}
    dispatch_map[to_journalist] = to_journalist
    dispatch_map["Journalist"] = "Journalist"
    orchestrator_map = dict(dispatch_map)
    if post_plan_on:
        orchestrator_map["hitl_post_plan"] = "hitl_post_plan"
        workflow.add_conditional_edges("hitl_post_plan", dispatch_router, dispatch_map)
    workflow.add_conditional_edges("orchestrator", orchestrator_router, orchestrator_map)
    if pre_journalist_on:
        workflow.add_edge("hitl_pre_journalist", "Journalist")

    if reviewer_enabled:
        workflow.add_edge("Journalist", "reviewer")
        # reviewer.on_rejection orchestrator | auto can send a rejection back to the
        # orchestrator for more evidence; the reviewer node decides (review_route).
        evidence_route = config["reviewer"].get("on_rejection", "journalist") != "journalist"

        def revise_target(state: GraphState) -> str:
            return "orchestrator" if evidence_route and state.get("review_route") == "orchestrator" else "Journalist"

        def reviewer_router(state: GraphState) -> str:
            if state.get("reviewer_approved", False) or state.get("final_status") == "failed":
                return END
            return "hitl_on_rejection" if on_rejection_on else revise_target(state)

        revise_map = {"Journalist": "Journalist", **({"orchestrator": "orchestrator"} if evidence_route else {})}
        reviewer_map = {END: END, **({"hitl_on_rejection": "hitl_on_rejection"} if on_rejection_on else revise_map)}
        workflow.add_conditional_edges("reviewer", reviewer_router, reviewer_map)

        if on_rejection_on:
            def rejection_router(state: GraphState) -> str:
                return END if state.get("reviewer_approved", False) else revise_target(state)

            workflow.add_conditional_edges("hitl_on_rejection", rejection_router, {END: END, **revise_map})
    else:
        workflow.add_edge("Journalist", END)

    # ── Compile with optional checkpointer for HITL interrupt/resume ─────
    # main.py opens the configured checkpointer (see config.open_checkpointer) and
    # passes it in; None means no checkpointer. Without the argument the previous
    # behaviour applies: a MemorySaver when HITL is on, nothing otherwise.
    if checkpointer is _NO_CHECKPOINTER:
        if get_checkpointer_config(config)["type"] == "sqlite":
            raise ValueError(
                "checkpointer.type: sqlite must be opened inside the event loop; "
                "pass build_graph(config, checkpointer=...) from config.open_checkpointer()."
            )
        checkpointer = None
        if hitl_on or get_checkpointer_config(config)["type"] == "memory":
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()
