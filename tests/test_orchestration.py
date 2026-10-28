"""
Offline tests for the orchestration control flow in automation/graph.py.

Written for docs/dev/ORCHESTRATION_AUDIT.md. Every test runs the real compiled
LangGraph with fake chat models — no API keys, no network.

Two kinds of tests live here. Every finding reproduced here has since been
fixed (quick-win pass, the journalist.brief change, and the B4/B5/R1/R3 pass), so
all of them now run as plain regression tests:

* plain tests document behaviour that is correct today (bounds, merging,
  fallbacks) so a refactor cannot silently change it;
* ``xfail(strict=True)`` tests reproduce defects listed in the audit. They fail
  today for the reason given, and will start *passing* once the defect is fixed,
  at which point pytest reports XPASS(strict) and the marker must be removed.
  Each marker names the audit finding it belongs to.
"""

import asyncio
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from automation.graph import (
    AgentAssignment,
    OrchestratorDecision,
    build_graph,
    make_orchestrator_node,
    make_post_plan_node,
)

# ── Harness ────────────────────────────────────────────────────────────────


def make_config(specialists=("A", "B"), max_agent_calls=8, max_rounds=3, max_rev=3,
                reviewer_enabled=True, hitl=None):
    personas = [{"name": "Orch", "icon": "o", "role": "r", "persona_file": "x", "tools": ["search_web"]}]
    personas += [{"name": s, "icon": "s", "role": "r", "persona_file": "x", "tools": []} for s in specialists]
    personas.append({"name": "Journalist", "icon": "j", "role": "r", "persona_file": "x", "tools": []})
    return {
        "swarm": {"name": "T", "description": "", "output_dir": "./Drafts", "traceability_matrix": "./K.md"},
        "orchestrator": {"agent": "Orch", "journalist": "Journalist",
                         "max_agent_calls": max_agent_calls, "max_tool_rounds_per_agent": max_rounds},
        "model": {"provider": "openai", "name": "m", "temperature": 0, "env_key": "K"},
        "personas": personas,
        "tools": {"search_web": {"module": "automation.tools", "function": "search_you_engine"}},
        "reviewer": {"enabled": reviewer_enabled, "max_revision_loops": max_rev, "banned_words": [],
                     "required_elements": [], "rejection_patterns": [], "tone": "neutral"},
        "hitl": hitl or {"enabled": False, "checkpoints": []},
        "epistemic_tags": ["[FACT]"],
    }


def decision(agents, instructions="do it", assignments=None):
    return OrchestratorDecision(
        reasoning="because", next_agents=agents, instructions=instructions,
        assignments=[AgentAssignment(agent=a, instructions=i) for a, i in (assignments or {}).items()],
    )


USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class ScriptedRouter:
    """Stands in for ``base_model.with_structured_output(OrchestratorDecision)``.

    Replays ``decisions`` in order and repeats the last one. An Exception in the
    script is raised instead of returned. With ``include_raw`` it mimics the
    LangChain ``{"raw": AIMessage, "parsed": model}`` shape.
    """

    def __init__(self, decisions, include_raw=False):
        self.decisions = list(decisions)
        self.include_raw = include_raw
        self.calls = []

    def invoke(self, msgs):
        self.calls.append(msgs)
        d = self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]
        if isinstance(d, Exception):
            raise d
        if self.include_raw:
            return {"raw": AIMessage(content="", usage_metadata=USAGE), "parsed": d, "parsing_error": None}
        return d


class _BoundFakeChat:
    """What ``FakeChat.bind_tools()`` returns: the same fake, with tools bound."""

    def __init__(self, parent):
        self.parent = parent

    async def ainvoke(self, msgs, **kwargs):
        return await self.parent.ainvoke(msgs, _bound=True)


class FakeChat:
    """Stands in for the base chat model shared by specialists and the Journalist.

    Identifies the calling persona from its system prompt. ``fail_for`` names
    personas whose LLM call raises; ``empty_for`` names personas that reply with
    empty text. ``tool_calls`` makes every reply *from a tool-bound model*
    request ``dummy_tool`` (so the tool loop never terminates on its own); like a
    real model, the unbound base model never emits tool calls.
    """

    def __init__(self, decisions, fail_for=(), tool_calls=False, empty_for=()):
        self.decisions = decisions
        self.router = None
        self.calls = []  # (persona_name, messages)
        self.bound_flags = []  # parallel to calls: True when the call came through bind_tools()
        self.fail_for = set(fail_for)
        self.empty_for = set(empty_for)
        self.tool_calls = tool_calls
        self.n = 0

    def with_structured_output(self, schema, **kwargs):
        self.router = ScriptedRouter(self.decisions, include_raw=kwargs.get("include_raw", False))
        return self.router

    def bind_tools(self, tools, **kwargs):
        return _BoundFakeChat(self)

    @staticmethod
    def persona(msgs):
        sys_msg = next(m for m in msgs if isinstance(m, SystemMessage))
        return sys_msg.content.split("You are ")[1].split("\n")[0].strip()

    def human(self, name, index=0):
        msgs = [m for n, m in self.calls if n == name][index]
        return next(m for m in msgs if isinstance(m, HumanMessage)).content

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)

    async def ainvoke(self, msgs, _bound=False, **kwargs):
        name = self.persona(msgs)
        self.calls.append((name, msgs))
        self.bound_flags.append(_bound)
        self.n += 1
        if name in self.fail_for:
            raise RuntimeError(f"simulated API failure in {name}")
        if name in self.empty_for:
            return AIMessage(content="", usage_metadata=USAGE)
        if self.tool_calls and _bound:
            return AIMessage(content="", usage_metadata=USAGE,
                             tool_calls=[{"name": "dummy_tool", "args": {"x": "1"}, "id": f"c{self.n}"}])
        return AIMessage(content=f"findings from {name} #{self.n}", usage_metadata=USAGE)


class FakeReviewer:
    def __init__(self, replies=("APPROVED",)):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, msgs):
        self.calls.append(msgs)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return AIMessage(content=reply, usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


@tool
def dummy_tool(x: str) -> str:
    """Test tool that always succeeds."""
    return "tool result"


@tool
def exploding_tool(x: str) -> str:
    """Test tool that always raises."""
    raise RuntimeError("tool exploded")


def initial_state(task="task"):
    return {"task": task, "messages": [], "agent_outputs": {}, "agent_assignments": {}, "next_agents": [],
            "next_instructions": "", "agent_call_count": 0, "reviewer_approved": False, "revision_count": 0,
            "token_usage": {}}


def build(config, chat, reviewer=None, tools_for=None):
    """Compile the real graph with fake models. ``tools_for`` maps persona name → tool list."""
    reviewer = reviewer or FakeReviewer()
    tools_for = tools_for or {}
    with patch("automation.graph.create_model", return_value=chat), \
         patch("automation.graph.create_reviewer_model", return_value=reviewer), \
         patch("automation.config.load_persona_content", return_value="persona"), \
         patch("automation.graph.load_tools_for_persona", side_effect=lambda c, p: tools_for.get(p["name"], [])):
        return build_graph(config)


def run(graph, state=None, config=None):
    return asyncio.run(graph.ainvoke(state or initial_state(), config=config or {}))


def resume(graph, answer, config):
    return asyncio.run(graph.ainvoke(Command(resume=answer), config=config))


def node_sequence(graph, state=None, config=None):
    """Ordered node names that emitted updates during one run."""
    async def _go():
        seq = []
        async for ev in graph.astream(state or initial_state(), config=config or {}, stream_mode="updates"):
            seq.extend(ev.keys())
        return seq
    return asyncio.run(_go())


# ── 1. Control flow ────────────────────────────────────────────────────────


def test_specialist_loop_is_bounded_by_max_agent_calls():
    """An orchestrator that always picks the same specialist is cut off by max_agent_calls."""
    chat = FakeChat([decision(["A"])])
    graph = build(make_config(max_agent_calls=3), chat)
    state = run(graph)
    assert chat.count("A") == 3
    assert chat.count("Journalist") == 1
    assert state["agent_call_count"] == 3
    assert any("safety valve" in m.content for m in state["messages"])


def test_parallel_dispatch_counts_every_specialist_towards_the_bound():
    chat = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"})])
    graph = build(make_config(max_agent_calls=2), chat)
    state = run(graph)
    assert chat.count("A") == 1 and chat.count("B") == 1
    assert state["agent_call_count"] == 2


def test_unknown_agent_name_falls_back_to_journalist():
    """A hallucinated agent name is dropped and the run goes straight to the Journalist."""
    chat = FakeChat([decision(["Ghost"])])
    graph = build(make_config(), chat)
    assert node_sequence(graph) == ["orchestrator", "Journalist", "reviewer"]
    state = run(build(make_config(), FakeChat([decision(["Ghost"])])))
    assert "ignored unknown targets ['Ghost']" in state["messages"][0].content


def test_empty_next_agents_falls_back_to_journalist():
    chat = FakeChat([decision([])])
    graph = build(make_config(), chat)
    state = run(graph)
    assert state["agent_call_count"] == 0
    assert list(state["agent_outputs"]) == ["Journalist"]


def test_specialist_and_journalist_in_one_decision_runs_specialist_only():
    chat = FakeChat([decision(["A", "Journalist"]), decision(["Journalist"])])
    graph = build(make_config(), chat)
    assert node_sequence(graph) == ["orchestrator", "A", "orchestrator", "Journalist", "reviewer"]


def test_end_route_with_reviewer_enabled_does_not_crash():
    """AUDIT B1: an END decision used to raise KeyError inside the conditional edge."""
    chat = FakeChat([decision(["END"])])
    graph = build(make_config(reviewer_enabled=True), chat)
    assert node_sequence(graph) == ["orchestrator", "Journalist", "reviewer"]


def test_end_route_with_reviewer_disabled_does_not_crash():
    chat = FakeChat([decision(["END"])])
    graph = build(make_config(reviewer_enabled=False), chat)
    assert node_sequence(graph) == ["orchestrator", "Journalist"]


def test_end_route_still_runs_journalist():
    chat = FakeChat([decision(["END"])])
    graph = build(make_config(), chat)
    state = run(graph)
    assert "Journalist" in state["agent_outputs"]


# ── 2. State merging ───────────────────────────────────────────────────────


def test_parallel_branches_merge_outputs_and_token_usage_once():
    chat = FakeChat([decision(["A", "B", "C"], assignments={"A": "a", "B": "b", "C": "c"}), decision(["Journalist"])])
    graph = build(make_config(specialists=("A", "B", "C")), chat)
    state = run(graph)
    assert sorted(state["agent_outputs"]) == ["A", "B", "C", "Journalist"]
    llm_calls = len(chat.calls) + len(chat.router.calls)  # 3 specialists + journalist + 2 routing calls
    assert state["token_usage"]["total_tokens"] == USAGE["total_tokens"] * llm_calls + 2  # +2 reviewer


def test_parallel_siblings_see_only_findings_collected_before_dispatch():
    chat = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])])
    graph = build(make_config(), chat)
    run(graph)
    assert "No findings collected yet." in chat.human("A")
    assert "No findings collected yet." in chat.human("B")


def test_per_agent_assignment_overrides_shared_instructions():
    chat = FakeChat([decision(["A", "B"], "shared", assignments={"A": "only-A", "B": "only-B"}),
                     decision(["Journalist"])])
    graph = build(make_config(), chat)
    run(graph)
    assert "only-A" in chat.human("A") and "shared" not in chat.human("A")
    assert "only-B" in chat.human("B")


def test_stale_assignment_is_not_reused_on_a_later_dispatch():
    chat = FakeChat([decision(["A", "B"], assignments={"A": "OLD-A", "B": "OLD-B"}),
                     decision(["A"], instructions="NEW-SHARED"),
                     decision(["Journalist"])])
    graph = build(make_config(), chat)
    run(graph)
    second = chat.human("A", index=1)
    assert "NEW-SHARED" in second and "OLD-A" not in second


def test_same_specialist_twice_keeps_both_rounds_of_findings():
    chat = FakeChat([decision(["A"], "round1"), decision(["A"], "round2"), decision(["Journalist"])])
    graph = build(make_config(), chat)
    state = run(graph)
    assert chat.count("A") == 2
    assert "findings from A #1" in state["agent_outputs"]["A"]
    assert "findings from A #2" in state["agent_outputs"]["A"]


def test_revision_count_is_incremented_per_rejection_and_never_reset():
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: a", "REJECTED: b", "APPROVED"])
    graph = build(make_config(), chat, reviewer)
    state = run(graph)
    assert state["revision_count"] == 2
    assert chat.count("Journalist") == 3


# ── 3. Reviewer feedback ───────────────────────────────────────────────────


def test_rejection_routes_back_to_journalist_only():
    chat = FakeChat([decision(["A"]), decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: missing citation", "APPROVED"])
    graph = build(make_config(), chat, reviewer)
    seq = node_sequence(graph)
    assert seq == ["orchestrator", "A", "orchestrator", "Journalist", "reviewer", "Journalist", "reviewer"]
    assert chat.count("A") == 1  # no evidence loop exists today (see audit I1)
    assert "REVISION INSTRUCTIONS FROM REVIEWER-2" in chat.human("Journalist", index=1)
    assert "missing citation" in chat.human("Journalist", index=1)


def test_revision_loop_is_bounded_and_force_approves():
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: never good enough"])
    graph = build(make_config(max_rev=2), chat, reviewer)
    state = run(graph)
    assert state["reviewer_approved"] is True
    assert chat.count("Journalist") == 3        # initial + 2 revisions
    assert len(reviewer.calls) == 2             # the 3rd draft is force-approved unread (audit R2)
    assert "safety valve" in state["messages"][-1].content


def test_reviewer_accepts_markdown_wrapped_approved():
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["**APPROVED**"])
    graph = build(make_config(), chat, reviewer)
    state = run(graph)
    assert state["revision_count"] == 0
    assert chat.count("Journalist") == 1


def test_journalist_revision_prompt_labels_the_previous_draft():
    """AUDIT R11: the previous draft is shown as PREVIOUS DRAFT, not as a specialist finding."""
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: fix", "APPROVED"])
    graph = build(make_config(), chat, reviewer)
    run(graph)
    second = chat.human("Journalist", index=1)
    assert "PREVIOUS DRAFT" in second and "findings from Journalist #1" in second
    assert "### [Journalist]" not in second


def test_journalist_brief_is_domain_neutral_and_lists_required_elements():
    """AUDIT B2: the default brief comes from config, not from psychiatry text in graph.py."""
    cfg = make_config()
    cfg["reviewer"]["required_elements"] = ["a limitations section", "a formal References section"]
    chat = FakeChat([decision(["Journalist"])])
    graph = build(cfg, chat, tools_for={"Journalist": [dummy_tool]})
    run(graph)
    brief = chat.human("Journalist")
    assert "clinical impairment" not in brief
    assert "write_manuscript_section" not in brief and "git_commit_snapshot" not in brief
    assert "• a limitations section" in brief and "• a formal References section" in brief
    assert "Your tools: dummy_tool" in brief


def test_custom_journalist_brief_is_rendered_with_config_variables():
    cfg = make_config()
    cfg["output_sections"] = ["A. Scope", "B. Evidence"]
    cfg["journalist"] = {"brief": "Sections:{% for s in output_sections %} {{ s }};{% endfor %} for {{ journalist_name }}"}
    chat = FakeChat([decision(["Journalist"])])
    graph = build(cfg, chat)
    run(graph)
    assert chat.human("Journalist").endswith("Sections: A. Scope; B. Evidence; for Journalist")


# ── 4. Robustness ──────────────────────────────────────────────────────────


def test_routing_failure_with_no_findings_is_retried_once_then_raised():
    """AUDIT R3: an unusable routing reply is retried; with nothing to write up, the run stops."""
    chat = FakeChat([ValueError("Failed to parse OrchestratorDecision")])
    graph = build(make_config(), chat)
    with pytest.raises(ValueError, match="OrchestratorDecision"):
        run(graph)
    assert len(chat.router.calls) == 2
    assert "could not be used" in chat.router.calls[1][1].content


def test_one_failed_parallel_branch_keeps_the_other_findings():
    """AUDIT R3: a model error in one branch is recorded; the sibling's findings survive."""
    chat = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])],
                    fail_for={"B"})
    graph = build(make_config(), chat)
    state = run(graph)
    assert "findings from A" in state["agent_outputs"]["A"]
    assert state["agent_outputs"]["B"].startswith("[AGENT FAILED] B: RuntimeError: simulated API failure")
    assert "[AGENT FAILED] B" in chat.human("Journalist")
    assert state["final_status"] == "approved"


