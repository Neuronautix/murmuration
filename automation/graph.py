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

Token strategy (per call)
--------------------------
  Orchestrator  : its persona prompt + compressed findings summary
  Specialist    : its persona prompt + task + per-agent orchestrator instructions
                  + peer findings capped at MAX_PEER_CHARS chars each
  Journalist    : its persona prompt + task + full uncompressed findings
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

Human-in-the-Loop (HITL)
-------------------------
  When hitl.enabled = true in swarm_config.yml, the graph is compiled with a
  MemorySaver checkpointer and interrupt() is called at configured checkpoints:

    post_plan      — after the orchestrator's first routing decision
    pre_journalist — before the Journalist node (final framing chance)
    on_rejection   — when Reviewer-2 rejects (REVISE / OVERRIDE / custom)

  The caller (main.py) detects the __interrupt__ event in the stream, prompts
  the user, and resumes with Command(resume=answer).
"""

import asyncio
from typing import Annotated, TypedDict

from pydantic import BaseModel
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt, Command  # noqa: F401 (Command re-exported for main.py)
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import ToolNode

from automation.config import (
    load_config,
    create_model,
    create_reviewer_model,
    build_reviewer_prompt,
    get_persona_config,
    load_tools_for_persona,
    build_agent_system_prompt,
    get_hitl_config,
)

# Characters retained from each peer agent's output when shown to another
# specialist. Keeps cross-agent context lean without losing key facts.
MAX_PEER_CHARS = 600


# ── State ──────────────────────────────────────────────────────────────────

def _merge_agent_outputs(existing: dict, update: dict) -> dict:
    """Reducer: merge agent-output dicts; newer values win on key collision."""
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

    task              Original user prompt — never mutated after initialisation.
    messages          Append-only audit log of all agent/reviewer messages.
    agent_outputs     {persona_name: latest findings text} — merged across nodes.
    agent_assignments {persona_name: specific_instructions} — set by orchestrator.
    next_agents       Routing targets set by the orchestrator.
    next_instructions Shared fallback instruction for the next agent batch.
    agent_call_count  Count of specialist invocations only (safety valve).
    reviewer_approved Set True once Reviewer-2 approves the Journalist draft.
    revision_count    Number of Journalist revision cycles completed.
    token_usage       Accumulated token counts across all LLM calls in the run.
    """
    task: str
    messages: Annotated[list, add_messages]
    agent_outputs: Annotated[dict, _merge_agent_outputs]
    agent_assignments: Annotated[dict, _merge_agent_outputs]   # per-agent instructions
    next_agents: list          # list[str]: specialist names | ["Journalist"] | ["END"]
    next_instructions: str     # fallback shared instructions
    agent_call_count: int      # only incremented for specialist dispatches
    reviewer_approved: bool
    revision_count: int
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


# ── Shared helpers ─────────────────────────────────────────────────────────

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
        body = (output[:max_chars] + "…") if compress and len(output) > max_chars else output
        parts.append(f"### [{agent}]\n{body}")
    return "\n\n".join(parts) if parts else "No findings from colleagues yet."


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


def _run_tool_loop(model, tools: list, messages: list, max_rounds: int):
    """Run the agent ↔ tools loop until the model stops calling tools.

    Returns (final_response, accumulated_token_usage).
    """
    tool_executor = ToolNode(tools)
    response = None
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for _ in range(max_rounds):
        response = model.invoke(messages)
        messages = list(messages) + [response]
        round_usage = _extract_token_usage(response)
        for k in usage:
            usage[k] += round_usage.get(k, 0)
        if not (hasattr(response, "tool_calls") and response.tool_calls):
            break
        tool_result = tool_executor.invoke({"messages": messages})
        messages = messages + tool_result["messages"]
    return response, usage


