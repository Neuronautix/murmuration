"""
Offline tests for the orchestration control flow in automation/graph.py.

Written for docs/dev/ORCHESTRATION_AUDIT.md. Every test runs the real compiled
LangGraph with fake chat models — no API keys, no network.

Two kinds of tests live here (findings B1, B3, B6–B10 and R4 from the audit
were fixed in the quick-win pass, so their tests now run as plain regression tests):

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

from automation.graph import AgentAssignment, OrchestratorDecision, build_graph, make_orchestrator_node

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


class FakeChat:
    """Stands in for the base chat model shared by specialists and the Journalist.

    Identifies the calling persona from its system prompt. ``fail_for`` names
    personas whose LLM call raises; ``tool_calls`` controls whether every answer
    requests ``dummy_tool`` (so the tool loop never terminates on its own).
    """

    def __init__(self, decisions, fail_for=(), tool_calls=False):
        self.decisions = decisions
        self.router = None
        self.calls = []  # (persona_name, messages)
        self.fail_for = set(fail_for)
        self.tool_calls = tool_calls
        self.n = 0

    def with_structured_output(self, schema, **kwargs):
        self.router = ScriptedRouter(self.decisions, include_raw=kwargs.get("include_raw", False))
        return self.router

    def bind_tools(self, tools, **kwargs):
        return self

    @staticmethod
    def persona(msgs):
        sys_msg = next(m for m in msgs if isinstance(m, SystemMessage))
        return sys_msg.content.split("You are ")[1].split("\n")[0].strip()

    def human(self, name, index=0):
        msgs = [m for n, m in self.calls if n == name][index]
        return next(m for m in msgs if isinstance(m, HumanMessage)).content

    def count(self, name):
        return sum(1 for n, _ in self.calls if n == name)

    async def ainvoke(self, msgs, **kwargs):
        name = self.persona(msgs)
        self.calls.append((name, msgs))
        self.n += 1
        if name in self.fail_for:
            raise RuntimeError(f"simulated API failure in {name}")
        if self.tool_calls:
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


@pytest.mark.xfail(strict=True, reason="AUDIT R1: agent_outputs reducer keeps only the latest text per persona")
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


def test_structured_output_failure_propagates_and_aborts_the_run():
    """Documents audit R3: there is no retry around the routing call."""
    chat = FakeChat([ValueError("Failed to parse OrchestratorDecision")])
    graph = build(make_config(), chat)
    with pytest.raises(ValueError, match="OrchestratorDecision"):
        run(graph)


@pytest.mark.xfail(strict=True, raises=RuntimeError,
                   reason="AUDIT R3: a model error in one parallel branch aborts the whole run; "
                          "the sibling's findings are lost")
def test_one_failed_parallel_branch_keeps_the_other_findings():
    chat = FakeChat([decision(["A", "B"], assignments={"A": "a", "B": "b"}), decision(["Journalist"])],
                    fail_for={"B"})
    graph = build(make_config(), chat)
    state = run(graph)
    assert "findings from A" in state["agent_outputs"]["A"]
    assert "Journalist" in state["agent_outputs"]


def test_tool_exception_is_returned_to_the_agent_not_raised():
    """AUDIT B7: ToolNode(handle_tool_errors=True) turns a raising tool into a ToolMessage."""
    class ToolOnce(FakeChat):
        async def ainvoke(self, msgs, **kwargs):
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


@pytest.mark.xfail(strict=True, reason="AUDIT B4: when max_tool_rounds_per_agent is exhausted the last "
                                       "AIMessage is a tool call with empty content, so the specialist "
                                       "reports nothing")
def test_exhausted_tool_rounds_still_yield_findings():
    chat = FakeChat([decision(["A"]), decision(["Journalist"])], tool_calls=True)
    graph = build(make_config(max_rounds=2), chat, tools_for={"A": [dummy_tool]})
    state = run(graph)
    assert chat.count("A") >= 2
    assert state["agent_outputs"]["A"].strip() != ""


@pytest.mark.xfail(strict=True, reason="AUDIT B4: an empty Journalist draft is auto-approved without review")
def test_empty_journalist_draft_is_not_auto_approved():
    chat = FakeChat([decision(["Journalist"])], tool_calls=True)
    reviewer = FakeReviewer(["REJECTED: empty"])
    graph = build(make_config(max_rounds=2), chat, reviewer, tools_for={"Journalist": [dummy_tool]})
    state = run(graph)
    assert state["agent_outputs"]["Journalist"].strip() == ""  # the draft really is empty…
    assert len(reviewer.calls) >= 1                             # …so the reviewer must see it, or
    assert state["reviewer_approved"] is False                  # the run must not report success


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


@pytest.mark.xfail(strict=True, reason="AUDIT B5: interrupt() sits after the routing LLM call in the same node, "
                                       "so resuming re-runs the LLM and may execute a different plan than the "
                                       "one the user approved")
def test_hitl_resume_executes_the_plan_the_user_approved():
    cfg = make_config(hitl={"enabled": True, "checkpoints": ["post_plan"]})
    chat = FakeChat([decision(["A"]), decision(["B"]), decision(["Journalist"])])
    graph = build(cfg, chat)
    rc = {"configurable": {"thread_id": "t2"}}
    paused = run(graph, config=rc)
    assert "['A']" in paused["__interrupt__"][0].value["question"]
    resume(graph, "APPROVE", rc)
    assert chat.count("A") == 1, "approved plan was A, but A never ran"
    assert len(chat.router.calls) == 1, "routing LLM was called again on resume"


@pytest.mark.xfail(strict=True, reason="AUDIT B5: the reviewer LLM is replayed on resume; a replayed APPROVED "
                                       "discards the user's REVISE answer")
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


def test_hitl_override_does_not_dispatch_agents_the_user_asked_to_skip():
    cfg = make_config(specialists=("LitScout", "B"), hitl={"enabled": True, "checkpoints": ["post_plan"]})
    router = ScriptedRouter([decision(["B"])])
    with patch("automation.config.load_persona_content", return_value="p"), \
         patch("automation.graph.interrupt", return_value="Skip LitScout, go straight to the Journalist"):
        node = make_orchestrator_node(cfg, ["LitScout", "B"], router, cfg["hitl"])
        out = node(initial_state())
    assert "LitScout" not in out["next_agents"]


def test_hitl_free_text_without_agent_names_becomes_a_redirect_note():
    """AUDIT R14: a redirect that names no agent keeps the plan but is appended to the instructions."""
    cfg = make_config(specialists=("Alpha", "Beta"), hitl={"enabled": True, "checkpoints": ["post_plan"]})
    router = ScriptedRouter([decision(["Beta"], instructions="original")])
    with patch("automation.config.load_persona_content", return_value="p"), \
         patch("automation.graph.interrupt", return_value="please focus on epidemiology instead"):
        node = make_orchestrator_node(cfg, ["Alpha", "Beta"], router, cfg["hitl"])
        out = node(initial_state())
    assert out["next_agents"] == ["Beta"]
    assert out["next_instructions"].startswith("original")
    assert "USER REDIRECT: please focus on epidemiology instead" in out["next_instructions"]


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
    cfg = make_config(specialists=("LitScout", "Beta"), hitl={"enabled": True, "checkpoints": ["post_plan"]})
    router = ScriptedRouter([decision(["Beta"], instructions="original")])
    with patch("automation.config.load_persona_content", return_value="p"), \
         patch("automation.graph.interrupt", return_value="litscout, Journalist; and keep it short"):
        node = make_orchestrator_node(cfg, ["LitScout", "Beta"], router, cfg["hitl"])
        out = node(initial_state())
    assert out["next_agents"] == ["LitScout", "Journalist"]
    assert out["agent_call_count"] == 1
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