def test_tool_exception_is_returned_to_the_agent_not_raised():
    """AUDIT B7: ToolNode(handle_tool_errors=True) turns a raising tool into a ToolMessage."""
    class ToolOnce(FakeChat):
        async def ainvoke(self, msgs, _bound=False, **kwargs):
            name = self.persona(msgs)
            self.calls.append((name, msgs))
            seen = [m for m in msgs if isinstance(m, ToolMessage)]
            if seen:
                return AIMessage(content=f"{name} saw: {seen[-1].content}", usage_metadata=USAGE)
            return AIMessage(content="", usage_metadata=USAGE,
                             tool_calls=[{"name": "exploding_tool", "args": {"x": "1"}, "id": "c1"}])

    chat = ToolOnce([decision(["A"]), decision(["Journalist"])])
    graph = build(make_config(), chat, tools_for={"A": [exploding_tool]})
    state = run(graph)
    assert "tool exploded" in state["agent_outputs"]["A"]


def test_exhausted_tool_rounds_still_yield_findings():
    """AUDIT B4: after the last tool round, one unbound call writes the answer from the tool results."""
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], tool_calls=True)
    graph = build(make_config(max_rounds=2), chat, tools_for={"A": [dummy_tool]})
    state = run(graph)
    a_flags = [bound for (name, _), bound in zip(chat.calls, chat.bound_flags) if name == "A"]
    assert a_flags == [True, True, False]           # 2 tool rounds, then the no-tools summary
    summary_prompt = chat.human("A", index=2)
    assert "TOOL-CALL LIMIT REACHED" in summary_prompt
    assert "[Result of dummy_tool]\ntool result" in summary_prompt
    assert state["agent_outputs"]["A"] == "findings from A #3"


def test_empty_journalist_draft_is_not_auto_approved():
    """AUDIT B4: an empty draft is rejected without an LLM call, and the run fails if it stays empty."""
    chat = FakeChat([decision(["Journalist"])], empty_for={"Journalist"})
    reviewer = FakeReviewer(["APPROVED"])
    graph = build(make_config(max_rev=2), chat, reviewer)
    state = run(graph)
    assert chat.count("Journalist") == 3            # initial draft + 2 revisions, all empty
    assert len(reviewer.calls) == 0                 # nothing to review, so no reviewer LLM call
    assert state["reviewer_approved"] is False
    assert state["final_status"] == "failed"
    assert "produced no draft text" in chat.human("Journalist", index=1)


# ── 5. HITL ────────────────────────────────────────────────────────────────


def test_hitl_post_plan_interrupts_and_resumes():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["post_plan"]})
    chat = FakeChat([decision(["A"]), decision(["Journalist"])])
    graph = build(cfg, chat)
    rc = {"configurable": {"thread_id": "t1"}}
    paused = run(graph, config=rc)
    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["checkpoint"] == "post_plan"
    final = resume(graph, "APPROVE", rc)
    assert "Journalist" in final["agent_outputs"]


def test_hitl_resume_executes_the_plan_the_user_approved():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["post_plan"]})
    chat = FakeChat([decision(["A"]), decision(["B"]), decision(["Journalist"])])
    graph = build(cfg, chat)
    rc = {"configurable": {"thread_id": "t2"}}
    paused = run(graph, config=rc)
    assert "['A']" in paused["__interrupt__"][0].value["question"]
    assert len(chat.router.calls) == 1
    resume(graph, "APPROVE", rc)
    # Before the fix the resume re-ran the router, which then picked B: the order was [B, Journalist].
    assert [name for name, _ in chat.calls] == ["A", "B", "Journalist"]
    assert len(chat.router.calls) == 3      # one per orchestrator pass, none replayed


def test_hitl_on_rejection_resume_honours_the_users_choice():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["on_rejection"]})
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: x", "APPROVED"])
    graph = build(cfg, chat, reviewer)
    rc = {"configurable": {"thread_id": "t3"}}
    paused = run(graph, config=rc)
    assert paused["__interrupt__"][0].value["checkpoint"] == "on_rejection"
    final = resume(graph, "REVISE", rc)
    assert final["revision_count"] == 1
    assert chat.count("Journalist") == 2


def planned_state(plan, instructions="original", assignments=None, call_count=None):
    state = initial_state()
    state.update({
        "next_agents": plan, "next_instructions": instructions, "agent_assignments": assignments or {},
        "agent_call_count": len(plan) if call_count is None else call_count, "routing_reasoning": "r",
    })
    return state


def post_plan(answer, specialists, state):
    cfg = make_config(specialists=specialists, hitl={"enabled": True, "checkpoints": ["post_plan"]})
    with patch("automation.graph.interrupt", return_value=answer):
        return make_post_plan_node(cfg, list(specialists))(state)


def test_hitl_override_does_not_dispatch_agents_the_user_asked_to_skip():
    out = post_plan("Skip LitScout, go straight to the Journalist", ("LitScout", "B"), planned_state(["B"]))
    assert "next_agents" not in out            # plan unchanged: no exact agent names given
    assert "USER REDIRECT: Skip LitScout, go straight to the Journalist" in out["next_instructions"]


def test_hitl_free_text_without_agent_names_becomes_a_redirect_note():
    """AUDIT R14: a redirect that names no agent keeps the plan but is appended to the instructions."""
    out = post_plan("please focus on epidemiology instead", ("Alpha", "Beta"),
                    planned_state(["Beta"], assignments={"Beta": "beta-task"}))
    assert "next_agents" not in out
    assert out["next_instructions"].startswith("original")
    assert "USER REDIRECT: please focus on epidemiology instead" in out["next_instructions"]
    # Specialists read their assignment first, so the note must reach it too.
    assert out["agent_assignments"]["Beta"].startswith("beta-task")
    assert "USER REDIRECT" in out["agent_assignments"]["Beta"]
    assert out["plan_reviewed"] is True


# ── 6. Cost and observability ──────────────────────────────────────────────


def test_orchestrator_token_usage_is_counted():
    chat = FakeChat([decision(["Journalist"])])
    graph = build(make_config(), chat)
    state = run(graph)
    router_calls = len(chat.router.calls)
    expected = USAGE["input_tokens"] * (chat.count("Journalist") + router_calls) + 1  # +1 reviewer
    assert state["token_usage"]["input_tokens"] == expected


def test_orchestrator_prompt_compresses_findings():
    cfg = make_config()
    router = ScriptedRouter([decision(["Journalist"])])
    with patch("automation.config.load_persona_content", return_value="p"):
        node = make_orchestrator_node(cfg, ["A", "B"], router, cfg["hitl"])
    state = initial_state()
    state["agent_outputs"] = {"A": "x" * 20000, "B": "y" * 20000}
    state["agent_call_count"] = 2
    node(state)
    prompt = next(m for m in router.calls[0] if isinstance(m, HumanMessage)).content
    assert len(prompt) < 10000


def test_orchestrator_persona_tools_are_not_loaded():
    """Documents audit B11: the orchestrator persona may list tools, but the routing model never gets them."""
    seen = []
    chat = FakeChat([decision(["Journalist"])])
    with patch("automation.graph.create_model", return_value=chat), \
         patch("automation.graph.create_reviewer_model", return_value=FakeReviewer()), \
         patch("automation.config.load_persona_content", return_value="persona"), \
         patch("automation.graph.load_tools_for_persona", side_effect=lambda c, p: seen.append(p["name"]) or []):
        build_graph(make_config())
    assert "Orch" not in seen


# ── 7. Grounding / config consistency ──────────────────────────────────────


def test_specialists_told_to_log_traceability_have_the_tool():
    from automation.config import CONFIG_PATH, load_config
    config = load_config(CONFIG_PATH)
    assert config.get("epistemic_tags")
    orch, journ = config["orchestrator"]["agent"], config["orchestrator"]["journalist"]
    trace_keys = {k for k, v in config["tools"].items() if v["function"] == "append_traceability_matrix"}
    missing = [p["name"] for p in config["personas"]
               if p["name"] not in (orch, journ) and not trace_keys & set(p.get("tools", []))]
    assert missing == []


# ── 8. Quick-win regression tests ──────────────────────────────────────────


def test_hitl_override_with_exact_names_dispatches_those_agents():
    out = post_plan("litscout, Journalist; and keep it short", ("LitScout", "Beta"), planned_state(["Beta"]))
    assert out["next_agents"] == ["LitScout", "Journalist"]
    assert out["agent_call_count"] == 1        # Beta's dispatch replaced by LitScout's
    assert out["agent_assignments"] == {}
    assert "USER REDIRECT: and keep it short" in out["next_instructions"]


def test_parse_plan_override_matches_whole_names_only():
    from automation.graph import _parse_plan_override
    agents, text = _parse_plan_override("Skip LitScout, go straight to the Journalist", ["LitScout", "Journalist"])
    assert agents == []
    assert text == "Skip LitScout, go straight to the Journalist"
    agents, text = _parse_plan_override("LitScout\nJournalist.", ["LitScout", "Journalist"])
    assert (agents, text) == (["LitScout", "Journalist"], "")


def test_compute_recursion_limit_covers_the_worst_case_run():
    """The limit passed to LangGraph must exceed the longest run the safety valves allow."""
    from automation.graph import compute_recursion_limit
    cfg = make_config(specialists=("A",), max_agent_calls=4, max_rev=2)
    chat = FakeChat([decision(["A"])])
    graph = build(cfg, chat, FakeReviewer(["REJECTED: n"]))
    steps = len(node_sequence(graph, config={"recursion_limit": compute_recursion_limit(cfg)}))
    assert steps == 2 * 4 + 1 + 1 + 2 * 2 + 1   # 4 cycles, valve, Journalist, 2 revision loops, force-approve
    assert compute_recursion_limit(cfg) > steps
    assert compute_recursion_limit(make_config(max_agent_calls=8, max_rev=3)) >= 25
    assert compute_recursion_limit(make_config(reviewer_enabled=False)) < compute_recursion_limit(make_config())


def test_routing_result_unpack_counts_tokens_and_surfaces_parse_errors():
    from automation.graph import _unpack_routing_result
    d = decision(["A"])
    assert _unpack_routing_result(d) == (d, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    raw = AIMessage(content="", usage_metadata=USAGE)
    assert _unpack_routing_result({"raw": raw, "parsed": d, "parsing_error": None}) == (d, USAGE)
    with pytest.raises(ValueError, match="unparseable"):
        _unpack_routing_result({"raw": raw, "parsed": None, "parsing_error": Exception("bad json")})


def test_traceability_instruction_only_when_the_agent_has_the_tool():
    from automation.config import build_agent_system_prompt
    persona = {"name": "A", "role": "r", "content": "c"}
    with_tool = build_agent_system_prompt(persona, ["[FACT]"], tool_names=["append_traceability_matrix"])
    without = build_agent_system_prompt(persona, ["[FACT]"], tool_names=["search_pubmed"])
    legacy = build_agent_system_prompt(persona, ["[FACT]"])
    assert "append_traceability_matrix" in with_tool and "append_traceability_matrix" in legacy
    assert "append_traceability_matrix" not in without
    assert "EPISTEMIC TAGS" in without and "in-text citations" in without


def test_doctor_warns_when_a_searching_specialist_cannot_log_traceability(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    report = inspect_swarm(tmp_path)
    assert not [i for i in report.issues if "no traceability tool" in i.message]
    for p in cfg["personas"]:
        if p["name"] == "LitScout":
            p["tools"] = [t for t in p["tools"] if t != "append_traceability"]
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    report = inspect_swarm(tmp_path)
    hits = [i for i in report.issues if "no traceability tool" in i.message]
    assert len(hits) == 1 and "LitScout" in hits[0].message and hits[0].severity == "warning"


def test_matrix_row_escapes_pipes_and_newlines(tmp_path):
    from automation.tools import append_traceability_matrix
    matrix = tmp_path / "M.md"
    matrix.write_text("| Source | Author/Agent | Claim | Method | Epistemic Tag |\n|--|--|--|--|--|\n", encoding="utf-8")
    cfg = {"swarm": {"output_dir": str(tmp_path), "traceability_matrix": str(matrix)}}
    with patch("automation.config.load_config", return_value=cfg):
        result = append_traceability_matrix.invoke({"fact": "a | b\nc", "source": "S|1", "epistemic_tag": "[FACT]"})
    assert result.startswith("SUCCESS")
    row = matrix.read_text(encoding="utf-8").splitlines()[-1]
    assert row.count("|") - row.count("\\|") == 6
    assert "a \\| b c" in row and "S\\|1" in row


def test_run_metrics_uses_configured_pricing(tmp_path):
    import json

    from automation.main import _write_run_metrics
    cfg = make_config()
    cfg["swarm"]["output_dir"] = str(tmp_path)
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "total_tokens": 2_000_000}
    _write_run_metrics(cfg, "t", usage)
    assert json.loads((tmp_path / "run_metrics.json").read_text())["estimated_cost_usd"] == 12.5
    cfg["model"]["pricing"] = {"input_per_m": 1.0, "output_per_m": 2.0}
    _write_run_metrics(cfg, "t", usage)
    metrics = json.loads((tmp_path / "run_metrics.json").read_text())
    assert metrics["estimated_cost_usd"] == 3.0 and "model.pricing" in metrics["cost_note"]


# ── 9. Journalist brief (audit B2) ─────────────────────────────────────────


def test_broken_journalist_brief_fails_at_build_time_and_in_doctor(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH, build_journalist_brief
    cfg = make_config()
    cfg["journalist"] = {"brief": "{{ no_such_variable }}"}
    with pytest.raises(ValueError, match="journalist.brief failed to render"):
        build(cfg, FakeChat([decision(["Journalist"])]))
    with pytest.raises(ValueError, match="empty"):
        build_journalist_brief({**cfg, "journalist": {"brief": "{% if false %}x{% endif %}"}})
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["journalist"] = {"brief": "{% for x in output_sections %}{{ x }}{% endfor %}"}  # key absent in root config
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    errors = [i.message for i in inspect_swarm(tmp_path).issues if i.severity == "error"]
    assert any("journalist.brief failed to render" in m for m in errors)


def test_load_config_rejects_non_string_brief(tmp_path):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["journalist"] = {"brief": ["not", "a", "string"]}
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match="journalist.brief"):
        load_config(tmp_path / "swarm_config.yml")


@pytest.mark.parametrize("pack, first, last", [
    ("toxicology", "A. Research Scope", "J. References"),
    ("hcm", "A. Research scope", "I. References"),
])
def test_example_briefs_render_their_output_sections(pack, first, last):
    from pathlib import Path

    from automation.config import build_journalist_brief, load_config
    cfg = load_config(Path(__file__).resolve().parents[1] / "examples" / pack / "swarm_config.yml")
    brief = build_journalist_brief(cfg, ["write_manuscript_section"])
    assert first in brief and last in brief
    assert brief.index(first) < brief.index(last)
    assert cfg["reviewer"]["required_elements"][0] in brief


def test_builder_spec_round_trips_journalist_brief(tmp_path):
    """The builder must preserve journalist.brief when it rewrites swarm_config.yml."""
    import shutil
    from pathlib import Path

    import yaml

    from automation.builder.loader import load_swarm_spec_from_disk
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "examples/toxicology/swarm_config.yml", tmp_path / "swarm_config.yml")
    shutil.copytree(root / "examples/toxicology", tmp_path / "examples/toxicology")
    original = yaml.safe_load((tmp_path / "swarm_config.yml").read_text(encoding="utf-8"))["journalist"]["brief"]
    spec = load_swarm_spec_from_disk(tmp_path)
    assert spec.journalist_brief == original
    assert spec.to_config()["journalist"] == {"brief": original}
    spec_without = spec.model_copy(update={"journalist_brief": None})
    assert "journalist" not in spec_without.to_config()