async def _run_tool_loop_async(model, tools: list, messages: list, max_rounds: int):
    """Async version of the agent ↔ tools loop.

    Uses model.ainvoke and ToolNode.ainvoke so that multiple tool calls issued
    in a single model response are executed concurrently (ToolNode.ainvoke
    dispatches all tool_calls via asyncio.gather). Synchronous tool functions
    are run in a thread-pool executor automatically by LangChain's async layer.

    Returns (final_response, accumulated_token_usage).
    """
    tool_executor = ToolNode(tools)
    response = None
    usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for _ in range(max_rounds):
        response = await model.ainvoke(messages)
        messages = list(messages) + [response]
        round_usage = _extract_token_usage(response)
        for k in usage:
            usage[k] += round_usage.get(k, 0)
        if not (hasattr(response, "tool_calls") and response.tool_calls):
            break
        tool_result = await tool_executor.ainvoke({"messages": messages})
        messages = messages + tool_result["messages"]
    return response, usage


# ── Orchestrator node (Dr. Nexus) ──────────────────────────────────────────

def make_orchestrator_node(config: dict, specialist_names: list, routing_model,
                           hitl_cfg: dict):
    """Build the orchestrator node that routes work between specialists.

    HITL checkpoints active here:
      post_plan      — pause after the first routing plan for user approval/redirect
      pre_journalist — pause before routing to Journalist for final framing
    """
    nexus_cfg = get_persona_config(config, config["orchestrator"]["agent"])
    system_prompt = build_agent_system_prompt(nexus_cfg)
    max_calls = config["orchestrator"].get("max_agent_calls", 8)
    valid_targets = set(specialist_names) | {"Journalist", "END"}
    specialist_set = set(specialist_names)

    hitl_on = hitl_cfg.get("enabled", False)
    hitl_checkpoints = set(hitl_cfg.get("checkpoints", []))

    def orchestrator_node(state: GraphState):
        call_count = state.get("agent_call_count", 0)
        already_consulted = list(state.get("agent_outputs", {}).keys())

        if call_count >= max_calls:
            return {
                "next_agents": ["Journalist"],
                "next_instructions": (
                    "Synthesise all collected findings into a final structured report. "
                    "The orchestrator has reached its maximum specialist call count."
                ),
                "agent_assignments": {},
                "messages": [AIMessage(content=(
                    f"[Orchestrator safety valve: {max_calls} specialist calls reached. "
                    "Routing directly to Journalist.]"
                ))],
                "token_usage": {},
            }

        findings = _format_findings(state.get("agent_outputs", {}))
        consulted_note = (
            f"SPECIALISTS ALREADY CONSULTED: {', '.join(already_consulted)}"
            if already_consulted else "No specialists consulted yet."
        )

        routing_prompt = (
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"{consulted_note}\n\n"
            f"FINDINGS COLLECTED SO FAR:\n{findings}\n\n"
            f"ALL AVAILABLE SPECIALISTS: {', '.join(specialist_names)}\n\n"
            "Decide the next step:\n"
            "  • Route to one specialist for sequential deep investigation.\n"
            "  • Route to TWO OR MORE specialists simultaneously for independent sub-questions.\n"
            "    When routing multiple specialists, populate `assignments` with a specific\n"
            "    `instructions` string for EACH agent.\n"
            "  • Route to ['Journalist'] when sufficient evidence is ready to write.\n"
            "  • Route to ['END'] only after the Journalist has already produced output.\n"
            "  • Do NOT re-route to a specialist already in ALREADY CONSULTED unless a\n"
            "    specific new sub-question requires it.\n"
            "Return: reasoning, next_agents (list), instructions (single-agent fallback),\n"
            "assignments (list of {agent, instructions} for parallel dispatch)."
        )

        decision = routing_model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=routing_prompt),
        ])
        round_usage = _extract_token_usage(decision)

        valid_next = [t for t in decision.next_agents if t in valid_targets]
        if not valid_next:
            valid_next = ["Journalist"]

        specialist_dispatches = [t for t in valid_next if t in specialist_set]
        assignments_dict = {
            a.agent: a.instructions
            for a in (decision.assignments or [])
            if a.agent in valid_targets
        }

        # ── HITL: post_plan ───────────────────────────────────────────────
        # Pause on the *first* orchestrator call to let the user approve or
        # redirect before any specialist work begins.
        if hitl_on and "post_plan" in hitl_checkpoints and call_count == 0:
            answer = interrupt({
                "checkpoint": "post_plan",
                "question": (
                    f"Orchestrator's routing plan:\n"
                    f"  Agents selected : {valid_next}\n"
                    f"  Reasoning       : {decision.reasoning}\n\n"
                    "Type APPROVE to proceed, or enter alternative agent names / "
                    "redirect instructions to override:"
                ),
                "default": "APPROVE",
            })
            answer = (answer or "APPROVE").strip()
            if answer.upper() != "APPROVE" and answer:
                # Try to match known agent names in the user's reply
                override_agents = [
                    a for a in list(specialist_set) + ["Journalist", "END"]
                    if a.lower() in answer.lower()
                ]
                if override_agents:
                    valid_next = override_agents
                    specialist_dispatches = [t for t in valid_next if t in specialist_set]
                    assignments_dict = {}

        # ── HITL: pre_journalist ──────────────────────────────────────────
        # Let the user add final framing constraints before the Journalist writes.
        final_instructions = decision.instructions
        if hitl_on and "pre_journalist" in hitl_checkpoints and "Journalist" in valid_next:
            answer = interrupt({
                "checkpoint": "pre_journalist",
                "question": (
                    "The swarm is ready to write the final report.\n\n"
                    "Specialist findings are in. Add final framing instructions\n"
                    "(audience, length, emphasis), or press Enter to continue:"
                ),
                "default": "",
            })
            if answer and answer.strip():
                final_instructions = decision.instructions + "\n\nUSER FRAMING: " + answer.strip()

        return {
            "next_agents": valid_next,
            "next_instructions": final_instructions,
            "agent_assignments": assignments_dict,
            "agent_call_count": call_count + len(specialist_dispatches),
            "messages": [AIMessage(content=(
                f"[Orchestrator → {valid_next}]: {decision.reasoning}"
            ))],
            "token_usage": round_usage,
        }

    return orchestrator_node