# ── 10. Failure handling, HITL nodes and builder passthrough (B4, B5, B13, R1, R3) ──


def test_hitl_pre_journalist_framing_and_orchestrator_instructions_reach_every_draft():
    """AUDIT B13: the Journalist never saw the orchestrator's instructions or the user's framing."""
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["pre_journalist"]})
    chat = FakeChat([decision(["Journalist"], instructions="ORCH-FOCUS-ON-X")])
    graph = build(cfg, chat, FakeReviewer(["REJECTED: fix", "APPROVED"]))
    rc = {"configurable": {"thread_id": "b13"}}
    paused = run(graph, config=rc)
    assert paused["__interrupt__"][0].value["checkpoint"] == "pre_journalist"
    resume(graph, "AUDIENCE-IS-REGULATORS", rc)
    first, second = chat.human("Journalist", 0), chat.human("Journalist", 1)
    assert "INSTRUCTIONS FROM THE ORCHESTRATOR:\nORCH-FOCUS-ON-X" in first
    assert "AUDIENCE-IS-REGULATORS" in first and "AUDIENCE-IS-REGULATORS" in second
    assert "ORCH-FOCUS-ON-X" in second


def test_hitl_checkpoints_run_as_separate_nodes_without_llm_calls():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["post_plan", "pre_journalist", "on_rejection"]})
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: x", "APPROVED"])
    graph = build(cfg, chat, reviewer)
    rc = {"configurable": {"thread_id": "nodes"}}
    paused = run(graph, config=rc)
    seen = [paused["__interrupt__"][0].value["checkpoint"]]
    for answer in ["APPROVE", "", "REVISE"]:
        paused = resume(graph, answer, rc)
        if "__interrupt__" in paused:
            seen.append(paused["__interrupt__"][0].value["checkpoint"])
    assert seen == ["post_plan", "pre_journalist", "on_rejection"]
    assert len(chat.router.calls) == 1          # never replayed across three resumes
    assert len(reviewer.calls) == 2             # one per draft, never replayed
    assert paused["final_status"] == "approved" and paused["revision_count"] == 1


def test_hitl_on_rejection_override_ends_the_run_as_overridden():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["on_rejection"]})
    chat = FakeChat([decision(["Journalist"])])
    graph = build(cfg, chat, FakeReviewer(["REJECTED: x"]))
    rc = {"configurable": {"thread_id": "ovr"}}
    run(graph, config=rc)
    final = resume(graph, "OVERRIDE", rc)
    assert final["reviewer_approved"] is True and final["final_status"] == "overridden"
    assert chat.count("Journalist") == 1


def test_hitl_on_rejection_custom_instructions_replace_reviewer_feedback():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["on_rejection"]})
    chat = FakeChat([decision(["Journalist"])])
    graph = build(cfg, chat, FakeReviewer(["REJECTED: reviewer text", "APPROVED"]))
    rc = {"configurable": {"thread_id": "custom"}}
    run(graph, config=rc)
    resume(graph, "Shorten the summary to 200 words", rc)
    second = chat.human("Journalist", index=1)
    assert "Shorten the summary to 200 words" in second and "reviewer text" not in second


def test_routing_reply_retried_once_after_an_unusable_reply():
    chat = FakeChat([ValueError("bad json"), decision(["A"]), decision(["Journalist"])])
    graph = build(make_config(), chat)
    state = run(graph)
    assert chat.count("A") == 1
    assert "routing retried after an unusable reply" in state["messages"][0].content


def test_routing_failing_twice_after_findings_hands_them_to_the_journalist():
    chat = FakeChat([decision(["A"]), ValueError("bad 1"), ValueError("bad 2")])
    graph = build(make_config(), chat)
    state = run(graph)
    assert state["final_status"] == "approved"
    assert "findings from A" in chat.human("Journalist")
    assert any("routing failed twice" in m.content for m in state["messages"])


def test_abort_policy_restores_fail_fast_behaviour():
    cfg = make_config()
    cfg["orchestrator"]["on_agent_failure"] = "abort"
    chat = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"})], fail_for={"B"})
    with pytest.raises(RuntimeError, match="simulated API failure in B"):
        run(build(cfg, chat))
    chat = FakeChat([ValueError("bad json")])
    with pytest.raises(ValueError):
        run(build(cfg, chat))
    assert len(chat.router.calls) == 1          # no retry under abort


def test_load_config_rejects_unknown_failure_policy(tmp_path):
    import yaml

    from automation.config import CONFIG_PATH, load_config
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"]["on_agent_failure"] = "ignore"
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    with pytest.raises(ValueError, match="on_agent_failure"):
        load_config(tmp_path / "swarm_config.yml")


def test_journalist_failure_is_retried_through_the_reviewer_then_fails_the_run():
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], fail_for={"Journalist"})
    reviewer = FakeReviewer(["APPROVED"])
    state = run(build(make_config(max_rev=1), chat, reviewer))
    assert chat.count("Journalist") == 2
    assert len(reviewer.calls) == 0
    assert state["final_status"] == "failed"
    assert "findings from A" in state["agent_outputs"]["A"]     # evidence kept for the user
    assert any("[AGENT FAILED] Journalist" in m.content for m in state["messages"])


def test_journalist_failure_without_reviewer_reports_failed():
    chat = FakeChat([decision(["Journalist"])], fail_for={"Journalist"})
    state = run(build(make_config(reviewer_enabled=False), chat))
    assert state["final_status"] == "failed"


def test_duplicate_targets_are_dispatched_once():
    chat = FakeChat([decision(["A", "A"]), decision(["Journalist"])])
    state = run(build(make_config(), chat))
    assert chat.count("A") == 1 and state["agent_call_count"] == 1


def test_combine_findings_keeps_rounds_newest_first_and_drops_failure_notes():
    from automation.graph import FAILED_PREFIX, _combine_findings
    assert _combine_findings("A", "", "r1") == "r1"
    assert _combine_findings("A", "r1", "r2") == "r2\n\n[Earlier findings from A]\nr1"
    assert _combine_findings("A", f"{FAILED_PREFIX} A: boom", "r2") == "r2"
    failed_later = _combine_findings("A", "r1", f"{FAILED_PREFIX} A: boom")
    assert failed_later.startswith(FAILED_PREFIX) and failed_later.endswith("r1")


def test_message_text_handles_block_lists():
    from automation.graph import _message_text
    msg = AIMessage(content=[{"type": "text", "text": "APPROVED"}, {"type": "tool_use", "id": "x"}])
    assert _message_text(msg) == "APPROVED"
    assert _message_text(AIMessage(content="plain")) == "plain"


def test_reviewer_accepts_block_list_content():
    class BlockReviewer(FakeReviewer):
        def invoke(self, msgs):
            self.calls.append(msgs)
            return AIMessage(content=[{"type": "text", "text": "APPROVED"}])

    reviewer = BlockReviewer()
    state = run(build(make_config(), FakeChat([decision(["Journalist"])]), reviewer))
    assert state["final_status"] == "approved"


def test_recursion_limit_grows_with_hitl_checkpoints():
    from automation.graph import compute_recursion_limit
    plain = compute_recursion_limit(make_config(max_rev=3))
    all_on = compute_recursion_limit(make_config(max_rev=3, hitl={
        "enabled": True, "checkpoints": ["post_plan", "pre_journalist", "on_rejection"]}))
    assert all_on == plain + 2 + 3


def test_run_loop_reports_final_status():
    from automation.main import _run_graph_async
    graph = build(make_config(max_rev=1), FakeChat([decision(["Journalist"])]), FakeReviewer(["REJECTED: n"]))
    usage, status = asyncio.run(_run_graph_async(make_config(), "task", graph, {}))
    assert status == "force_approved"
    assert usage["total_tokens"] > 0


def test_builder_round_trip_preserves_unmodelled_keys(tmp_path):
    """Builder commands rewrite swarm_config.yml from the spec; keys it does not model must survive."""
    import shutil
    from pathlib import Path

    import yaml

    from automation.builder.generator import render_swarm_config
    from automation.builder.loader import load_swarm_spec_from_disk
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "examples/toxicology/swarm_config.yml").read_text(encoding="utf-8"))
    cfg["orchestrator"]["on_agent_failure"] = "abort"
    cfg["orchestrator"]["max_findings_chars"] = 900
    cfg["model"]["pricing"] = {"input_per_m": 1.0, "output_per_m": 2.0}
    cfg["reviewer"]["custom_prompt"] = "Be strict."
    cfg["reviewer"]["model"].update({"provider": "anthropic", "env_key": "ANTHROPIC_API_KEY"})
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    shutil.copytree(root / "examples/toxicology", tmp_path / "examples/toxicology")

    spec = load_swarm_spec_from_disk(tmp_path)
    out = yaml.safe_load(render_swarm_config(spec))
    assert out["output_sections"] == cfg["output_sections"]
    assert out["orchestrator"]["on_agent_failure"] == "abort"
    assert out["orchestrator"]["max_findings_chars"] == 900
    assert out["model"]["pricing"] == {"input_per_m": 1.0, "output_per_m": 2.0}
    assert out["reviewer"]["custom_prompt"] == "Be strict."
    assert out["reviewer"]["model"]["provider"] == "anthropic"
    assert out["journalist"] == cfg["journalist"]

    # Clearing the reviewer model in the builder must not leave a half model block behind.
    cleared = yaml.safe_load(render_swarm_config(spec.model_copy(update={"reviewer_model_name": None})))
    assert "model" not in cleared["reviewer"]
    assert cleared["reviewer"]["custom_prompt"] == "Be strict."


# ── 11. Observability: run log and persisted findings (R6, I5) ─────────────


def _read_jsonl(path):
    import json
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_log_writes_one_line_per_node_update(tmp_path):
    from automation.main import _run_graph_async
    cfg = make_config(max_rev=2)
    cfg["swarm"]["run_log"] = str(tmp_path / "logs" / "run.jsonl")
    chat = FakeChat([decision(["A"], assignments={"A": "look at X"}), decision(["Journalist"])])
    graph = build(cfg, chat, FakeReviewer(["REJECTED: add limits", "APPROVED"]))
    asyncio.run(_run_graph_async(cfg, "task", graph, {}))
    lines = _read_jsonl(tmp_path / "logs" / "run.jsonl")
    assert [line["event"] for line in (lines[0], lines[-1])] == ["run_start", "run_end"]
    nodes = [line for line in lines if line["event"] == "node"]
    assert [n["node"] for n in nodes] == ["orchestrator", "A", "orchestrator", "Journalist", "reviewer",
                                         "Journalist", "reviewer"]
    first = nodes[0]
    assert first["next_agents"] == ["A"] and first["routing_reasoning"] == "because"
    assert first["assignments"] == {"A": "look at X"}
    assert first["token_delta"] == USAGE and "timestamp" in first
    assert nodes[4]["verdict"] == "rejected" and nodes[4]["feedback"] == "REJECTED: add limits"
    assert nodes[6]["verdict"] == "approved" and nodes[6]["final_status"] == "approved"
    assert lines[-1]["final_status"] == "approved"
    assert lines[-1]["token_usage"]["total_tokens"] > 0


def test_run_log_records_hitl_answers(tmp_path):
    from automation import main
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["post_plan"]})
    cfg["swarm"]["run_log"] = str(tmp_path / "run.jsonl")
    graph = build(cfg, FakeChat([decision(["A"]), decision(["Journalist"])]))
    with patch.object(main, "_handle_hitl_interrupt", return_value="APPROVE"):
        asyncio.run(main._run_graph_async(cfg, "task", graph, {"configurable": {"thread_id": "log-t"}}))
    nodes = [line for line in _read_jsonl(tmp_path / "run.jsonl") if line["event"] == "node"]
    hitl = [n for n in nodes if "hitl" in n]
    assert len(hitl) == 1 and hitl[0]["node"] == "hitl_post_plan"
    assert hitl[0]["hitl"] == {"checkpoint": "post_plan", "answer": "APPROVE"}
    assert all(n["run_id"] == "log-t" for n in nodes)


def test_persisted_findings_survive_a_crash(tmp_path):
    from automation.main import _run_graph_async
    cfg = make_config(specialists=("TOCSAS Lead", "B"))
    cfg["orchestrator"]["on_agent_failure"] = "abort"
    cfg["swarm"].update({"output_dir": str(tmp_path), "persist_findings": True, "run_log": str(tmp_path / "r.jsonl")})
    chat = FakeChat([decision(["TOCSAS Lead", "B"]), decision(["Journalist"])], fail_for={"Journalist"})
    graph = build(cfg, chat)
    with pytest.raises(RuntimeError, match="simulated API failure in Journalist"):
        asyncio.run(_run_graph_async(cfg, "task", graph, {}))
    files = sorted(p.name for p in (tmp_path / "findings").iterdir())
    assert files == ["b.md", "tocsas-lead.md"]
    assert "findings from TOCSAS Lead" in (tmp_path / "findings" / "tocsas-lead.md").read_text(encoding="utf-8")
    end = _read_jsonl(tmp_path / "r.jsonl")[-1]
    assert end["event"] == "run_end" and end["final_status"] == "error" and "Journalist" in end["error"]


def test_observability_is_off_by_default(tmp_path):
    from automation.main import _run_graph_async
    cfg = make_config()
    cfg["swarm"]["output_dir"] = str(tmp_path)
    graph = build(cfg, FakeChat([decision(["A"]), decision(["Journalist"])]))
    asyncio.run(_run_graph_async(cfg, "task", graph, {}))
    assert list(tmp_path.iterdir()) == []
    from automation.config import CONFIG_PATH, get_run_log_path, load_config
    root = load_config(CONFIG_PATH)
    assert get_run_log_path(root) is None and not root["swarm"].get("persist_findings")


def test_unwritable_run_artefacts_do_not_stop_the_run(tmp_path):
    from automation.main import _run_graph_async
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    cfg = make_config()
    cfg["swarm"].update({"output_dir": str(blocker), "persist_findings": True, "run_log": str(blocker / "r.jsonl")})
    graph = build(cfg, FakeChat([decision(["A"]), decision(["Journalist"])]))
    usage, status = asyncio.run(_run_graph_async(cfg, "task", graph, {}))
    assert status == "approved"


def test_findings_files_name_the_run_and_keep_unicode_names(tmp_path):
    from automation.config import findings_slug
    from automation.main import _run_graph_async
    assert findings_slug("Ärztin Lead") == "ärztin-lead" and findings_slug("A_B") == findings_slug("a b")
    cfg = make_config(specialists=("Ärztin",))
    cfg["swarm"].update({"output_dir": str(tmp_path), "persist_findings": True})
    graph = build(cfg, FakeChat([decision(["Ärztin"]), decision(["Journalist"])]))
    asyncio.run(_run_graph_async(cfg, "task", graph, {"configurable": {"thread_id": "th-9"}}))
    assert "<!-- run th-9," in (tmp_path / "findings" / "ärztin.md").read_text(encoding="utf-8")


def test_doctor_rejects_colliding_findings_files(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["swarm"]["persist_findings"] = True
    real["personas"][1]["name"] = "Lit_Scout"
    real["personas"][3]["name"] = "lit scout"
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    errors = [i.message for i in inspect_swarm(tmp_path).issues if i.severity == "error"]
    assert any("findings/lit-scout.md" in m for m in errors)


def test_doctor_rejects_a_run_log_that_is_a_directory(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["swarm"]["run_log"] = str(tmp_path)
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    errors = [i.message for i in inspect_swarm(tmp_path).issues if i.severity == "error"]
    assert any("swarm.run_log points to a directory" in m for m in errors)


# ── 12. Provenance and orchestrator tools (B11, B12) ──────────────────────

MATRIX_HEADER = "| Source | Author/Agent | Claim | Method | Epistemic Tag |\n|--|--|--|--|--|\n"


class CallsToolOnce(FakeChat):
    """Each tool-bound persona issues one ``tool_name`` call, then answers with the result it saw."""

    def __init__(self, decisions, tool_name, args, **kwargs):
        super().__init__(decisions, **kwargs)
        self.tool_name, self.args = tool_name, args

    async def ainvoke(self, msgs, _bound=False, **kwargs):
        name = self.persona(msgs)
        seen = [m.content for m in msgs if isinstance(m, ToolMessage)]
        if not _bound or name in self.fail_for:
            return await super().ainvoke(msgs, _bound=_bound)
        self.calls.append((name, msgs))
        self.bound_flags.append(_bound)
        if seen:
            return AIMessage(content=f"{name} checked: {seen[-1]}", usage_metadata=USAGE)
        self.n += 1
        return AIMessage(content="", usage_metadata=USAGE,
                         tool_calls=[{"name": self.tool_name, "args": self.args, "id": f"t{self.n}"}])


def _matrix(tmp_path):
    matrix = tmp_path / "M.md"
    matrix.write_text(MATRIX_HEADER, encoding="utf-8")
    return matrix, {"swarm": {"output_dir": str(tmp_path), "traceability_matrix": str(matrix)}}


def _matrix_rows(matrix):
    return matrix.read_text(encoding="utf-8").splitlines()[2:]


TRACE_ARGS = {"fact": "f", "source": "s", "epistemic_tag": "[FACT]"}


def _trace_chat(decisions, **kwargs):
    return CallsToolOnce(decisions, "append_traceability_matrix", TRACE_ARGS, **kwargs)


def test_specialist_matrix_row_names_the_calling_persona(tmp_path):
    """AUDIT B12: the row records the specialist that logged it, not 'Auto-Agent'."""
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    chat = _trace_chat([decision(["A"]), decision(["Journalist"])])
    graph = build(make_config(), chat, tools_for={"A": [append_traceability_matrix]})
    with patch("automation.config.load_config", return_value=cfg):
        state = run(graph)
    rows = _matrix_rows(matrix)
    assert len(rows) == 1 and "| **A** |" in rows[0]
    assert "SUCCESS" in state["agent_outputs"]["A"]


def test_parallel_specialists_each_log_their_own_row(tmp_path):
    """Send fan-out: both rows survive (appends are serialised) and each names its persona."""
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    for _ in range(5):
        matrix.write_text(MATRIX_HEADER, encoding="utf-8")
        chat = _trace_chat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])])
        graph = build(make_config(), chat, tools_for={"A": [append_traceability_matrix],
                                                      "B": [append_traceability_matrix]})
        with patch("automation.config.load_config", return_value=cfg):
            run(graph)
        rows = _matrix_rows(matrix)
        assert sorted(r.split(" | ")[1] for r in rows) == ["**A**", "**B**"]


def test_journalist_matrix_row_names_the_journalist_persona(tmp_path):
    """The Journalist node is called 'Journalist'; the row must carry the persona name from the config."""
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    config = make_config()
    config["personas"][-1]["name"] = "Scribe"
    config["orchestrator"]["journalist"] = "Scribe"
    chat = _trace_chat([decision(["Journalist"])])
    graph = build(config, chat, tools_for={"Scribe": [append_traceability_matrix]})
    with patch("automation.config.load_config", return_value=cfg):
        run(graph)
    rows = _matrix_rows(matrix)
    assert len(rows) == 1 and "| **Scribe** |" in rows[0]


def test_matrix_row_without_a_persona_says_auto_agent(tmp_path):
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    with patch("automation.config.load_config", return_value=cfg):
        append_traceability_matrix.invoke(TRACE_ARGS)
    assert "| **Auto-Agent** |" in _matrix_rows(matrix)[0]


def test_matrix_row_reads_and_escapes_the_persona_from_config_metadata(tmp_path):
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    with patch("automation.config.load_config", return_value=cfg):
        append_traceability_matrix.invoke(TRACE_ARGS, config={"metadata": {"murmuration_agent": "Dr. X | Lab"}})
    row = _matrix_rows(matrix)[0]
    assert "| **Dr. X \\| Lab** |" in row
    assert row.count("|") - row.count("\\|") == 6


def test_traceability_tool_schema_is_unchanged():
    """The injected RunnableConfig parameter must not be visible to the model."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from automation.tools import append_traceability_matrix
    assert set(append_traceability_matrix.args) == {"fact", "source", "epistemic_tag"}
    params = convert_to_openai_tool(append_traceability_matrix)["function"]["parameters"]
    assert set(params["properties"]) == {"fact", "source", "epistemic_tag"}
    assert set(params.get("required", [])) == {"fact", "source", "epistemic_tag"}


def test_tool_metadata_merges_with_the_node_config():
    """A config passed to ToolNode replaces the parent's metadata; it must be merged, not dropped."""
    from automation.graph import _tool_run_config
    parent = {"metadata": {"thread": "t1"}, "configurable": {"thread_id": "t1"}, "run_id": "r"}
    cfg = _tool_run_config(parent, "A")
    assert cfg["metadata"] == {"thread": "t1", "murmuration_agent": "A"}
    assert cfg["configurable"] == {"thread_id": "t1"} and "run_id" not in cfg
    assert parent["metadata"] == {"thread": "t1"}
    assert _tool_run_config(None, None) is None


def _orch_tools_config(allow_tools, **kwargs):
    config = make_config(**kwargs)
    config["orchestrator"]["allow_tools"] = allow_tools
    return config


def test_orchestrator_tool_loop_runs_before_routing_when_allowed(tmp_path):
    """AUDIT B11: with allow_tools the orchestrator's quick checks reach the routing prompt."""
    from automation.tools import append_traceability_matrix
    matrix, cfg = _matrix(tmp_path)
    tools_for = {"Orch": [append_traceability_matrix]}

    off = _trace_chat([decision(["Journalist"])])
    with patch("automation.config.load_config", return_value=cfg):
        state_off = run(build(_orch_tools_config(False), off, tools_for=tools_for))
    assert off.count("Orch") == 0 and _matrix_rows(matrix) == []

    on = _trace_chat([decision(["Journalist"])])
    with patch("automation.config.load_config", return_value=cfg):
        state_on = run(build(_orch_tools_config(True), on, tools_for=tools_for))
    orch_flags = [bound for (name, _), bound in zip(on.calls, on.bound_flags) if name == "Orch"]
    assert orch_flags == [True, True]                       # one tool round, then the report
    routing_prompt = on.router.calls[0][1].content
    assert "YOUR QUICK CHECKS" in routing_prompt and "Orch checked: SUCCESS" in routing_prompt
    assert "YOUR QUICK CHECKS" not in off.router.calls[0][1].content
    assert "| **Orch** |" in _matrix_rows(matrix)[0]
    extra = state_on["token_usage"]["total_tokens"] - state_off["token_usage"]["total_tokens"]
    assert extra == USAGE["total_tokens"] * on.count("Orch")


def test_orchestrator_tools_only_loaded_and_bound_when_allowed():
    seen = []
    bound = []

    class Recording(FakeChat):
        def bind_tools(self, tools, **kwargs):
            bound.append([t.name for t in tools])
            return super().bind_tools(tools, **kwargs)

    for allow in (False, True):
        seen.clear()
        bound.clear()
        with patch("automation.graph.create_model", return_value=Recording([decision(["Journalist"])])), \
             patch("automation.graph.create_reviewer_model", return_value=FakeReviewer()), \
             patch("automation.config.load_persona_content", return_value="persona"), \
             patch("automation.graph.load_tools_for_persona",
                   side_effect=lambda c, p: seen.append(p["name"]) or ([dummy_tool] if p["name"] == "Orch" else [])):
            build_graph(_orch_tools_config(allow))
        assert ("Orch" in seen) is allow
        assert bound == ([["dummy_tool"]] if allow else [])


def test_orchestrator_quick_checks_skip_the_safety_valve_pass():
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], tool_calls=True)
    graph = build(_orch_tools_config(True, max_agent_calls=1, max_rounds=1), chat, tools_for={"Orch": [dummy_tool]})
    state = run(graph)
    assert len(chat.router.calls) == 1                     # the second pass is the safety valve
    orch_flags = [bound for (name, _), bound in zip(chat.calls, chat.bound_flags) if name == "Orch"]
    assert orch_flags == [True, False]                     # one loop: 1 tool round + no-tools summary
    assert "safety valve" in state["routing_reasoning"]


def test_orchestrator_tool_failure_is_noted_under_continue():
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], fail_for={"Orch"})
    graph = build(_orch_tools_config(True), chat, tools_for={"Orch": [dummy_tool]})
    state = run(graph)
    first = state["messages"][0].content
    assert "quick checks failed (RuntimeError: simulated API failure in Orch)" in first
    assert "YOUR QUICK CHECKS" not in chat.router.calls[0][1].content
    assert state["final_status"] == "approved"


def test_orchestrator_tool_failure_raises_under_abort():
    config = _orch_tools_config(True)
    config["orchestrator"]["on_agent_failure"] = "abort"
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], fail_for={"Orch"})
    graph = build(config, chat, tools_for={"Orch": [dummy_tool]})
    with pytest.raises(RuntimeError, match="simulated API failure in Orch"):
        run(graph)


def test_orchestrator_tool_loop_lets_graph_interrupts_propagate():
    from langgraph.errors import GraphBubbleUp, GraphInterrupt

    class Interrupting:
        async def ainvoke(self, msgs, **kwargs):
            raise GraphInterrupt(())

    with patch("automation.config.load_persona_content", return_value="persona"):
        node = make_orchestrator_node(_orch_tools_config(True), ["A", "B"], ScriptedRouter([decision(["A"])]),
                                      tool_model=Interrupting(), tools=[dummy_tool])
    with pytest.raises(GraphBubbleUp):
        asyncio.run(node(initial_state()))


def test_orchestrator_without_allow_tools_stays_a_sync_router():
    """Default: no tools loaded, the routing prompt has no quick-checks block."""
    with patch("automation.config.load_persona_content", return_value="persona"):
        node = make_orchestrator_node(make_config(), ["A", "B"], ScriptedRouter([decision(["A"])]))
    assert not asyncio.iscoroutinefunction(node)
    out = node(initial_state())
    assert out["next_agents"] == ["A"]


def test_recursion_limit_ignores_allow_tools():
    """The orchestrator's tool loop runs inside its node: no extra graph steps."""
    from automation.graph import compute_recursion_limit
    assert compute_recursion_limit(_orch_tools_config(True)) == compute_recursion_limit(make_config())