# ── Specialist node factory ────────────────────────────────────────────────

def make_specialist_node(persona_name: str, config: dict, model, tools: list):
    """Return an async LangGraph node function for a specialist persona.

    The node uses _run_tool_loop_async so that multiple tool calls issued in a
    single model response (e.g., search_pubmed + search_semantic_scholar) are
    dispatched concurrently via ToolNode.ainvoke / asyncio.gather. Synchronous
    tool functions are transparently run in a thread-pool executor by LangChain.
    """
    persona_cfg = get_persona_config(config, persona_name)
    system_prompt = build_agent_system_prompt(
        persona_cfg,
        epistemic_tags=config.get("epistemic_tags"),
    )
    max_rounds = config["orchestrator"].get("max_tool_rounds_per_agent", 5)

    async def specialist_node(state: GraphState):
        agent_instructions = (
            state.get("agent_assignments", {}).get(persona_name)
            or state.get("next_instructions")
            or "Provide your expert analysis."
        )
        peer_findings = _format_findings(
            state.get("agent_outputs", {}),
            compress=True,
            exclude=persona_name,
        )
        context = HumanMessage(content=(
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"YOUR SPECIFIC INSTRUCTIONS:\n{agent_instructions}\n\n"
            f"RELEVANT FINDINGS FROM COLLEAGUES:\n{peer_findings}"
        ))
        response, usage = await _run_tool_loop_async(
            model, tools,
            [SystemMessage(content=system_prompt), context],
            max_rounds,
        )
        final_text = response.content if hasattr(response, "content") else str(response)
        brief = final_text[:150] + "…" if len(final_text) > 150 else final_text

        return {
            "messages": [AIMessage(content=f"[{persona_name}]: {brief}")],
            "agent_outputs": {persona_name: final_text},
            "token_usage": usage,
        }

    return specialist_node


# ── Journalist node ────────────────────────────────────────────────────────

def make_journalist_node(config: dict, model, tools: list):
    """Journalist receives full findings and writes the structured final output.

    Uses _run_tool_loop_async so that write_section + git_snapshot tool calls
    can run concurrently if the model issues them together.
    """
    journalist_name = config["orchestrator"]["journalist"]
    journalist_cfg = get_persona_config(config, journalist_name)
    system_prompt = build_agent_system_prompt(journalist_cfg)
    max_rounds = config["orchestrator"].get("max_tool_rounds_per_agent", 5)

    async def journalist_node(state: GraphState):
        full_findings = _format_findings(state.get("agent_outputs", {}))
        revision_note = ""
        if state.get("revision_count", 0) > 0:
            revision_note = (
                f"\n\nREVISION INSTRUCTIONS FROM REVIEWER-2:\n"
                f"{state.get('next_instructions', '')}\n"
                "Address all reviewer feedback in this revised version."
            )
        context = HumanMessage(content=(
            f"RESEARCH TASK:\n{state['task']}\n\n"
            f"ALL SPECIALIST FINDINGS:\n{full_findings}"
            f"{revision_note}\n\n"
            "Write a complete structured output. You MUST include:\n"
            "  • Formal in-text citations [1], [2]…\n"
            "  • A section explicitly distinguishing high engagement from clinical impairment\n"
            "  • A limitations section\n"
            "  • A complete formal References section at the end\n"
            "Save the output to disk using write_manuscript_section, "
            "then call git_commit_snapshot."
        ))
        response, usage = await _run_tool_loop_async(
            model, tools,
            [SystemMessage(content=system_prompt), context],
            max_rounds,
        )
        final_text = response.content if hasattr(response, "content") else str(response)
        return {
            "messages": [AIMessage(content=f"[Journalist draft — {len(final_text)} chars]")],
            "agent_outputs": {journalist_name: final_text},
            "token_usage": usage,
        }

    return journalist_node


# ── Reviewer node ──────────────────────────────────────────────────────────

def make_reviewer_node(config: dict, reviewer_model, hitl_cfg: dict):
    """Adversarial reviewer.

    HITL checkpoint active here:
      on_rejection — pause when the draft is rejected so the user can choose
                     REVISE (default) / OVERRIDE / custom revision instructions.
    """
    reviewer_prompt = build_reviewer_prompt(config)
    max_revisions = config["reviewer"].get("max_revision_loops", 3)
    journalist_name = config["orchestrator"]["journalist"]

    hitl_on = hitl_cfg.get("enabled", False)
    hitl_checkpoints = set(hitl_cfg.get("checkpoints", []))

    def reviewer_node(state: GraphState):
        revision_count = state.get("revision_count", 0)

        if revision_count >= max_revisions:
            return {
                "reviewer_approved": True,
                "messages": [AIMessage(content=(
                    f"[Reviewer-2 safety valve: {max_revisions} revision loops reached. "
                    "Force-approved.]"
                ))],
                "token_usage": {},
            }

        draft = state.get("agent_outputs", {}).get(journalist_name, "")
        if not draft:
            return {"reviewer_approved": True, "token_usage": {}}

        response = reviewer_model.invoke([
            SystemMessage(content=reviewer_prompt),
            HumanMessage(content=draft),
        ])
        usage = _extract_token_usage(response)

        if response.content.strip().startswith("APPROVED"):
            return {
                "reviewer_approved": True,
                "messages": [AIMessage(content="[Reviewer-2]: APPROVED")],
                "token_usage": usage,
            }

        # Draft was rejected.
        reviewer_feedback = response.content

        # ── HITL: on_rejection ────────────────────────────────────────────
        if hitl_on and "on_rejection" in hitl_checkpoints:
            answer = interrupt({
                "checkpoint": "on_rejection",
                "question": (
                    f"Reviewer-2 REJECTED the draft.\n\n"
                    f"Reasons:\n{response.content[:500]}\n\n"
                    "Options:\n"
                    "  REVISE   — send back to Journalist with reviewer feedback (default)\n"
                    "  OVERRIDE — force-approve this draft and end the run\n"
                    "  Or type your own revision instructions for the Journalist:"
                ),
                "default": "REVISE",
            })
            answer = (answer or "REVISE").strip()

            if answer.upper() == "OVERRIDE":
                return {
                    "reviewer_approved": True,
                    "messages": [AIMessage(content=(
                        "[Reviewer-2]: OVERRIDE by user — force-approved."
                    ))],
                    "token_usage": usage,
                }
            elif answer.upper() != "REVISE" and answer:
                # User provided custom revision instructions
                reviewer_feedback = answer

        return {
            "reviewer_approved": False,
            "revision_count": revision_count + 1,
            "next_instructions": reviewer_feedback,
            "messages": [AIMessage(content=(
                f"[Reviewer-2 REJECTED]: {reviewer_feedback[:200]}…"
            ))],
            "token_usage": usage,
        }

    return reviewer_node