def test_doctor_orchestrator_tool_warnings_never_contradict(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    base = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    orch = base["orchestrator"]["agent"]

    def warnings_for(allow, tools):
        cfg = yaml.safe_load(yaml.safe_dump(base))
        if allow is not None:
            cfg["orchestrator"]["allow_tools"] = allow
        next(p for p in cfg["personas"] if p["name"] == orch)["tools"] = tools
        (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        messages = [i.message for i in inspect_swarm(tmp_path).issues if i.severity == "warning"]
        ignored = [m for m in messages if m.startswith(f"orchestrator '{orch}'") and "allow_tools: true" in m]
        weak = [m for m in messages if m.startswith(f"orchestrator '{orch}' has no discovery-oriented tools")]
        return len(ignored), len(weak)

    assert warnings_for(None, ["search_web", "append_traceability"]) == (1, 0)   # root default: tools ignored
    assert warnings_for(False, ["append_traceability"]) == (1, 0)
    assert warnings_for(True, ["search_web", "append_traceability"]) == (0, 0)
    assert warnings_for(True, ["append_traceability"]) == (0, 1)                 # tools matter only when on


def test_builder_round_trip_preserves_allow_tools(tmp_path):
    import shutil
    from pathlib import Path

    import yaml

    from automation.builder.generator import render_swarm_config
    from automation.builder.loader import load_swarm_spec_from_disk
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "examples/toxicology/swarm_config.yml").read_text(encoding="utf-8"))
    shutil.copytree(root / "examples/toxicology", tmp_path / "examples/toxicology")
    for value in (True, False):
        cfg["orchestrator"]["allow_tools"] = value
        (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        out = yaml.safe_load(render_swarm_config(load_swarm_spec_from_disk(tmp_path)))
        assert out["orchestrator"]["allow_tools"] is value


# ── 13. Per-persona models and fan-out limit (I3, R9) ──────────────────────


def build_with_models(config, chats, reviewer=None, tools_for=None):
    """Like build(), but create_model returns ``chats[model name]`` and records each merged spec."""
    specs = []

    def fake_create_model(cfg):
        specs.append(dict(cfg["model"]))
        return chats[cfg["model"]["name"]]

    tools_for = tools_for or {}
    with patch("automation.graph.create_model", side_effect=fake_create_model), \
         patch("automation.graph.create_reviewer_model", return_value=reviewer or FakeReviewer()), \
         patch("automation.config.load_persona_content", return_value="persona"), \
         patch("automation.graph.load_tools_for_persona", side_effect=lambda c, p: tools_for.get(p["name"], [])):
        return build_graph(config), specs


def test_persona_model_override_is_merged_and_used_by_that_persona_only():
    cfg = make_config(specialists=("A", "B"))
    cfg["personas"][1]["model"] = {"name": "small"}
    cfg["personas"][-1]["model"] = {"name": "big", "temperature": 0.5}
    base = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])])
    small, big = FakeChat([decision(["Journalist"])]), FakeChat([decision(["Journalist"])])
    graph, specs = build_with_models(cfg, {"m": base, "small": small, "big": big})
    run(graph)
    assert [n for n, _ in small.calls] == ["A"]
    assert [n for n, _ in big.calls] == ["Journalist"]
    assert [n for n, _ in base.calls] == ["B"] and len(base.router.calls) == 2
    assert {"provider": "openai", "name": "small", "temperature": 0, "env_key": "K"} in specs
    assert {"provider": "openai", "name": "big", "temperature": 0.5, "env_key": "K"} in specs


def test_orchestrator_model_override_routes_with_that_model():
    cfg = make_config()
    cfg["personas"][0]["model"] = {"name": "router"}
    base, router = FakeChat([decision(["Journalist"])]), FakeChat([decision(["A"]), decision(["Journalist"])])
    graph, _ = build_with_models(cfg, {"m": base, "router": router})
    run(graph)
    assert base.router is None and len(router.router.calls) == 2
    assert [n for n, _ in base.calls] == ["A", "Journalist"]


def test_personas_with_the_same_model_override_share_one_client():
    cfg = make_config(specialists=("A", "B"))
    cfg["personas"][1]["model"] = {"name": "small"}
    cfg["personas"][2]["model"] = {"name": "small"}
    small = FakeChat([decision(["Journalist"])])
    _, specs = build_with_models(cfg, {"m": FakeChat([decision(["Journalist"])]), "small": small})
    assert [s["name"] for s in specs] == ["m", "small"]