# ── Graph builder ──────────────────────────────────────────────────────────

def build_graph(config: dict = None):
    """Build and compile the multi-agent supervisor LangGraph.

    When hitl.enabled = true in swarm_config.yml, the graph is compiled with a
    MemorySaver checkpointer so that interrupt() can pause execution and resume
    after the user responds. When HITL is disabled, no checkpointer is used
    and behaviour is identical to the fully autonomous mode.

    All LLM model instances are created once here and closed over in node
    closures, eliminating repeated client instantiation during execution.
    """
    if config is None:
        config = load_config()

    reviewer_enabled = config["reviewer"].get("enabled", True)
    orchestrator_name = config["orchestrator"]["agent"]
    journalist_name = config["orchestrator"]["journalist"]
    hitl_cfg = get_hitl_config(config)

    specialist_names = [
        p["name"] for p in config["personas"]
        if p["name"] not in (orchestrator_name, journalist_name)
    ]

    # ── Build models once ────────────────────────────────────────────────
    base_model = create_model(config)
    reviewer_model_inst = create_reviewer_model(config)
    orchestrator_routing_model = base_model.with_structured_output(OrchestratorDecision)

    workflow = StateGraph(GraphState)

    # ── Register orchestrator ────────────────────────────────────────────
    workflow.add_node(
        "orchestrator",
        make_orchestrator_node(config, specialist_names, orchestrator_routing_model, hitl_cfg),
    )

    # ── Register specialists (each with pre-bound tool set) ──────────────
    for name in specialist_names:
        persona_cfg = get_persona_config(config, name)
        tools = load_tools_for_persona(config, persona_cfg)
        specialist_model = base_model.bind_tools(tools) if tools else base_model
        workflow.add_node(name, make_specialist_node(name, config, specialist_model, tools))

    # ── Register journalist ──────────────────────────────────────────────
    journalist_cfg = get_persona_config(config, journalist_name)
    journalist_tools = load_tools_for_persona(config, journalist_cfg)
    journalist_model = base_model.bind_tools(journalist_tools) if journalist_tools else base_model
    workflow.add_node("Journalist", make_journalist_node(config, journalist_model, journalist_tools))

    # ── Register reviewer ────────────────────────────────────────────────
    if reviewer_enabled:
        workflow.add_node("reviewer", make_reviewer_node(config, reviewer_model_inst, hitl_cfg))

    # ── Wire edges ──────────────────────────────────────────────────────
    workflow.add_edge(START, "orchestrator")

    for name in specialist_names:
        workflow.add_edge(name, "orchestrator")

    specialist_set = set(specialist_names)

    def orchestrator_router(state: GraphState):
        targets = state.get("next_agents", ["END"])
        specialist_targets = [t for t in targets if t in specialist_set]
        if len(specialist_targets) > 1:
            return [Send(name, state) for name in specialist_targets]
        elif len(specialist_targets) == 1:
            return specialist_targets[0]
        elif "Journalist" in targets:
            return "Journalist"
        return "reviewer" if reviewer_enabled else END

    routing_map = {name: name for name in specialist_names}
    routing_map["Journalist"] = "Journalist"
    routing_map["END"] = "reviewer" if reviewer_enabled else END

    workflow.add_conditional_edges("orchestrator", orchestrator_router, routing_map)

    if reviewer_enabled:
        workflow.add_edge("Journalist", "reviewer")

        def reviewer_router(state: GraphState) -> str:
            return END if state.get("reviewer_approved", False) else "Journalist"

        workflow.add_conditional_edges(
            "reviewer",
            reviewer_router,
            {END: END, "Journalist": "Journalist"},
        )
    else:
        workflow.add_edge("Journalist", END)

    # ── Compile with optional checkpointer for HITL interrupt/resume ─────
    if hitl_cfg.get("enabled", False):
        from langgraph.checkpoint.memory import MemorySaver
        return workflow.compile(checkpointer=MemorySaver())

    return workflow.compile()