def test_validate_env_checks_persona_model_keys(monkeypatch):
    from automation.config import validate_env
    cfg = make_config()
    cfg["personas"][1]["model"] = {"provider": "anthropic", "name": "c", "env_key": "PERSONA_ONLY_KEY"}
    monkeypatch.setenv("K", "x")
    monkeypatch.delenv("PERSONA_ONLY_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="PERSONA_ONLY_KEY.*persona 'A'"):
        validate_env(cfg)
    monkeypatch.setenv("PERSONA_ONLY_KEY", "y")
    validate_env(cfg)


def test_provider_switch_without_env_key_checks_that_providers_key(tmp_path, monkeypatch):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH, validate_env
    cfg = make_config()
    cfg["personas"][1]["model"] = {"provider": "anthropic", "name": "claude-x"}
    monkeypatch.setenv("K", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY.*persona 'A'"):
        validate_env(cfg)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["personas"][1]["model"] = {"provider": "anthropic", "name": "claude-x"}
    real["personas"][2]["model"] = {"name": "gpt-4o-mini"}       # same key as the base: no extra warning
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    warnings = [i.message for i in inspect_swarm(tmp_path).issues if i.severity == "warning"]
    persona_key_warnings = [m for m in warnings if "for persona" in m]
    assert persona_key_warnings == ["model env key 'ANTHROPIC_API_KEY' for persona 'Specialist1' is not set "
                                    "in the current shell"]


def test_builder_round_trips_persona_models(tmp_path):
    import shutil
    from pathlib import Path

    import yaml

    from automation.builder.generator import render_swarm_config
    from automation.builder.loader import load_swarm_spec_from_disk
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load((root / "examples/toxicology/swarm_config.yml").read_text(encoding="utf-8"))
    override = {"provider": "anthropic", "name": "claude-x", "temperature": 0.3, "env_key": "ANTHROPIC_API_KEY"}
    cfg["personas"][2]["model"] = override
    cfg["orchestrator"]["max_parallel_agents"] = 2
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    shutil.copytree(root / "examples/toxicology", tmp_path / "examples/toxicology")
    out = yaml.safe_load(render_swarm_config(load_swarm_spec_from_disk(tmp_path)))
    assert out["personas"][2]["model"] == override
    assert all("model" not in p for i, p in enumerate(out["personas"]) if i != 2)
    assert out["orchestrator"]["max_parallel_agents"] == 2


class ConcurrencyChat(FakeChat):
    """Tracks how many specialist model calls are in flight at once."""

    def __init__(self, decisions, specialists):
        super().__init__(decisions)
        self.specialists, self.in_flight, self.peak = set(specialists), 0, 0

    async def ainvoke(self, msgs, _bound=False, **kwargs):
        if self.persona(msgs) not in self.specialists:
            return await super().ainvoke(msgs, _bound=_bound)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.02)
        self.in_flight -= 1
        return await super().ainvoke(msgs, _bound=_bound)


@pytest.mark.parametrize("limit, peak", [(None, 3), (1, 1), (2, 2)])
def test_max_parallel_agents_caps_concurrent_specialists(limit, peak):
    names = ("A", "B", "C")
    cfg = make_config(specialists=names)
    cfg["orchestrator"]["max_parallel_agents"] = limit
    plan = decision(list(names), assignments={n: n for n in names})
    chat = ConcurrencyChat([], names)
    graph = build(cfg, chat)
    for _ in range(2):          # a second asyncio.run() on the same graph uses a new event loop
        chat.router.decisions[:] = [plan, decision(["Journalist"])]
        chat.peak = 0
        state = run(graph)
        assert chat.peak == peak
        assert sorted(k for k in state["agent_outputs"] if k in names) == list(names)


def test_recursion_limit_ignores_max_parallel_agents():
    from automation.graph import compute_recursion_limit
    cfg = make_config()
    cfg["orchestrator"]["max_parallel_agents"] = 1
    assert compute_recursion_limit(cfg) == compute_recursion_limit(make_config())


# ── 14. Persistent checkpointer and resume (R5) ────────────────────────────


def _sqlite_cfg(tmp_path, **kwargs):
    cfg = make_config(**kwargs)
    cfg["orchestrator"]["on_agent_failure"] = "abort"
    cfg["checkpointer"] = {"type": "sqlite", "path": str(tmp_path / "ckpt" / "run.sqlite")}
    return cfg


def _run_with_saver(cfg, chat, run_config, resume=False, reviewer=None):
    """One "process": open the configured saver, build the graph on it, run or resume."""
    from automation.config import open_checkpointer
    from automation.main import _run_graph_async

    async def go():
        async with open_checkpointer(cfg) as saver:
            with patch("automation.graph.create_model", return_value=chat), \
                 patch("automation.graph.create_reviewer_model", return_value=reviewer or FakeReviewer()), \
                 patch("automation.config.load_persona_content", return_value="persona"), \
                 patch("automation.graph.load_tools_for_persona", return_value=[]):
                graph = build_graph(cfg, checkpointer=saver)
            return await _run_graph_async(cfg, "task", graph, run_config, resume=resume)
    return asyncio.run(go())


def test_crashed_run_resumes_from_sqlite_without_rerunning_finished_specialists(tmp_path):
    """Acceptance test for R5: a run that raises midway resumes in a new "process" on the same thread."""
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    cfg = _sqlite_cfg(tmp_path)
    rc = {"configurable": {"thread_id": "crash-1"}}
    first = FakeChat([decision(["A"]), decision(["B"]), decision(["Journalist"])], fail_for={"B"})
    with pytest.raises(RuntimeError, match="simulated API failure in B"):
        _run_with_saver(cfg, first, rc)
    assert first.count("A") == 1 and len(first.router.calls) == 2

    second = FakeChat([decision(["Journalist"])])          # a fresh process: new models, same file
    usage, status = _run_with_saver(cfg, second, rc, resume=True)
    assert status == "approved"
    assert second.count("A") == 0                           # finished before the crash: not re-run
    assert second.count("B") == 1 and second.count("Journalist") == 1
    assert len(second.router.calls) == 1                    # only the routing step after B
    assert "findings from A" in second.human("Journalist")  # A's findings came from the checkpoint
    # The total covers both processes: 2 routing calls + A before the crash, the rest after it.
    llm_calls = first.count("A") + len(first.router.calls) + len(second.calls) + len(second.router.calls)
    assert usage["total_tokens"] == USAGE["total_tokens"] * llm_calls + 2


def test_hitl_pause_resumes_in_a_new_process(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    from automation import main
    cfg = _sqlite_cfg(tmp_path, hitl={"enabled": True, "checkpoints": ["post_plan"]})
    rc = {"configurable": {"thread_id": "hitl-1"}}
    with patch.object(main, "_handle_hitl_interrupt", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):        # the user closes the terminal at the question
            _run_with_saver(cfg, FakeChat([decision(["A"]), decision(["Journalist"])]), rc)
    second = FakeChat([decision(["Journalist"])])
    with patch.object(main, "_handle_hitl_interrupt", return_value="B") as ask:
        _, status = _run_with_saver(cfg, second, rc, resume=True)
    assert ask.call_args[0][0][0].value["checkpoint"] == "post_plan"
    assert status == "approved"
    assert [n for n, _ in second.calls] == ["B", "Journalist"]   # the plan the user typed on resume
    assert len(second.router.calls) == 1


def test_resuming_an_unknown_or_finished_thread(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    cfg = _sqlite_cfg(tmp_path)
    with pytest.raises(ValueError, match="No saved run found for thread 'nope'"):
        _run_with_saver(cfg, FakeChat([decision(["Journalist"])]), {"configurable": {"thread_id": "nope"}},
                        resume=True)
    rc = {"configurable": {"thread_id": "done"}}
    _run_with_saver(cfg, FakeChat([decision(["Journalist"])]), rc)
    again = FakeChat([decision(["Journalist"])])
    _, status = _run_with_saver(cfg, again, rc, resume=True)
    assert status == "approved" and again.calls == []


def _write_cli_config(tmp_path, checkpointer):
    import yaml
    cfg = make_config()
    cfg["swarm"].update({"output_dir": str(tmp_path / "out"), "traceability_matrix": str(tmp_path / "M.md")})
    cfg["orchestrator"]["on_agent_failure"] = "abort"
    if checkpointer:
        cfg["checkpointer"] = checkpointer
    path = tmp_path / "swarm_config.yml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_execute_prints_the_thread_id_and_resume_continues_it(tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    import re

    from typer.testing import CliRunner

    from automation import main
    monkeypatch.setenv("K", "x")
    path = _write_cli_config(tmp_path, {"type": "sqlite", "path": str(tmp_path / "c.sqlite")})
    chats = iter([FakeChat([decision(["A"]), decision(["Journalist"])], fail_for={"Journalist"}),
                  FakeChat([decision(["Journalist"])])])

    def fake_build(config, checkpointer=None):
        with patch("automation.graph.create_model", return_value=next(chats)), \
             patch("automation.graph.create_reviewer_model", return_value=FakeReviewer()), \
             patch("automation.config.load_persona_content", return_value="persona"), \
             patch("automation.graph.load_tools_for_persona", return_value=[]):
            return build_graph(config, checkpointer=checkpointer)

    runner = CliRunner()
    with patch.object(main, "build_graph", side_effect=fake_build):
        crashed = runner.invoke(main.app, ["execute", "task", "--config", str(path)])
        assert crashed.exit_code == 1
        thread = re.search(r"Thread: (\S+)", crashed.output).group(1)
        assert f"execute --resume {thread}" in crashed.output
        resumed = runner.invoke(main.app, ["execute", "--resume", thread, "--config", str(path)])
    assert resumed.exit_code == 0, resumed.output
    assert f"Thread: {thread}" in resumed.output and "Task complete" in resumed.output
    assert f"### Resumed run {thread}" in (tmp_path / "M.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("checkpointer", [None, {"type": "memory"}])
def test_resume_needs_a_sqlite_checkpointer(tmp_path, monkeypatch, checkpointer):
    from typer.testing import CliRunner

    from automation import main
    monkeypatch.setenv("K", "x")
    path = _write_cli_config(tmp_path, checkpointer)
    result = CliRunner().invoke(main.app, ["execute", "--resume", "abc", "--config", str(path)])
    assert result.exit_code == 1 and "--resume needs a checkpointer" in result.output


def test_execute_without_prompt_or_resume_fails(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from automation import main
    monkeypatch.setenv("K", "x")
    result = CliRunner().invoke(main.app, ["execute", "--config", str(_write_cli_config(tmp_path, None))])
    assert result.exit_code == 1 and "--resume <thread_id>" in result.output


def test_default_checkpointer_is_unchanged():
    from langgraph.checkpoint.memory import MemorySaver

    from automation.config import get_checkpointer_config
    assert get_checkpointer_config(make_config()) == {"type": "none", "path": None}
    hitl = make_config(hitl={"enabled": True, "checkpoints": ["post_plan"]})
    assert get_checkpointer_config(hitl)["type"] == "memory"
    chat = FakeChat([decision(["Journalist"])])
    assert build(make_config(), chat).checkpointer is None
    assert isinstance(build(hitl, chat).checkpointer, MemorySaver)
    memory = make_config()
    memory["checkpointer"] = {"type": "memory"}
    assert isinstance(build(memory, chat).checkpointer, MemorySaver)


def test_sqlite_checkpointer_must_be_opened_by_the_caller(tmp_path):
    cfg = _sqlite_cfg(tmp_path)
    with pytest.raises(ValueError, match="checkpointer.type: sqlite must be opened inside the event loop"):
        build(cfg, FakeChat([decision(["Journalist"])]))
    from automation.config import get_checkpointer_config
    default_path = get_checkpointer_config({**cfg, "checkpointer": {"type": "sqlite"}})["path"]
    assert default_path.name == "checkpoints.sqlite"


def test_missing_persist_extra_names_it(tmp_path, monkeypatch):
    import sys

    from automation.config import open_checkpointer
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.sqlite.aio", None)

    async def go():
        async with open_checkpointer(_sqlite_cfg(tmp_path)):
            pass
    with pytest.raises(RuntimeError, match=r"murmuration\[persist\]"):
        asyncio.run(go())


def test_doctor_checks_the_checkpointer(tmp_path, monkeypatch):
    import sys

    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    def issues(block):
        real["checkpointer"] = block
        (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
        return [(i.severity, i.message) for i in inspect_swarm(tmp_path).issues if "checkpointer" in i.message]

    assert any(s == "warning" and "cannot be resumed" in m for s, m in issues({"type": "memory"}))
    assert any(s == "error" and "points to a directory" in m
               for s, m in issues({"type": "sqlite", "path": str(tmp_path)}))
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.sqlite.aio", None)
    assert any(s == "error" and "persist" in m for s, m in issues({"type": "sqlite"}))


def test_parallel_crash_resume_reruns_only_the_failed_branch_and_logs_no_replays(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    names = ("A", "B", "C")
    cfg = _sqlite_cfg(tmp_path, specialists=names)
    cfg["swarm"]["run_log"] = str(tmp_path / "run.jsonl")
    rc = {"configurable": {"thread_id": "fan-1"}}
    plan = decision(list(names), assignments={n: n for n in names})
    first = FakeChat([plan, decision(["Journalist"])], fail_for={"B"})
    with pytest.raises(RuntimeError, match="simulated API failure in B"):
        _run_with_saver(cfg, first, rc)
    second = FakeChat([decision(["Journalist"])])
    usage, status = _run_with_saver(cfg, second, rc, resume=True)
    assert status == "approved" and [n for n, _ in second.calls] == ["B", "Journalist"]
    lines = _read_jsonl(tmp_path / "run.jsonl")
    after = lines[[line["event"] for line in lines].index("run_resume"):]
    nodes = [line["node"] for line in after if line["event"] == "node"]
    assert nodes == ["B", "orchestrator", "Journalist", "reviewer"]     # A and C are not replayed
    assert after[-1]["token_usage"] == usage


def test_resume_with_a_config_that_no_longer_builds_the_saved_step_fails_clearly(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    from automation import main
    cfg = _sqlite_cfg(tmp_path, hitl={"enabled": True, "checkpoints": ["post_plan"]})
    rc = {"configurable": {"thread_id": "edited"}}
    with patch.object(main, "_handle_hitl_interrupt", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            _run_with_saver(cfg, FakeChat([decision(["A"])]), rc)
    cfg["hitl"] = {"enabled": False, "checkpoints": []}
    with pytest.raises(main.ResumeError, match="does not build"):
        _run_with_saver(cfg, FakeChat([decision(["Journalist"])]), rc, resume=True)


def test_cli_resume_keeps_the_saved_task_and_unknown_threads_have_no_side_effects(tmp_path, monkeypatch):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    import json
    import re

    from typer.testing import CliRunner

    from automation import main
    monkeypatch.setenv("K", "x")
    path = _write_cli_config(tmp_path, {"type": "sqlite", "path": str(tmp_path / "c.sqlite")})
    chats = iter([FakeChat([decision(["Journalist"])], fail_for={"Journalist"}),
                  FakeChat([decision(["Journalist"])]), FakeChat([decision(["Journalist"])])])

    def fake_build(config, checkpointer=None):
        with patch("automation.graph.create_model", return_value=next(chats)), \
             patch("automation.graph.create_reviewer_model", return_value=FakeReviewer()), \
             patch("automation.config.load_persona_content", return_value="persona"), \
             patch("automation.graph.load_tools_for_persona", return_value=[]):
            return build_graph(config, checkpointer=checkpointer)

    runner = CliRunner()
    with patch.object(main, "build_graph", side_effect=fake_build):
        crashed = runner.invoke(main.app, ["execute", "the saved task", "--config", str(path)])
        thread = re.search(r"Thread: (\S+)", crashed.output).group(1)
        matrix_before = (tmp_path / "M.md").read_text(encoding="utf-8")
        unknown = runner.invoke(main.app, ["execute", "--resume", "no-such-thread", "--config", str(path)])
        assert unknown.exit_code == 1 and "No saved run found" in unknown.output
        assert "--resume no-such-thread" not in unknown.output
        assert (tmp_path / "M.md").read_text(encoding="utf-8") == matrix_before
        resumed = runner.invoke(main.app, ["execute", "--resume", thread, "--config", str(path)])
    assert resumed.exit_code == 0, resumed.output
    metrics = json.loads((tmp_path / "out" / "run_metrics.json").read_text(encoding="utf-8"))
    assert metrics["task"] == "the saved task"
    assert f"### Resumed run {thread}" in (tmp_path / "M.md").read_text(encoding="utf-8")
    assert "Task: the saved task" in (tmp_path / "M.md").read_text(encoding="utf-8").split("Resumed run")[1]


def test_default_sqlite_path_is_outside_output_dir_and_doctor_warns_otherwise(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH, get_checkpointer_config, resolve_swarm_path
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["checkpointer"] = {"type": "sqlite"}
    path = get_checkpointer_config(real)["path"]
    assert path.parent.name == ".murmuration"
    assert resolve_swarm_path(real, "output_dir") not in path.parents
    real["checkpointer"]["path"] = "./Drafts/state.sqlite"
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert any("inside swarm.output_dir" in i.message for i in inspect_swarm(tmp_path).issues)


# ── 15. Early stop and boundary-aware truncation (I4, I6) ──────────────────


def stop_decision(agents, coverage="covers X; Y still open"):
    return OrchestratorDecision(reasoning="enough", next_agents=agents, instructions="go",
                                sufficient_evidence=True, coverage=coverage)


def _early_stop_cfg(enabled=True, min_rounds=1):
    cfg = make_config()
    cfg["orchestrator"]["early_stop"] = {"enabled": enabled, "min_rounds": min_rounds}
    return cfg


def test_early_stop_hands_over_to_the_journalist_once_evidence_is_sufficient():
    chat = FakeChat([decision(["A"]), stop_decision(["B"])])
    state = run(build(_early_stop_cfg(), chat))
    assert chat.count("B") == 0 and chat.count("Journalist") == 1
    assert "EVIDENCE COVERAGE (orchestrator's assessment):\ncovers X; Y still open" in chat.human("Journalist")
    assert "early stop: evidence judged sufficient after 1 round(s); skipped ['B']" in state["messages"][2].content
    prompt = next(m for m in chat.router.calls[0] if isinstance(m, HumanMessage)).content
    assert "EARLY STOP" in prompt and "at least 1 specialist round(s)" in prompt


def test_early_stop_waits_for_min_rounds():
    chat = FakeChat([stop_decision(["A"]), stop_decision(["B"]), decision(["Journalist"])])
    state = run(build(_early_stop_cfg(min_rounds=2), chat))
    assert chat.count("A") == 1 and chat.count("B") == 1       # round 1 ran despite the claim
    assert "early_stop.min_rounds is 2, so continuing" in state["messages"][0].content
    assert state["specialist_rounds"] == 2


def test_early_stop_is_ignored_unless_enabled():
    chat = FakeChat([decision(["A"]), stop_decision(["B"]), decision(["Journalist"])])
    run(build(_early_stop_cfg(enabled=False), chat))
    assert chat.count("B") == 1
    prompt = next(m for m in chat.router.calls[0] if isinstance(m, HumanMessage)).content
    assert "EARLY STOP" not in prompt and "EVIDENCE COVERAGE" not in chat.human("Journalist")


def test_peer_and_orchestrator_views_end_at_a_sentence_boundary():
    cfg = make_config()
    cfg["orchestrator"].update({"max_peer_chars": 60, "max_findings_chars": 60})
    long = "Alpha finding is solid. Beta finding needs care. Gamma finding is speculative and long."
    chat = FakeChat([decision(["A"]), decision(["B"]), decision(["Journalist"])])
    graph = build(cfg, chat)
    state = initial_state()
    state["agent_outputs"] = {"Prior": long}
    run(graph, state)
    expected = "### [Prior]\nAlpha finding is solid. Beta finding needs care. …"
    assert expected in chat.human("A")
    assert expected in next(m for m in chat.router.calls[0] if isinstance(m, HumanMessage)).content
    assert long in chat.human("Journalist")                    # the Journalist still reads everything


def test_truncate_prefers_paragraphs_then_sentences_then_words():
    from automation.graph import _truncate
    assert _truncate("short", 10) == "short"
    assert _truncate("Para one is here.\n\nPara two goes on", 30) == "Para one is here. …"
    assert _truncate("One two three four five six", 20) == "One two three four …"
    assert _truncate("x" * 50, 10) == "x" * 10 + "…"           # no boundary: hard cut, as before
    assert _truncate("ab " + "y" * 40, 20) == "ab " + "y" * 17 + "…"   # boundary below half the budget


def test_doctor_warns_when_early_stop_can_never_apply(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"]["early_stop"] = {"enabled": True, "min_rounds": 20}
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert any("early stop can never apply" in i.message for i in inspect_swarm(tmp_path).issues)


def test_early_stop_does_not_pass_the_skipped_specialists_instructions_to_the_journalist():
    skipped = OrchestratorDecision(reasoning="enough", next_agents=["B"], instructions="Search for dosing trials",
                                   sufficient_evidence=True, coverage="dosing covered")
    chat = FakeChat([decision(["A"]), skipped])
    run(build(_early_stop_cfg(), chat))
    brief = chat.human("Journalist")
    assert "Search for dosing trials" not in brief
    assert "Synthesise all collected findings" in brief and "dosing covered" in brief


def test_coverage_note_is_kept_when_the_orchestrator_routes_to_the_journalist_itself():
    chat = FakeChat([stop_decision(["Journalist"], coverage="all covered")])
    run(build(_early_stop_cfg(min_rounds=3), chat))
    assert "EVIDENCE COVERAGE (orchestrator's assessment):\nall covered" in chat.human("Journalist")
    assert "INSTRUCTIONS FROM THE ORCHESTRATOR:\ngo" in chat.human("Journalist")


def test_post_plan_override_keeps_specialist_rounds_in_step():
    out = post_plan("A", ("A", "B"), planned_state(["Journalist"], call_count=0))
    assert out["specialist_rounds"] == 1
    state = planned_state(["A"])
    state["specialist_rounds"] = 1
    assert post_plan("Journalist", ("A", "B"), state)["specialist_rounds"] == 0


def test_truncate_handles_limits_crlf_and_tabs():
    from automation.graph import _truncate
    assert _truncate("Alpha. Beta gamma delta. More text here", 24) == "Alpha. Beta gamma delta. …"
    assert _truncate("Para one is here.\r\n\r\nPara two goes on", 30) == "Para one is here. …"
    assert _truncate("word\tword\tword\tword", 12) == "word\tword …"
    assert _truncate("ab cd", 2) == "ab…"                   # a cut at position 0 is never used


def test_doctor_warns_when_min_rounds_equals_max_agent_calls(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["orchestrator"].update({"max_agent_calls": 2, "early_stop": {"enabled": True, "min_rounds": 2}})
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert any("early stop can never apply" in i.message for i in inspect_swarm(tmp_path).issues)


# ── 16. Reviewer evidence loop, verdict policy and context (I1, R2, R10) ───


class StructuredReviewer(FakeReviewer):
    """Reviewer whose structured-output view replays ReviewerVerdicts (None = unparseable reply)."""

    def __init__(self, verdicts, text_replies=("APPROVED",)):
        super().__init__(text_replies)
        self.verdicts = list(verdicts)
        self.structured_calls = []

    def with_structured_output(self, schema, **kwargs):
        reviewer = self

        class _View:
            def invoke(self, msgs):
                reviewer.structured_calls.append(msgs)
                v = reviewer.verdicts.pop(0) if len(reviewer.verdicts) > 1 else reviewer.verdicts[0]
                return {"raw": AIMessage(content="", usage_metadata=USAGE), "parsed": v,
                        "parsing_error": None if v is not None else ValueError("bad json")}
        return _View()


def verdict(ok, issues=(), missing=()):
    from automation.graph import EvidenceRequest, ReviewerVerdict
    return ReviewerVerdict(
        verdict="APPROVED" if ok else "REJECTED", style_issues=list(issues),
        missing_evidence=[EvidenceRequest(claim=c, reason=r, suggested_agent=a) for c, r, a in missing],
    )


def _reviewer_cfg(**reviewer):
    cfg = make_config()
    cfg["reviewer"].update(reviewer)
    return cfg


def test_rejection_with_missing_evidence_goes_back_to_the_orchestrator():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    chat = FakeChat([decision(["Journalist"]), decision(["A"], "check the dose claim"), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, ["tone"], [("dose X is safe", "no source", "A")]),
                                   verdict(True)])
    graph = build(cfg, chat, reviewer)
    assert node_sequence(graph) == ["orchestrator", "Journalist", "reviewer", "orchestrator", "A",
                                    "orchestrator", "Journalist", "reviewer"]
    chat = FakeChat([decision(["Journalist"]), decision(["A"], "check the dose claim"), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, ["tone"], [("dose X is safe", "no source", "A")]),
                                   verdict(True)])
    state = run(build(cfg, chat, reviewer))
    assert state["final_status"] == "approved" and state["evidence_loop_count"] == 1
    routing = next(m for m in chat.router.calls[1] if isinstance(m, HumanMessage)).content
    assert "REVIEWER REQUESTS" in routing and "• dose X is safe — no source (suggested: A)" in routing
    # Still shown while the round is open (after A returns), cleared once it hands back to the Journalist.
    assert "REVIEWER REQUESTS" in next(m for m in chat.router.calls[2] if isinstance(m, HumanMessage)).content
    assert state["review_route"] == "journalist" and state["reviewer_requests"] == []
    revision = chat.human("Journalist", 1)
    assert "REVISION INSTRUCTIONS FROM REVIEWER-2:\nREJECTED:\n- tone\n- Missing evidence: dose X is safe" in revision
    assert "check the dose claim" not in revision.split("REVISION INSTRUCTIONS")[1]


def test_auto_route_uses_the_orchestrator_only_for_missing_evidence_and_within_the_loop_budget():
    cfg = _reviewer_cfg(on_rejection="auto", max_evidence_loops=1)
    chat = FakeChat([decision(["Journalist"]), decision(["A"]), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, ["style only"]),                       # → Journalist
                                   verdict(False, [], [("c1", "r1", None)]),             # → orchestrator
                                   verdict(False, [], [("c2", "r2", None)]),             # budget spent → Journalist
                                   verdict(True)])
    seq = node_sequence(build(cfg, chat, reviewer))
    assert seq == ["orchestrator", "Journalist", "reviewer", "Journalist", "reviewer", "orchestrator", "A",
                   "orchestrator", "Journalist", "reviewer", "Journalist", "reviewer"]


def test_unknown_suggested_agent_is_ignored():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    chat = FakeChat([decision(["Journalist"]), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "Ghost")]), verdict(True)])
    state = run(build(cfg, chat, reviewer))
    from automation.graph import _evidence_requests
    assert _evidence_requests(verdict(False, [], [("c", "r", "Ghost"), ("d", "s", "A")]), ["A", "B"]) == [
        {"claim": "c", "reason": "r", "suggested_agent": None}, {"claim": "d", "reason": "s", "suggested_agent": "A"}]
    assert state["final_status"] == "approved"
    routing = next(m for m in chat.router.calls[1] if isinstance(m, HumanMessage)).content
    assert "• c — r" in routing and "Ghost" not in routing


def test_unparseable_structured_verdict_falls_back_to_the_text_protocol():
    cfg = _reviewer_cfg(on_rejection="auto", custom_prompt="Reply APPROVED or REJECTED: reasons.")
    chat = FakeChat([decision(["Journalist"])])
    reviewer = StructuredReviewer([None], text_replies=["REJECTED: weak", "APPROVED"])
    state = run(build(cfg, chat, reviewer))
    assert state["final_status"] == "approved" and len(reviewer.calls) == 2      # two text calls
    assert len(reviewer.structured_calls) == 2                                   # each after a structured try
    assert "Be strict" not in reviewer.calls[0][0].content                      # custom prompt used verbatim
    assert reviewer.calls[0][0].content == "Reply APPROVED or REJECTED: reasons."
    assert "REJECTED: weak" in chat.human("Journalist", 1)
    assert state["token_usage"]["total_tokens"] > 0


def test_default_reviewer_keeps_the_text_protocol_and_the_draft_only():
    chat = FakeChat([decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False)], text_replies=["APPROVED"])
    run(build(make_config(), chat, reviewer))
    assert reviewer.structured_calls == []
    assert reviewer.calls[0][1].content == "findings from Journalist #1"


def test_on_max_revisions_fail_reviews_the_last_draft_and_fails_the_run():
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: never good enough"])
    state = run(build(_reviewer_cfg(max_revision_loops=2, on_max_revisions="fail"), chat, reviewer))
    assert len(reviewer.calls) == 3            # the third draft is read, not force-approved
    assert state["final_status"] == "failed" and state["reviewer_approved"] is False
    chat = FakeChat([decision(["Journalist"])])
    reviewer = FakeReviewer(["REJECTED: a", "REJECTED: b", "APPROVED"])
    state = run(build(_reviewer_cfg(max_revision_loops=2, on_max_revisions="fail"), chat, reviewer))
    assert state["final_status"] == "approved"


def test_reviewer_context_adds_the_task_and_this_runs_matrix_rows(tmp_path):
    matrix = tmp_path / "M.md"
    matrix.write_text(MATRIX_HEADER + "\n### Run: old | Task: t\n\n| *old* | **A** | stale | m | **[FACT]** |\n"
                      "\n### Run: now | Task: t\n\n| *s1* | **A** | fresh claim | m | **[FACT]** |\n"
                      "| *s2* | **B** | second claim | m | **[FACT]** |\n", encoding="utf-8")
    cfg = _reviewer_cfg(context=["draft", "task", "matrix"], matrix_excerpt_rows=1)
    cfg["swarm"]["traceability_matrix"] = str(matrix)
    reviewer = FakeReviewer(["APPROVED"])
    run(build(cfg, FakeChat([decision(["Journalist"])]), reviewer), initial_state("the task"))
    sent = reviewer.calls[0][1].content
    assert sent.startswith("RESEARCH TASK:\nthe task\n\nDRAFT TO REVIEW:\nfindings from Journalist #1")
    assert "second claim" in sent and "fresh claim" not in sent and "stale" not in sent


def test_hitl_on_rejection_works_with_the_orchestrator_route():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    cfg["hitl"] = {"enabled": True, "checkpoints": ["on_rejection", "pre_journalist"]}
    chat = FakeChat([decision(["Journalist"]), decision(["A"]), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "A")]), verdict(True)])
    graph = build(cfg, chat, reviewer)
    rc = {"configurable": {"thread_id": "rev-orch"}}
    paused = run(graph, config=rc)
    assert paused["__interrupt__"][0].value["checkpoint"] == "pre_journalist"
    paused = resume(graph, "", rc)
    question = paused["__interrupt__"][0].value
    assert question["checkpoint"] == "on_rejection" and "the orchestrator" in question["question"]
    final = resume(graph, "Focus on the dose table", rc)            # custom text, still via the orchestrator
    assert final["final_status"] == "approved" and chat.count("A") == 1
    routing = next(m for m in chat.router.calls[1] if isinstance(m, HumanMessage)).content
    assert "Focus on the dose table" in routing
    assert "Focus on the dose table" in chat.human("Journalist", 1)
    # pre_journalist is asked once: the evidence round goes straight back to the Journalist.
    assert len(chat.router.calls) == 3


def test_hitl_override_ends_an_orchestrator_routed_rejection():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    cfg["hitl"] = {"enabled": True, "checkpoints": ["on_rejection"]}
    chat = FakeChat([decision(["Journalist"])])
    graph = build(cfg, chat, StructuredReviewer([verdict(False, [], [("c", "r", None)])]))
    rc = {"configurable": {"thread_id": "rev-ovr"}}
    run(graph, config=rc)
    assert resume(graph, "OVERRIDE", rc)["final_status"] == "overridden" and len(chat.router.calls) == 1


def test_recursion_limit_covers_the_worst_case_evidence_loop():
    from automation.graph import compute_recursion_limit
    cfg = _reviewer_cfg(on_rejection="orchestrator", max_evidence_loops=2, max_revision_loops=3)
    cfg["orchestrator"]["max_agent_calls"] = 2
    chat = FakeChat([decision(["A"])])                        # always asks for more evidence
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "A")])])
    steps = len(node_sequence(build(cfg, chat, reviewer), config={"recursion_limit": compute_recursion_limit(cfg)}))
    assert steps < compute_recursion_limit(cfg)
    assert compute_recursion_limit(cfg) == compute_recursion_limit(_reviewer_cfg(max_revision_loops=3) | {
        "orchestrator": cfg["orchestrator"]}) + 4


def test_doctor_flags_reviewer_routing_that_cannot_take_effect(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["reviewer"].update({"on_rejection": "auto", "max_evidence_loops": 0, "context": ["draft", "matrix"]})
    for persona in real["personas"]:
        persona["tools"] = [t for t in persona["tools"] if t != "append_traceability"] or ["search_web"]
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    messages = [i.message for i in inspect_swarm(tmp_path).issues]
    assert any("max_evidence_loops is 0" in m for m in messages)
    assert any("reviewer.context includes matrix" in m for m in messages)


def test_early_stop_does_not_cancel_an_evidence_round():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    cfg["orchestrator"]["early_stop"] = {"enabled": True, "min_rounds": 1}
    chat = FakeChat([decision(["A"]), decision(["Journalist"]), stop_decision(["B"]), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "B")]), verdict(True)])
    run(build(cfg, chat, reviewer))
    assert chat.count("B") == 1


def test_no_evidence_round_once_the_specialist_budget_is_spent():
    cfg = _reviewer_cfg(on_rejection="orchestrator")
    cfg["orchestrator"]["max_agent_calls"] = 1
    chat = FakeChat([decision(["A"]), decision(["Journalist"])])
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "A")]), verdict(True)])
    seq = node_sequence(build(cfg, chat, reviewer))
    assert seq == ["orchestrator", "A", "orchestrator", "Journalist", "reviewer", "Journalist", "reviewer"]


def test_unparseable_structured_reply_in_text_form_needs_no_second_call():
    class RawReviewer(StructuredReviewer):
        def with_structured_output(self, schema, **kwargs):
            reviewer = self

            class _View:
                def invoke(self, msgs):
                    reviewer.structured_calls.append(msgs)
                    return {"raw": AIMessage(content="**APPROVED**", usage_metadata=USAGE), "parsed": None,
                            "parsing_error": ValueError("not json")}
            return _View()

    reviewer = RawReviewer([None])
    state = run(build(_reviewer_cfg(on_rejection="auto"), FakeChat([decision(["Journalist"])]), reviewer))
    assert state["final_status"] == "approved" and reviewer.calls == [] and len(reviewer.structured_calls) == 1


def test_structured_call_exception_falls_back_to_text():
    class Exploding(StructuredReviewer):
        def with_structured_output(self, schema, **kwargs):
            class _View:
                def invoke(self, msgs):
                    raise RuntimeError("provider rejected the schema")
            return _View()

    reviewer = Exploding([None], text_replies=["APPROVED"])
    state = run(build(_reviewer_cfg(on_rejection="orchestrator"), FakeChat([decision(["Journalist"])]), reviewer))
    assert state["final_status"] == "approved" and len(reviewer.calls) == 1


def test_matrix_excerpt_keeps_rows_logged_before_a_resume(tmp_path):
    from automation.graph import _matrix_excerpt
    matrix = tmp_path / "M.md"
    matrix.write_text(MATRIX_HEADER + "\n### Run: old | Task: t\n\n| *o* | **A** | stale | m | **[FACT]** |\n"
                      "\n### Run: now | Task: t\n\n| *s1* | **A** | before crash | m | **[FACT]** |\n"
                      "\n### Resumed run th-1: now | Task: t\n\n| *s2* | **B** | after resume | m | **[FACT]** |\n",
                      encoding="utf-8")
    excerpt = _matrix_excerpt({"swarm": {"traceability_matrix": str(matrix)}}, 50)
    assert "before crash" in excerpt and "after resume" in excerpt and "stale" not in excerpt


def test_default_on_rejection_question_wording_is_unchanged():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["on_rejection"]})
    graph = build(cfg, FakeChat([decision(["Journalist"])]), FakeReviewer(["REJECTED: x"]))
    paused = run(graph, config={"configurable": {"thread_id": "wording"}})
    question = paused["__interrupt__"][0].value["question"]
    assert "REVISE   — send back to Journalist with reviewer feedback (default)" in question
    assert "Or type your own revision instructions for the Journalist:" in question


def test_run_log_records_the_reason_of_a_final_review_failure(tmp_path):
    from automation.main import _run_graph_async
    cfg = _reviewer_cfg(max_revision_loops=1, on_max_revisions="fail")
    cfg["swarm"]["run_log"] = str(tmp_path / "r.jsonl")
    graph = build(cfg, FakeChat([decision(["Journalist"])]), FakeReviewer(["REJECTED: still wrong"]))
    asyncio.run(_run_graph_async(cfg, "task", graph, {}))
    last_review = [line for line in _read_jsonl(tmp_path / "r.jsonl") if line.get("node") == "reviewer"][-1]
    assert last_review["verdict"] == "failed" and last_review["feedback"] == "REJECTED: still wrong"


def test_doctor_ignores_an_orchestrator_logger_whose_tools_are_not_loaded(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["reviewer"]["context"] = ["draft", "matrix"]
    for persona in real["personas"]:
        if persona["name"] != real["orchestrator"]["agent"]:
            persona["tools"] = [t for t in persona["tools"] if t != "append_traceability"] or ["search_web"]
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert any("reviewer.context includes matrix" in i.message for i in inspect_swarm(tmp_path).issues)
    real["orchestrator"]["allow_tools"] = True
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert not any("reviewer.context includes matrix" in i.message for i in inspect_swarm(tmp_path).issues)


def test_recursion_limit_covers_journalist_first_evidence_rounds_with_hitl():
    """Worst case: draft first, then every rejection triggers an evidence round, fail mode, all checkpoints."""
    from automation.graph import compute_recursion_limit
    cfg = _reviewer_cfg(on_rejection="orchestrator", max_evidence_loops=2, max_revision_loops=3,
                        on_max_revisions="fail")
    cfg["orchestrator"]["max_agent_calls"] = 4
    cfg["hitl"] = {"enabled": True, "checkpoints": ["post_plan", "pre_journalist", "on_rejection"]}
    plan = [decision(["Journalist"])] + [decision(["A"]), decision(["Journalist"])] * 2
    chat = FakeChat(plan)
    reviewer = StructuredReviewer([verdict(False, [], [("c", "r", "A")])])
    graph = build(cfg, chat, reviewer)
    rc = {"configurable": {"thread_id": "worst"}, "recursion_limit": compute_recursion_limit(cfg)}

    async def go():
        seq, command = [], initial_state()
        while True:
            paused = False
            async for ev in graph.astream(command, config=rc, stream_mode="updates"):
                seq.extend(k for k in ev if k != "__interrupt__")
                if "__interrupt__" in ev:
                    paused = True
            if not paused:
                return seq
            command = Command(resume="")
    seq = asyncio.run(go())
    assert seq.count("orchestrator") == 5 and seq.count("reviewer") == 4
    assert len(seq) < compute_recursion_limit(cfg)


# ── 17. Structured findings, disagreements and critique round (I2) ─────────


class FindingsChat(FakeChat):
    """FakeChat whose structured-output view also serves SpecialistFindings.

    ``reports`` maps persona → list of SpecialistFindings (one per call, last one repeats);
    None in the list means an unparseable reply.
    """

    def __init__(self, decisions, reports, **kwargs):
        super().__init__(decisions, **kwargs)
        self.reports = {k: list(v) for k, v in reports.items()}
        self.findings_calls = []

    def with_structured_output(self, schema, **kwargs):
        from automation.graph import SpecialistFindings
        if schema is not SpecialistFindings:
            return super().with_structured_output(schema, **kwargs)
        chat = self

        class _View:
            async def ainvoke(self, msgs, config=None):
                name = chat.persona(msgs)
                chat.findings_calls.append((name, msgs))
                queue = chat.reports.get(name) or [None]
                report = queue.pop(0) if len(queue) > 1 else queue[0]
                return {"raw": AIMessage(content="", usage_metadata=USAGE), "parsed": report,
                        "parsing_error": None if report is not None else ValueError("bad json")}
        return _View()


def report(narrative, *claims):
    from automation.graph import Claim, SpecialistFindings
    return SpecialistFindings(narrative=narrative, findings=[
        Claim(text=t, epistemic_tag=tag, sources=list(src), confidence=conf, contradicts=list(contra))
        for t, tag, src, conf, contra in claims
    ])


def _collab_cfg(tmp_path=None, **collab):
    cfg = make_config()
    cfg["epistemic_tags"] = ["[FACT]", "[INFERENCE]"]
    cfg["collaboration"] = {"structured_findings": True, **collab}
    if tmp_path is not None:
        matrix = tmp_path / "M.md"
        matrix.write_text(MATRIX_HEADER + "\n### Run: now | Task: t\n\n", encoding="utf-8")
        cfg["swarm"]["traceability_matrix"] = str(matrix)
    return cfg


def a_report():
    return report("A found a dose effect.",
                  ("Dose X lowers Y by 20%", "fact", ["PMID:1"], 0.9, []),
                  ("Effect may be sex-specific", "Hunch", [], 1.7, ["nonexistent.1.1"]))


def test_structured_findings_become_claims_with_ids_in_state_and_the_matrix(tmp_path):
    cfg = _collab_cfg(tmp_path)
    chat = FindingsChat([decision(["A"]), decision(["Journalist"])], {"A": [a_report()]})
    state = run(build(cfg, chat))
    assert sorted(state["claims"]) == ["a.1.1", "a.1.2"]
    first, second = state["claims"]["a.1.1"], state["claims"]["a.1.2"]
    assert first["epistemic_tag"] == "[FACT]" and first["sources"] == ["PMID:1"] and first["agent"] == "A"
    assert second["epistemic_tag"] == "[UNTAGGED]" and second["confidence"] == 1.0
    assert second["contradicts"] == []                       # unknown ids are dropped
    assert "CLAIMS:\n- [a.1.1] [FACT] (confidence 0.90) Dose X lowers Y by 20%" in state["agent_outputs"]["A"]
    rows = _matrix_rows(tmp_path / "M.md")
    assert any("**A**" in r and "[a.1.1] Dose X lowers Y by 20%" in r and "PMID:1" in r for r in rows)
    assert any("Structured finding (confidence 0.90)" in r for r in rows)
    prompt = next(m for m in chat.findings_calls[0][1] if isinstance(m, HumanMessage)).content
    assert "one of: [FACT], [INFERENCE]" in prompt and "YOUR REPORT:\nfindings from A" in prompt


def test_peers_and_orchestrator_see_claims_not_a_text_slice(tmp_path):
    cfg = _collab_cfg(tmp_path)
    chat = FindingsChat([decision(["A"]), decision(["B"]), decision(["Journalist"])], {"A": [a_report()],
                        "B": [report("B agrees.")]})
    run(build(cfg, chat))
    peer_view = chat.human("B")
    assert "### [A]\n- [a.1.1] [FACT] (confidence 0.90) Dose X lowers Y by 20% — sources: PMID:1" in peer_view
    assert "A found a dose effect." not in peer_view
    routing = next(m for m in chat.router.calls[1] if isinstance(m, HumanMessage)).content
    assert "- [a.1.1] [FACT]" in routing
    journalist = chat.human("Journalist")
    assert "findings from A #1\n\nCLAIMS:\n- [a.1.1]" in journalist and "CLAIM IDS:" in journalist


def test_contradictions_are_surfaced_to_the_orchestrator_and_the_journalist(tmp_path):
    cfg = _collab_cfg(tmp_path, surface_disagreements=True)
    b_report = report("B disagrees.", ("Dose X has no effect on Y", "fact", ["PMID:2"], 0.6, ["a.1.1"]))
    chat = FindingsChat([decision(["A"]), decision(["B"]), decision(["Journalist"])],
                        {"A": [a_report()], "B": [b_report]})
    state = run(build(cfg, chat))
    assert state["claims"]["b.2.1"]["contradicts"] == ["a.1.1"]
    routing = next(m for m in chat.router.calls[2] if isinstance(m, HumanMessage)).content
    assert "DISAGREEMENTS BETWEEN SPECIALISTS" in routing and "disputes [a.1.1] A: Dose X lowers Y" in routing
    journalist = chat.human("Journalist")
    assert "Include a 'Points of disagreement' section" in journalist and "[b.2.1] B:" in journalist
    rows = _matrix_rows(tmp_path / "M.md")
    assert any("contradicts a.1.1" in r for r in rows)


def test_critique_round_runs_once_before_the_journalist_and_respects_the_call_budget(tmp_path):
    cfg = _collab_cfg(tmp_path, surface_disagreements=True, critique_round=True)
    b_report = report("B found it too.", ("Y falls with dose X", "fact", ["PMID:3"], 0.7, []))
    objection = report("Objection.", ("Study of a.1.1 was underpowered", "inference", [], 0.5, ["a.1.1"]))
    chat = FindingsChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"]),
                         decision(["Journalist"])], {"A": [a_report(), report("none")], "B": [b_report, objection]})
    seq = node_sequence(build(cfg, chat))
    assert seq.count("orchestrator") == 3 and seq.count("Journalist") == 1
    assert chat.count("A") == 2 and chat.count("B") == 2
    assert "CRITIQUE ROUND" in chat.human("B", 1) and "- [a.1.1]" in chat.human("B", 1)
    chat = FindingsChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"]),
                         decision(["Journalist"])], {"A": [a_report(), report("none")], "B": [b_report, objection]})
    state = run(build(cfg, chat))
    assert state["critique_done"] is True and "b.2.1" in state["claims"]
    assert "disputes [a.1.1]" in chat.human("Journalist")
    # With the call budget already spent there is no critique round.
    cfg["orchestrator"]["max_agent_calls"] = 2
    chat = FindingsChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])],
                        {"A": [a_report()], "B": [b_report]})
    run(build(cfg, chat))
    assert chat.count("A") == 1 and chat.count("B") == 1


def test_unparseable_structured_findings_fall_back_to_the_text_report(tmp_path):
    cfg = _collab_cfg(tmp_path)
    chat = FindingsChat([decision(["A"]), decision(["Journalist"])], {"A": [None]})
    state = run(build(cfg, chat))
    assert state.get("claims", {}) == {} and state["agent_outputs"]["A"] == "findings from A #1"
    assert any("structured findings unavailable" in m.content for m in state["messages"])
    assert not any("Structured finding" in r for r in _matrix_rows(tmp_path / "M.md"))


def test_reviewer_checks_cited_claim_ids_against_this_runs_claims(tmp_path):
    cfg = _collab_cfg(tmp_path)
    cfg["reviewer"]["context"] = ["draft", "matrix"]

    class CitingChat(FindingsChat):
        async def ainvoke(self, msgs, _bound=False, **kwargs):
            if self.persona(msgs) == "Journalist":
                self.calls.append(("Journalist", msgs))
                return AIMessage(content="Y falls [a.1.1, b.9.9] and Z rises [zz.9.9]; see section [1.2.3].",
                                 usage_metadata=USAGE)
            return await super().ainvoke(msgs, _bound=_bound)

    reviewer = FakeReviewer(["APPROVED"])
    run(build(cfg, CitingChat([decision(["A"]), decision(["Journalist"])], {"A": [a_report()]}), reviewer))
    sent = reviewer.calls[0][1].content
    # Grouped ids are split; zz.9.9 and 1.2.3 do not start with a specialist's slug, so they are not ids.
    assert "CLAIM-ID CHECK: the draft cites 2 claim id(s); not found among this run's claims: b.9.9." in sent
    assert "[a.1.1] Dose X lowers Y by 20%" in sent             # the matrix excerpt carries the ids


def test_collaboration_is_off_by_default():
    chat = FindingsChat([decision(["A"]), decision(["Journalist"])], {"A": [a_report()]})
    state = run(build(make_config(), chat))
    assert chat.findings_calls == [] and not state.get("claims")
    assert "CLAIM IDS" not in chat.human("Journalist")


def test_doctor_warns_when_structured_findings_have_no_tag_vocabulary(tmp_path):
    import yaml

    from automation.builder.doctor import inspect_swarm
    from automation.config import CONFIG_PATH
    real = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    real["collaboration"] = {"structured_findings": True}
    real["epistemic_tags"] = []
    (tmp_path / "swarm_config.yml").write_text(yaml.safe_dump(real), encoding="utf-8")
    assert any("claim tags are not checked" in i.message for i in inspect_swarm(tmp_path).issues)


def test_recursion_limit_covers_a_critique_round(tmp_path):
    from automation.graph import compute_recursion_limit
    cfg = _collab_cfg(tmp_path, critique_round=True)
    cfg["orchestrator"]["max_agent_calls"] = 4      # 2 research calls, 1 critique call, 1 kept back
    cfg["reviewer"]["max_revision_loops"] = 1
    chat = FindingsChat([decision(["A"]), decision(["B"]), decision(["Journalist"])],
                        {"A": [a_report()], "B": [report("b", ("claim", "fact", [], 0.5, []))]})
    graph = build(cfg, chat, FakeReviewer(["REJECTED: n"]))
    seq = node_sequence(graph, config={"recursion_limit": compute_recursion_limit(cfg)})
    assert "A" in seq and seq.count("A") + seq.count("B") == 3
    assert len(seq) < compute_recursion_limit(cfg)


def test_claims_view_keeps_to_the_budget_and_shows_a_newer_claimless_round():
    from automation.graph import _format_claim_views
    claims = {f"a.1.{i}": {"id": f"a.1.{i}", "agent": "A", "round": 1, "text": "x" * 80,
                           "epistemic_tag": "[FACT]", "confidence": 0.5} for i in range(1, 11)}
    outputs = {"A": "[AGENT FAILED] A: Boom\n\n[Earlier findings from A]\nreport\n\nCLAIMS:\n- [a.1.1] …"}
    view = _format_claim_views(outputs, claims, None, 300)
    assert view.startswith("### [A]\nLatest report (no claims):\n[AGENT FAILED] A: Boom")
    assert view.count("- [a.1.") == 2 and "(… 8 more claims not shown)" in view


def test_critique_round_keeps_a_call_for_the_orchestrator_and_is_considered_once(tmp_path):
    cfg = _collab_cfg(tmp_path, critique_round=True)
    cfg["orchestrator"]["max_agent_calls"] = 4
    b_report = report("b", ("Y falls", "fact", [], 0.7, []))
    chat = FindingsChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}),
                         decision(["Journalist"], "WRITE IT AS A MEMO"), decision(["Journalist"], "WRITE IT AS A MEMO")],
                        {"A": [a_report(), report("none")], "B": [b_report, report("none")]})
    state = run(build(cfg, chat))
    assert chat.count("A") == 2 and chat.count("B") == 1     # 4 calls - 2 used - 1 kept back = 1 critique
    assert state["agent_call_count"] == 3 and "safety valve" not in " ".join(m.content for m in state["messages"])
    assert "WRITE IT AS A MEMO" in chat.human("Journalist")
    # Fewer than two specialists with claims at the first hand-over: no critique, and none later.
    chat = FindingsChat([decision(["A"]), decision(["Journalist"])], {"A": [a_report()]})
    state = run(build(_collab_cfg(tmp_path, critique_round=True), chat))
    assert state["critique_done"] is True and chat.count("A") == 1


def test_structured_findings_call_is_inside_the_parallel_limit(tmp_path):
    class SlowFindings(FindingsChat):
        in_flight = peak = 0

        def with_structured_output(self, schema, **kwargs):
            view = super().with_structured_output(schema, **kwargs)
            from automation.graph import SpecialistFindings
            if schema is not SpecialistFindings:
                return view
            chat = self

            class _Slow:
                async def ainvoke(self, msgs, config=None):
                    chat.in_flight += 1
                    chat.peak = max(chat.peak, chat.in_flight)
                    await asyncio.sleep(0.02)
                    chat.in_flight -= 1
                    return await view.ainvoke(msgs, config)
            return _Slow()

    cfg = _collab_cfg(tmp_path, )
    cfg["orchestrator"]["max_parallel_agents"] = 1
    chat = SlowFindings([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])],
                        {"A": [a_report()], "B": [report("b")]})
    run(build(cfg, chat))
    assert chat.peak == 1


# ── 18. PR review follow-ups ───────────────────────────────────────────────


def test_failure_note_is_dropped_but_earlier_successful_rounds_are_kept():
    from automation.graph import FAILED_PREFIX, _combine_findings
    after_failure = _combine_findings("A", "r1", f"{FAILED_PREFIX} A: boom")
    assert _combine_findings("A", after_failure, "r3") == "r3\n\n[Earlier findings from A]\nr1"


def test_specialist_that_fails_between_successes_keeps_its_first_round():
    class FailSecond(FakeChat):
        async def ainvoke(self, msgs, _bound=False, **kwargs):
            if self.persona(msgs) == "A" and self.count("A") == 1:
                self.calls.append(("A", msgs))
                raise RuntimeError("rate limited")
            return await super().ainvoke(msgs, _bound=_bound)

    chat = FailSecond([decision(["A"]), decision(["A"]), decision(["A"]), decision(["Journalist"])])
    state = run(build(make_config(), chat))
    assert "findings from A #1" in state["agent_outputs"]["A"]
    assert "AGENT FAILED" not in state["agent_outputs"]["A"]


def test_matrix_excerpt_follows_the_resumed_thread_not_the_latest_run(tmp_path):
    from automation.graph import _matrix_excerpt
    matrix = tmp_path / "M.md"
    matrix.write_text(MATRIX_HEADER
                      + "\n### Run: 10:00 | Thread: run-a | Task: a\n\n| *s* | **A** | A before crash | m | **[FACT]** |\n"
                      + "\n### Run: 10:05 | Thread: run-b | Task: b\n\n| *s* | **B** | B's evidence | m | **[FACT]** |\n"
                      + "\n### Resumed run run-a: 10:10 | Task: a\n\n| *s* | **A** | A after resume | m | **[FACT]** |\n",
                      encoding="utf-8")
    cfg = {"swarm": {"traceability_matrix": str(matrix)}}
    excerpt = _matrix_excerpt(cfg, 50, "run-a")
    assert "A before crash" in excerpt and "A after resume" in excerpt and "B's evidence" not in excerpt
    assert "B's evidence" in _matrix_excerpt(cfg, 50, "run-b") and "A after" not in _matrix_excerpt(cfg, 50, "run-b")
    # Without a thread id: the last run section, as before.
    assert "B's evidence" in _matrix_excerpt(cfg, 50) and "A before crash" not in _matrix_excerpt(cfg, 50)


def test_run_separator_records_the_thread_id(tmp_path):
    from automation.main import _ensure_matrix_header
    cfg = {"swarm": {"traceability_matrix": str(tmp_path / "M.md")}}
    _ensure_matrix_header(cfg, "task one", thread_id="t-1")
    _ensure_matrix_header(cfg, "task two")
    text = (tmp_path / "M.md").read_text(encoding="utf-8")
    assert "| Thread: t-1 | Task: task one" in text
    assert [line for line in text.splitlines() if line.startswith("### Run")][1].endswith("| Task: task two")


def test_reviewer_reads_its_own_threads_matrix_rows(tmp_path):
    matrix = tmp_path / "M.md"
    matrix.write_text(MATRIX_HEADER
                      + "\n### Run: 10:00 | Thread: mine | Task: t\n\n| *s* | **A** | my row | m | **[FACT]** |\n"
                      + "\n### Run: 10:05 | Thread: other | Task: t\n\n| *s* | **B** | other row | m | **[FACT]** |\n",
                      encoding="utf-8")
    cfg = _reviewer_cfg(context=["draft", "matrix"])
    cfg["swarm"]["traceability_matrix"] = str(matrix)
    cfg["hitl"] = {"enabled": True, "checkpoints": []}
    reviewer = FakeReviewer(["APPROVED"])
    run(build(cfg, FakeChat([decision(["Journalist"])]), reviewer), config={"configurable": {"thread_id": "mine"}})
    sent = reviewer.calls[0][1].content
    assert "my row" in sent and "other row" not in sent
