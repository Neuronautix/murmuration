# Orchestration Audit — Murmuration multi-agent graph

*Scope:* `automation/graph.py`, `automation/config.py`, `automation/tools.py`, the
`execute`/`report` path of `automation/main.py`, `swarm_config.yml`, `agents/*/persona.md`,
`examples/toxicology/`, `tests/`.
*Method:* every finding below was traced through the code and then reproduced offline
with fake models against the real compiled graph (langgraph 1.2.18, langchain-core 1.7.0,
Python 3.13). The reproductions live in `tests/test_orchestration.py`; defects are
`xfail(strict=True)` tests tagged with the finding ID, so a fix flips them to XPASS and
the marker must be removed. Anything not reproduced is marked **unverified**.
*Constraint:* the CLAUDE.md invariant — users customise only `swarm_config.yml` and
`agents/*/persona.md` — is treated as binding. Every proposal says whether it is a
pure code fix (behaviour-preserving for users) or needs a new config key.

No code was changed by the audit itself. The quick-win pass (§6) was applied in a follow-up
commit; see **Status** below.

### Status

Fixed in the quick-win pass: **B1, B3, B6, B7, B8, B9, B10, B12, R4, R7, R8, R11, R12
(logged), R13 (logged), R14, R15**. Their tests in `tests/test_orchestration.py` now run as
plain regression tests. B11 was left out: `doctor` already *warns when the orchestrator has
no discovery tools*, so a second warning that its tools are ignored would contradict it; the
right fix is the bounded orchestrator tool loop in recommendation 1's follow-up, or removing
the tools from the orchestrator persona in the templates. The `langgraph>=1.0` floor was not
bumped because `main` now pins `langchain-core<1`; `handle_tool_errors=True` gives the same
behaviour on every supported version.

**B2** was fixed in a second follow-up: the brief is now the `journalist.brief` Jinja2
template (default in `automation/config.py`), rendered once at build time and validated by
`swarm doctor`; `output_sections` in the toxicology and HCM examples is now read by it.

**B4, B5, R1 and R3** were fixed in a third follow-up, together with three defects found
while fixing them (B13–B15 below):

- **B4.** When the tool budget runs out, one extra call to the same model *without tools*
  receives a plain-text digest of the tool results and writes the answer. Plain text is used
  rather than replaying tool messages because some providers reject tool messages in a request
  that defines no tools. An empty draft is rejected without a reviewer LLM call. If it is still
  empty at `max_revision_loops`, the run ends with `final_status: failed` and `execute` exits 1.
- **B5.** `post_plan`, `pre_journalist` and `on_rejection` are now separate nodes that make no
  LLM calls. LangGraph re-runs an interrupted node from its start on resume, so the plan or
  verdict the user answered is the one executed. A run through all three checkpoints makes
  exactly one routing call and one reviewer call per draft.
- **R1.** A re-dispatched specialist writes its new findings first and keeps the earlier
  rounds under an `[Earlier findings from …]` marker. Newest-first keeps the latest round
  visible in truncated views.
- **R3.** `orchestrator.on_agent_failure: continue` (default) records an `[AGENT FAILED]`
  note for a specialist or Journalist whose model call raises, so parallel siblings keep
  their findings. An unusable routing reply is retried once. If the retry also fails and
  findings exist, they go to the Journalist; with no findings the error is raised. `abort`
  restores fail-fast behaviour. Client-level HTTP retries already exist in the provider SDKs
  (`max_retries=2` by default), so no retry key was added.

New findings fixed in the same pass:

| ID | Sev. | Location (before the fix) | Failure scenario | Fix |
| --- | --- | --- | --- | --- |
| B13 | bug | `graph.py` Journalist node | The Journalist's first-draft prompt never included `next_instructions`, so the orchestrator's instructions **and the user's `pre_journalist` framing were silently dropped**. The checkpoint had no effect. | Orchestrator instructions are stored as `journalist_instructions` and framing as `user_framing`; both are shown on every draft, revisions included. |
| B14 | bug | `graph.py` orchestrator | A decision such as `["A", "A"]` sent two `Send`s to A, which ran twice and overwrote itself. | Targets are de-duplicated. |
| B15 | bug | `automation/builder/models.py`, `loader.py` | Every builder command that rewrites `swarm_config.yml` dropped keys the spec does not model: `output_sections` (which the toxicology and HCM briefs read), `reviewer.custom_prompt`, `reviewer.model.provider`/`env_key` (a cross-provider reviewer silently reverted to the base provider), and the new orchestrator and `model.pricing` keys. | The loader records unmodelled values and `to_config()` writes them back. A value is only restored while its parent block still exists, so clearing the reviewer model also clears its provider. |
| — | bug | `graph.py` reviewer and node outputs | Content returned as a list of blocks (Anthropic-style) was compared with `startswith`/sliced as a string. | `_message_text()` normalises message content everywhere. |

All 85 tests in the suite pass and no `xfail` markers remain. Every new or rewritten test
was run against the pre-fix commit and fails there.

Still open after the third follow-up: the design items **I1–I6** and **R2, R5, R6, R9, R10**
(reviewer-to-orchestrator evidence loop, structured findings, persistent checkpointer, run log,
fan-out limit, reviewer context). R2 was partly addressed: a force-approved draft is reported
as such by `execute`.

#### Fourth follow-up: the open design items

Each item became an optional config key whose default reproduces the previous behaviour.
One commit per workstream; every new behaviour test was run against the commit before it
and fails there.

| Item | Status | Key(s) | Notes |
| --- | --- | --- | --- |
| R6 | fixed | `swarm.run_log` | One JSON line per node update, plus run start/end (`final_status: error` or `interrupted` when the run dies). Every line has a `run_id`. HITL answers are attached to the checkpoint node's line. Written from the stream loop in `main._run_graph_async()`, so the graph is unchanged. A failed write disables the log with a warning rather than killing the run. The `pre_flight` answer is not logged: it is a CLI prompt before the graph starts. |
| I5 | fixed | `swarm.persist_findings` | `<output_dir>/findings/<persona-slug>.md` is rewritten after every specialist update, from the same stream loop, with the run id in a header comment. The Journalist's draft is not written there; its own writing tool saves it. `doctor` reports personas whose names map to the same file. |
| B11 | fixed | `orchestrator.allow_tools` | New key `orchestrator.allow_tools` (default `false`, validated as a bool by `load_config`). When `true`, `build_graph` loads and binds the orchestrator persona's tools and the node runs a tool loop bounded by `max_tool_rounds_per_agent` before each routing decision (not on the safety-valve pass); its short report goes into the routing prompt under `YOUR QUICK CHECKS` and its tokens are counted. A failing loop is noted and skipped under `on_agent_failure: continue` and raises under `abort`. The loop runs inside the orchestrator node, so `compute_recursion_limit()` is unchanged. With the default the node is the same synchronous router with a byte-identical prompt. `doctor` now warns that the orchestrator's tools are ignored when `allow_tools` is false, and gives the "no discovery-oriented tools" warning only when it is true, so the two never fire together. The toxicology, FAIR-NAMs and PIU examples set `allow_tools: true`: their orchestrator personas list search tools (toxicology and PIU ask for quick factual checks with them), and opting in keeps their doctor reports free of the new warning. The root template keeps the default, so `doctor` warns there until a user decides. Trade-offs: (1) the warning also fires on the climate, finance, HCM and legal examples and on every builder-generated swarm, because the builder requires every persona (the orchestrator included) to keep at least one tool; it is accurate, so it was kept rather than downgraded. (2) The FAIR-NAMs persona does not itself ask for quick checks; the pack was opted in because `tests/test_example_packs.py` pins its warning set and the builder forbids an empty tool list, so the opt-in changes that pack's runtime (extra calls per routing pass). |
| B12 | fixed (provenance) | — | Rows name the persona that logged them. Each node passes its persona name (the Journalist's from `orchestrator.journalist`) in the `RunnableConfig` metadata key `murmuration_agent` to `ToolNode`; `append_traceability_matrix` reads it through an injected `config: RunnableConfig` parameter that is hidden from the tool schema. The node's own config is passed explicitly and merged, because Python 3.10 does not propagate it through context variables. A call without that metadata still writes `Auto-Agent`. Appends are now serialised with a lock: review found that two parallel specialists logging at once lost a row in 3 of 8 runs on Windows, where an append is seek-then-write (the earlier single-thread stress test in §7 ran on Linux). |
| I3 | fixed | `personas[].model` | Merged over `model` (as `reviewer.model` is) for the orchestrator's routing and tool models, specialists and the Journalist; one client per distinct merged spec. Validated per key (`personas[<name>].model.<key>`). `validate_env()` checks each persona's key; a provider switch without `env_key` checks that provider's standard variable rather than the base key. `doctor` warns about persona keys missing from the shell. `env_key` only names the variable that is checked: `create_model()` does not pass it to the client, which reads its provider's standard variable (unchanged behaviour, also true for the base model). Modelled in `PersonaSpec`, the loader and `to_config()`, because `extra_config` does not reach list items. Not done: per-persona pricing, so `run_metrics.json` prices every token with `model.pricing`. |
| R9 | fixed | `orchestrator.max_parallel_agents` | A semaphore around each specialist's model/tool loop. It is created per running event loop (`_LoopLocalSemaphore`), because a compiled graph is run from a new loop on each `asyncio.run()` and an `asyncio.Semaphore` binds to the first loop that waits on it. Chosen over capping the dispatch list because it changes neither routing, `agent_call_count`, nor the recursion bound. |
| R5 | fixed | `checkpointer: {type, path}`; `execute/report --resume` | Deviation from the §4 sketch (`hitl.checkpointer`): the key is top-level because crash resume is useful without HITL. Default: `memory` with HITL, otherwise none (unchanged). `sqlite` uses `AsyncSqliteSaver` from `langgraph-checkpoint-sqlite`, the optional `persist` extra (`>=3.0.1,<3.1`: 3.1 needs `langgraph-checkpoint` 4, which breaks the langgraph lines this project supports; CI installs the extra). The saver holds an aiosqlite connection bound to the event loop, so `main` opens it inside the loop and passes it to `build_graph(config, checkpointer=...)`; `build_graph` refuses `sqlite` without one. The thread id is printed at start and a resume hint on failure or Ctrl-C. `--resume` reads the saved state: a pending interrupt is asked again, otherwise the graph continues with `None`, so steps that finished are not re-run (the failed step runs again). The resumed run's token total is read from the checkpoint, so it covers both processes; writes LangGraph replays from the crashed step (marked `cached`) are not counted or logged again. A resume whose saved next step the current config does not build (e.g. HITL turned off) fails with a clear error instead of reporting success. The default path is `./.murmuration/checkpoints.sqlite`, outside `output_dir`, because `git_commit_snapshot` force-adds that folder and the database is binary run state; `*.sqlite*` and `.murmuration/` are gitignored and `doctor` warns about a path inside `output_dir`. `doctor` reports a missing extra, a directory path, and `memory` without HITL. No new node, so the recursion bound is unchanged. |
| I4 | fixed | `orchestrator.early_stop: {enabled, min_rounds}` | `OrchestratorDecision` gains `sufficient_evidence: bool = False` and `coverage: str = ""`; the defaults keep existing fakes and replies parsing. Consequence: the structured-output schema the routing model sees has two more optional fields even when early stop is off (the prompt text is unchanged). When enabled, the routing prompt asks for them, and a sufficient claim after `min_rounds` specialist rounds (new state counter `specialist_rounds`) replaces any specialist dispatch with the Journalist and passes the coverage note on. It only shortens runs, so the recursion bound is unchanged. `doctor` warns when `min_rounds` exceeds `max_agent_calls`. |
| I6 | fixed | `orchestrator.max_peer_chars` (existing) | Views end at the last paragraph break, sentence end or whitespace inside the limit, if that keeps at least half of it, and otherwise cut hard as before. This is the one deliberate change to default behaviour in this pass: truncated views can now be shorter than the limit and end with " …". No existing test depended on the exact boundary. |
| I1 | fixed | `reviewer.on_rejection`, `reviewer.max_evidence_loops` | `ReviewerVerdict(verdict, style_issues, missing_evidence: [EvidenceRequest(claim, reason, suggested_agent)])` via `with_structured_output(..., include_raw=True)`, used only when `on_rejection` is not `journalist`; a parse failure or exception falls back to a plain text call with the `APPROVED` / `REJECTED:` protocol (the reviewer prompt, including `custom_prompt`, is unchanged). The reviewer node decides the route and stores it in state (`review_route`, `reviewer_requests`, `evidence_loop_count`); `auto` goes to the orchestrator only with missing evidence; both stop at `max_evidence_loops` and once `max_agent_calls` is spent (the orchestrator could only hand straight back). An early-stop claim is ignored during an evidence round. A structured reply that is unparseable but follows the text protocol is used as is, without a second call. The orchestrator's routing prompt gets a `REVIEWER REQUESTS` block (a `suggested_agent` that is not a specialist is dropped) until it hands back to the Journalist. Rejection reasons now live in `reviewer_feedback`, so the orchestrator's own instructions cannot overwrite the Journalist's revision instructions. The `on_rejection` HITL node names the destination and routes either way; `pre_journalist` is asked only before the first draft. `agent_call_count` still bounds specialist work; `compute_recursion_limit()` adds `2 * max_evidence_loops` when the route is enabled (each round adds one orchestrator hand-back pass; Journalist and reviewer passes are already counted by `max_revision_loops`). |
| R2 | fixed | `reviewer.on_max_revisions: flag\|fail` | `flag` (default) is the previous behaviour: force-approve and warn. `fail` reviews the last draft once more instead of approving it unread; if it is still rejected the run ends `failed` and `execute` exits 1. With the HITL `on_rejection` checkpoint on, that final failure is not put to the user (there is nothing left to revise); a user who wants the last word should keep `flag`. The audit's `approve` option was not added: it is what `flag` already does minus the warning. |
| R10 | fixed | `reviewer.context`, `reviewer.matrix_excerpt_rows` | `[draft]` (default) sends the draft only, byte-identical to before. `task` prepends the research task; `matrix` appends up to `matrix_excerpt_rows` of the rows logged after the last `### Run` separator, i.e. this run's (a `### Resumed run` separator does not start a new section, so rows logged before a crash stay in). `doctor` warns when `matrix` is requested but no persona can log to the matrix. |

---

## 1. How it works today (verified)

| Claim in the brief | Verdict | Where |
| --- | --- | --- |
| START → orchestrator → specialists (one, or several via `Send`) → orchestrator → Journalist → Reviewer → END | **Mostly true.** The orchestrator may also route to `END`, which crashes (B1). | `graph.py:605-636` |
| Orchestrator decides with structured output (`OrchestratorDecision`) | True. `base_model.with_structured_output(OrchestratorDecision)`; the orchestrator's own tool list is ignored (B11). | `graph.py:575`, `:283` |
| Specialists run tool calls concurrently via `asyncio.gather`, see peers truncated at `MAX_PEER_CHARS` | True. `ToolNode.ainvoke` gathers tool calls; peers capped at 600 chars, hard-coded. Parallel siblings see only findings that existed *before* dispatch, not each other. | `graph.py:75`, `:205-217`, `:380-384` |
| Reviewer rejection loops back to the Journalist only, capped by `max_revision_loops` | True. The orchestrator is never revisited. The cap force-approves the final draft **unread** (R2). | `graph.py:475-482`, `:630-636` |
| HITL `interrupt()` at pre_flight / post_plan / pre_journalist / on_rejection with `MemorySaver` | True, but `interrupt()` is placed *after* the LLM call inside the same node, so resuming replays the LLM (B5). `pre_flight` is a plain CLI confirm, not an interrupt. | `graph.py:303-337`, `:506-523`, `:643-644`; `main.py:377-383` |
| Evidence goes into the traceability matrix through `append_traceability_matrix` | Only partially. The literature-searching specialists in every shipped config lack the tool (B10); the matrix never records which persona logged a row (B12); the reviewer never sees the matrix (R10). | `config.py:194`, `swarm_config.yml:76-107`, `tools.py:599` |

Step budget: a run with the root defaults (`max_agent_calls: 8`, `max_revision_loops: 3`)
executes at most 25 graph nodes in the worst case (8 sequential cycles + safety-valve
routing + Journalist + 3 full revision loops). With langgraph 1.2.18 and the empty run
config `main.py:396` passes, **no recursion limit is enforced at all** (verified: 203 node
executions without `GraphRecursionError`; an explicit `recursion_limit: 25` does raise).
See R7.

---

## 2. Answers to the ten questions

**Q1 Control flow.** The loop is bounded by `orchestrator.max_agent_calls`
(`graph.py:233,244`), counted per specialist dispatch, so a parallel fan-out of three
counts three. Every orchestrator exit leads to a specialist, the Journalist, or the
reviewer, and specialists always return to the orchestrator, so the loop cannot run
forever (test `test_specialist_loop_is_bounded_by_max_agent_calls`). A run **can** end
without the Journalist, and worse: when the orchestrator returns `["END"]`, which its own
prompt invites (`graph.py:276`), `orchestrator_router` returns `"reviewer"` or `END`
(`graph.py:619`) but the conditional-edge map is keyed on `"END"` (`graph.py:623`), so
LangGraph raises `KeyError: 'reviewer'` (or `'__end__'`) and the run aborts with all
findings lost (B1). Unknown names are filtered out and an empty result silently becomes
`["Journalist"]` (`graph.py:289-291`), even when unconsulted specialists remain (R13).
`["A", "Journalist"]` runs A and silently drops the Journalist for that round (R12).

**Q2 State.** `agent_outputs` uses a dict-merge reducer where newer values win
(`graph.py:80-82`). Parallel branches write different keys and merge losslessly;
`token_usage` sums once per node (verified, `test_parallel_branches_merge_outputs_and_token_usage_once`).
The same specialist dispatched twice **overwrites** its earlier findings (R1).
`agent_assignments` uses the same merge reducer (`graph.py:111`) and is never cleared, so a
per-agent instruction from round 1 shadows a new shared instruction in round 3 because the
specialist reads the assignment first (`graph.py:375-377`) (B3). `revision_count` only
increments (`graph.py:535`) and is never reset; that is fine because there is exactly one
Journalist/reviewer phase per run. Token usage is not double-counted, but the orchestrator's
own calls are never counted at all because `with_structured_output()` returns a bare
Pydantic object with no `usage_metadata` (`graph.py:152-171`, `:575`) (B8), and HITL replays
spend tokens that are never reported (B5).

**Q3 Feedback.** Yes, and the toxicology Journalist persona already assumes it exists
("insert [SOURCE NEEDED] and notify Dr. Nexus", `examples/toxicology/agents/TOCSAS_Journalist/persona.md:45`)
although no such path exists. Design in §4, recommendation 3.

**Q4 Collaboration.** Peer findings are free text, truncated mid-sentence at 600 chars.
The orchestrator persona is asked to "resolve disagreements" (`agents/Orchestrator/persona.md:46`)
but has no structured way to see one. Claim-level evidence objects would also fix the
grounding gap in Q5. Design in §4, recommendation 2 and §5.

**Q5 Grounding.** Not traceable today. (a) In every shipped config the specialists that
run literature searches have no `append_traceability` tool, yet `config.py:194` injects
"After every literature or web search, call `append_traceability_matrix` immediately" into
their system prompt (B10). (b) Rows are attributed to `**Auto-Agent**`, not the persona
(`tools.py:599`) (B12). (c) The reviewer receives only the draft text, never the task or the
matrix (`graph.py:489-492`), so it cannot check that a citation exists (R10). (d) A `|` in a
logged fact breaks the Markdown row (B12). Concurrent appends did **not** interleave in a
stress test (8 threads × 100 rows × 3 KB; every row intact) because each row is a single
`O_APPEND` write; the race is not reproduced and is marked low risk.

**Q6 Cost and latency.** The orchestrator is sent *all* findings uncompressed on every
routing call (`graph.py:259`; the module docstring at `:22` claims otherwise). With two
specialists at 20 K chars each the routing prompt is 40 K chars, and it grows every cycle
(R4). There is no cap on parallel fan-out (`graph.py:614`) (R9), one model for every role
except the reviewer (`config.py:230`) (I3), no convergence/early-stop signal (I4), and
cost estimation hard-codes gpt-4o prices (`main.py:216`) (R15).

**Q7 Robustness.** Tool exceptions: `ToolNode(tools)` is built with the default handler
(`graph.py:179,205`), which in langgraph ≥ 1.0 re-raises anything that is not an
argument-validation error, so a tool body that raises aborts the whole run (B7; the built-in
tools mostly catch their own exceptions, but `write_manuscript_section` calls `os.makedirs`
outside its `try`, `tools.py:539`, and user-registered tools get no protection). A model
error in one parallel branch aborts the run and the sibling's completed findings are lost
(R3). A structured-output parse failure in the orchestrator propagates (R3). There are no
retries and no per-node timeout; `search_pubmed` issues two HTTP requests with no timeout
(`tools.py:152,170`) (R8). When `max_tool_rounds_per_agent` is exhausted while the model is
still calling tools, the final message is a tool call with empty content, so the specialist
reports `""`; if that happens to the Journalist, the empty draft is auto-approved and the CLI
prints "Task complete" (`graph.py:395,446,485-487`; `main.py:406`) (B4).

**Q8 HITL and resumability.** `MemorySaver` is created only when HITL is on and is
process-local (`graph.py:643-644`); the `thread_id` is a fresh UUID never shown to the user
(`main.py:396`). A crash or Ctrl-C after a checkpoint cannot be resumed (R5). Separately,
because `interrupt()` is placed after the LLM call in the same node, resuming re-executes
the call: the plan the user approved is not necessarily the plan that runs (reproduced:
approved `['A']`, executed `B`), and a replayed reviewer that happens to say APPROVED
discards the user's REVISE (B5). The `post_plan` override matches agent names as substrings
of the reply, so "Skip LitScout, go straight to the Journalist" dispatches LitScout (B9),
and a reply that names no agent is silently ignored (R14).

**Q9 Observability.** Routing reasoning is only printed to the console
(`main.py:245-273`); `run_metrics.json` holds token counts only (`main.py:211-243`). Nothing
persists which agents ran, why, in what order, or what the reviewer said (R6).

**Q10 Tests.** Before this audit `tests/test_graph.py` only compiled the graph, and
`tests/test_example_packs.py` exercised one orchestrator call. Nothing covered routing,
bounds, reducers, the reviewer loop, HITL, tool-loop exhaustion, or failure propagation.
`tests/test_orchestration.py` now adds 33 offline tests: 16 pass and pin current correct
behaviour; 17 are strict xfails reproducing B1–B10, R1, R3, R4 and the Journalist brief.

---

## 3. Findings table

Severity: **bug** = wrong result or crash on a reachable path; **risk** = silent degradation
or data loss under realistic conditions; **improvement** = capability gap.
Effort: S < ½ day, M = 1–2 days, L > 2 days. "Config key" says whether a user-visible option
is needed; "no" means a pure code fix that preserves the invariant.

| ID | Sev. | Location | Failure scenario | Proposed fix | Config key | Effort | Verified |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | bug | `graph.py:619,623` | Orchestrator returns `["END"]` (its prompt at `:276` offers it) → router returns `"reviewer"`/`END`, not a `routing_map` key → `KeyError`, run aborts, findings lost. | Return path-map keys from the router (`"END"`), and treat `END` before a draft exists as `Journalist`. Drop `END` from the routing prompt. | no | S | test `test_end_route_*` |
| B2 | bug | `graph.py:430-439` | Journalist brief hard-codes "a section distinguishing high engagement from clinical impairment" (psychiatry) and the tool names `write_manuscript_section`/`git_commit_snapshot`. Toxicology, climate, legal and finance examples all receive it; 4 of 6 example Journalists lack `git_snapshot`. Violates the CLAUDE.md invariant. | Move the brief to config (`journalist.brief`, Jinja-templated with `{{ required_elements }}`), default to a domain-neutral text, and derive tool guidance from the persona's actual tool list. | **yes** | S | test `test_journalist_brief_is_domain_neutral` |
| B3 | bug | `graph.py:111,375-377` | Round 1 assigns `{A: "old"}`; round 3 dispatches A alone with new shared instructions → A still receives "old". Safety valve and HITL override "clear" with `{}` which merges nothing (`:325`). | Make `agent_assignments` a plain (replacing) field, or read an assignment only when `persona_name in state["next_agents"]`. | no | S | test `test_stale_assignment_*` |
| B4 | bug | `graph.py:205-217,395,446,485-487` | Model still calling tools when `max_tool_rounds_per_agent` runs out → final `AIMessage.content == ""` → specialist output is empty; for the Journalist the empty draft is auto-approved and the CLI reports success. | After the last round, make one more call with tools disabled ("summarise what you found"). Treat an empty draft as a rejection with a fixed reason, not an approval. | optional `orchestrator.on_tool_rounds_exhausted: summarise\|accept` | S–M | tests `test_exhausted_tool_rounds_*`, `test_empty_journalist_draft_*` |
| B5 | bug | `graph.py:283 vs 303`, `:489 vs 506` | `interrupt()` follows the LLM call in the same node. On resume the node re-runs: the orchestrator may pick a different plan than the one shown (reproduced), `post_plan`+`pre_journalist` costs 3 routing calls, and a replayed reviewer at temperature 0.8 that says APPROVED discards the user's REVISE. Replayed tokens are never counted. | Split each checkpoint into its own node that reads the decision from state (LangGraph's documented pattern), or persist the decision in state before interrupting. | no | M | tests `test_hitl_resume_*`, `test_hitl_on_rejection_*` |
| B6 | bug | `graph.py:495` | Verdict parse is `content.strip().startswith("APPROVED")`. `**APPROVED**` or "The draft is APPROVED." counts as a rejection and burns a full revision loop. | Structured output `ReviewerVerdict(verdict, reasons, missing_evidence)` (also needed for recommendation 3); fallback regex `^\W*APPROVED`. | no (prompt text stays config-driven) | S | test `test_reviewer_accepts_markdown_wrapped_approved` |
| B7 | bug | `graph.py:179,205` | `ToolNode(tools)` default handler in langgraph ≥ 1.0 re-raises tool-body exceptions. Any user-registered tool that raises (and `write_manuscript_section`'s `os.makedirs`, `tools.py:539`) aborts the run. `pyproject.toml` allows `langgraph>=0.2`, where the default was to swallow errors, so behaviour depends on the installed version. | `ToolNode(tools, handle_tool_errors=True)`; pin `langgraph>=1.0`. | no | S | test `test_tool_exception_is_returned_*` |
| B8 | bug | `graph.py:152-171,575` | `with_structured_output()` returns a Pydantic object; `_extract_token_usage` finds no `usage_metadata` and records 0. Orchestrator cost is invisible in `run_metrics.json`. | `with_structured_output(..., include_raw=True)` and read `raw.usage_metadata`. | no | S | test `test_orchestrator_token_usage_is_counted` |
| B9 | bug | `graph.py:318-320` | Override parsing is `a.lower() in answer.lower()`. "Skip LitScout, go to Journalist" dispatches LitScout; a one-letter persona name matches any sentence. | Accept only exact, comma-separated persona names; anything else is appended to `next_instructions` as a user note. | no | S | test `test_hitl_override_*` |
| B10 | bug | `config.py:194`; `swarm_config.yml:76-107`; all `examples/*/swarm_config.yml` | Every specialist is told to call `append_traceability_matrix` after each search, but no literature-searching specialist in any shipped config has the tool. The matrix receives little or no primary evidence. | Add `append_traceability` to specialist tool lists in the root template, the examples and the builder templates. Inject the instruction only when the persona actually has the tool. Add a `doctor` warning. | config edit (existing key) | S | test `test_specialists_told_to_log_traceability_have_the_tool`; config scan |
| B11 | bug | `graph.py:575,588` | Orchestrator persona lists four tools and its persona promises to use them; the routing model is built without tools and `load_tools_for_persona` is never called for it. | Either give the orchestrator a bounded tool loop before routing (`orchestrator.allow_tools: true`) or have `doctor` warn that orchestrator tools are ignored. | optional `orchestrator.allow_tools` | S (warn) / M (loop) | test `test_orchestrator_persona_tools_are_not_loaded` |
| B12 | bug | `tools.py:597-601` | Every row says `**Auto-Agent**`; a `\|` inside `fact` or `source` breaks the table (reproduced: 7 pipes in a 6-pipe row). Provenance per persona is lost. | Escape `\|`; pass the persona name via `RunnableConfig` metadata set by the node (tool signature gains an injected `config` arg, no persona-specific code). | no | S–M | manual reproduction |
| R1 | risk | `graph.py:80-82,400` | Orchestrator legitimately re-dispatches a specialist for a new sub-question → the first round's findings are overwritten. | Reducer appends with a round header, or stores `list[str]` per persona; `_format_findings` joins them. | optional `orchestrator.keep_findings_history: true` | S | test `test_same_specialist_twice_*` |
| R2 | risk | `graph.py:475-482`; `main.py:406` | After `max_revision_loops` rejections the next draft is force-approved without being read, and the CLI prints "Task complete". | `reviewer.on_max_revisions: approve\|fail\|flag` (default `flag`: approve but write the last rejection into the draft header and run log). | **yes** | S | test `test_revision_loop_is_bounded_*` |
| R3 | risk | `graph.py:388-392`; `main.py:398-404` | One parallel branch's model call raises (rate limit, 5xx) → whole run aborts; nothing is persisted; the orchestrator never learns which agent failed. Same for a structured-output parse error. | Wrap node LLM calls: on failure record `"[A failed: …]"` in `agent_outputs` and continue; `model.with_retry()`; retry the routing call once with the parse error appended. | `model.max_retries`, `orchestrator.on_agent_failure: skip\|abort` | M | tests `test_one_failed_parallel_branch_*`, `test_structured_output_failure_*` |
| R4 | risk | `graph.py:259` (vs docstring `:22`) | Orchestrator prompt carries all findings verbatim every cycle; 40 K chars with two specialists, growing per round. | `_format_findings(..., compress=True, max_chars=cfg)`. | `orchestrator.max_findings_chars` (default 1500) | S | test `test_orchestrator_prompt_compresses_findings` |
| R5 | risk | `graph.py:643-644`; `main.py:396` | `MemorySaver` dies with the process; `thread_id` is random and hidden. A crash after 7 of 8 specialist calls loses everything; HITL sessions cannot be resumed. | `hitl.checkpointer: {type: memory\|sqlite, path}` using `langgraph-checkpoint-sqlite` behind a try/except import (same pattern as Chroma); print the `thread_id`; add `execute --resume <thread_id>`. | **yes** | M | by reading |
| R6 | risk | `main.py:245-273,211-243` | No persisted record of routing decisions, reasoning, agent order, reviewer verdicts or HITL answers. | Append one JSON line per node update to `<output_dir>/run_log.jsonl`; include `reasoning`, `next_agents`, assignments, verdicts, token deltas. | `swarm.run_log` (path or `false`) | S | by reading |
| R7 | risk | `main.py:396` | `recursion_limit` is never set. On langgraph 1.2.18 with `config={}` **no limit is enforced** (verified), so the graph relies solely on the two safety valves. Older langgraph versions enforce 25 by default, which a toxicology-style run (`max_agent_calls: 10`, three rejections → 29 node executions) would exceed. | Compute `recursion_limit = 2*max_agent_calls + 2*(max_revision_loops+1) + 4` and pass it explicitly. | no | S | current version verified; older-version behaviour **unverified** |
| R8 | risk | `tools.py:152,170` | `search_pubmed` has no HTTP timeout; a hung NCBI call blocks the specialist thread and, through `asyncio.gather`, the whole superstep. | `timeout=20` on both calls; add a per-node timeout. | optional `orchestrator.agent_timeout_s` | S | by reading |
| R9 | risk | `graph.py:614` | Nothing limits fan-out; a 7-specialist example can issue 7 × `max_tool_rounds` concurrent model calls and trip provider rate limits. | Semaphore around specialist LLM calls. | `orchestrator.max_parallel_agents` | S–M | by reading |
| R10 | risk | `graph.py:489-492` | Reviewer sees only the draft: it cannot check relevance to the task or that cited claims exist in the matrix. | Pass task and a matrix excerpt; reviewer checklist keys become config. | `reviewer.context: [task, matrix]` | S | by reading |
| R11 | risk | `graph.py:421` | On revision the Journalist's own previous draft is re-injected under "ALL SPECIALIST FINDINGS ### [Journalist]". Harmless today, but it inflates the prompt and blurs provenance. | Exclude the journalist from findings and add an explicit "PREVIOUS DRAFT" block. | no | S | test `test_journalist_revision_prompt_includes_*` |
| R12 | risk | `graph.py:610-619` | `["A", "Journalist"]` runs A and drops Journalist silently; nothing is logged. | Log the dropped target in the orchestrator message. | no | S | test `test_specialist_and_journalist_in_one_decision_*` |
| R13 | risk | `graph.py:289-291` | A hallucinated name silently jumps to the Journalist although specialists were never consulted. | Re-ask the orchestrator once with the invalid names listed; only then fall back. | `orchestrator.max_routing_retries` (default 1) | S | test `test_unknown_agent_name_*` |
| R14 | risk | `graph.py:316-325` | A `post_plan` reply that names no agent is discarded without notice. | Append it to `next_instructions` as "USER REDIRECT". | no | S | test `test_hitl_free_text_*` |
| R15 | risk | `main.py:216-217` | Cost estimate always uses gpt-4o prices regardless of `model.name`. | `model.pricing: {input_per_m, output_per_m}` with the current values as default. | **yes** | S | by reading |
| I1 | improvement | `graph.py:630-636` | Reviewer cannot request more evidence (see §4). | `reviewer.on_rejection` routing. | **yes** | M | design |
| I2 | improvement | `graph.py:75,380-384` | Peer context is a 600-char slice; no disagreement or confidence capture. | Claim-level evidence objects, see §5. | **yes** | M–L | design |
| I3 | improvement | `config.py:201-253` | One model for every role except the reviewer. | `personas[].model` override merged like the reviewer's. | **yes** | S | design |
| I4 | improvement | `graph.py:240-256` | No early stop when findings converge. | Add `sufficient_evidence: bool` + `coverage` to `OrchestratorDecision`; stop after `min_rounds`. | `orchestrator.early_stop` | S–M | design |
| I5 | improvement | `graph.py:388-400` | Nothing persists findings before the Journalist; a crash loses them. | Write `agent_outputs` to `<output_dir>/findings/<persona>.md` after each specialist node (generic, not domain code). | `swarm.persist_findings` | S | design |
| I6 | improvement | `graph.py:75` | `MAX_PEER_CHARS` is a constant and truncation is mid-sentence. | `orchestrator.max_peer_chars`; truncate at a paragraph boundary. | **yes** | S | design |

---

## 4. Top 5 recommendations (ranked by impact ÷ effort)

### 1. Crash-proof the control flow (B1, B7, R3, R8, R7) — effort S–M, no new keys required

Four independent reachable paths abort a run and discard every finding: the `END` route
(`KeyError`), a tool body raising, one parallel branch failing, and a hung HTTP call. All
are small, local fixes:

```python
# graph.py (sketch)
tool_executor = ToolNode(tools, handle_tool_errors=True)
...
def orchestrator_router(state):
    ...
    if "Journalist" in targets or "END" in targets:   # END before a draft means "write it"
        return "Journalist"
```

Specialist node: wrap the LLM loop, and on exception return
`{"agent_outputs": {name: f"[{name} failed: {exc}]"}}` so the orchestrator can decide to
retry or move on. Pass an explicit `recursion_limit` derived from the config.

Optional config surface, all with safe defaults:

```yaml
orchestrator:
  on_agent_failure: skip        # skip | abort
  agent_timeout_s: 300
model:
  max_retries: 2
```

### 2. Close the grounding gap (B10, B2, B12, R10) — effort S, one new key

* Give every evidence-gathering specialist `append_traceability` in the root template, all
  six examples, and `automation/builder/templates`; have `doctor` warn when
  `epistemic_tags` is set but a specialist lacks a traceability tool.
* Move the Journalist brief out of `graph.py`:

```yaml
journalist:
  brief: |
    Write a complete structured output. You MUST include:
    {% for item in reviewer.required_elements %}  • {{ item }}
    {% endfor %}
    Save each section with your write tool, then version it with your snapshot tool.
```

  Default is the domain-neutral text above; the toxicology example sets its own
  (sections A–J). Tool guidance is generated from the persona's tool list, so no tool
  name is hard-coded.
* Record the persona name in each matrix row (node sets `config["metadata"]["agent"]`,
  the tool reads it) and escape `|`.
* Give the reviewer the task and a matrix excerpt:

```yaml
reviewer:
  context: [task, matrix]        # default: [draft] (today's behaviour)
  matrix_excerpt_rows: 50
```

### 3. Let the reviewer send the swarm back for evidence (I1, B6, R2) — effort M, new keys

Replace the free-text verdict with structured output and route on it:

```python
class ReviewerVerdict(BaseModel):
    verdict: Literal["APPROVED", "REJECTED"]
    style_issues: list[str] = []                 # Journalist can fix alone
    missing_evidence: list[EvidenceRequest] = [] # needs a specialist

class EvidenceRequest(BaseModel):
    claim: str
    reason: str
    suggested_agent: str | None = None           # must be in valid_targets or it is ignored
```

```yaml
reviewer:
  on_rejection: auto            # journalist | orchestrator | auto
  max_evidence_loops: 1         # orchestrator round-trips triggered by the reviewer
  on_max_revisions: flag        # approve | fail | flag
```

Routing rule: `journalist` is today's behaviour; `orchestrator` always re-enters the
supervisor with the verdict as `next_instructions`; `auto` goes to the orchestrator when
`missing_evidence` is non-empty and `evidence_loops < max_evidence_loops`, otherwise to the
Journalist. The orchestrator prompt gains a "REVIEWER REQUESTS" block listing each claim and
suggested agent, and `agent_call_count` continues to bound total specialist work. State
gains `evidence_loop_count: int`. The existing `max_revision_loops` keeps bounding
Journalist-only revisions, so the total number of node executions stays finite:
`2*max_agent_calls + 2*max_evidence_loops + 2*(max_revision_loops+1) + 4`.

### 4. Make HITL honest and resumable (B5, B9, R5, R14) — effort M, one new key

Split each checkpoint into its own node that reads the already-computed decision from
state (`orchestrator → hitl_post_plan → …`, `reviewer → hitl_on_rejection → …`), so a
resume never re-runs an LLM. Parse overrides as exact persona names; append anything else to
the instructions. Then:

```yaml
hitl:
  enabled: true
  checkpointer:
    type: sqlite                # memory (default) | sqlite
    path: ./Drafts/checkpoints.sqlite
```

`main.py` prints the `thread_id` at start and gains `execute --resume <thread_id>`. The
sqlite saver is imported inside a try/except like Chroma, so the hard dependency list does
not change.

### 5. Observability and cost hygiene (R6, B8, R4, R15, I6) — effort S, two new keys

```yaml
swarm:
  run_log: ./Drafts/run_log.jsonl     # false to disable
orchestrator:
  max_findings_chars: 1500            # per specialist, in the routing prompt
  max_peer_chars: 600                 # replaces the MAX_PEER_CHARS constant
model:
  pricing: {input_per_m: 2.50, output_per_m: 10.00}
```

One JSON line per node update (node, timestamp, `next_agents`, `reasoning`, assignments,
verdict, token delta) answers "why did it route there?" after the fact. Counting the
orchestrator's tokens (`include_raw=True`) makes `run_metrics.json` truthful.

---

## 5. Agent collaboration design notes (Q4)

Recommended, in order of value for cost:

1. **Claim-level evidence objects** instead of free text in `agent_outputs`. Each specialist
   returns, via structured output, `findings: list[Claim]` where
   `Claim = {text, epistemic_tag, sources: [str], confidence: 0–1, contradicts: [claim_id]}`,
   plus a free-text `narrative`. The traceability matrix is written from these objects
   (fixing B10/B12 structurally), peers see claims rather than a 600-char slice, the
   reviewer can check every draft citation against a claim id, and `confidence` and
   `contradicts` give the orchestrator persona the material its "resolve disagreements" rule
   needs. Config: `collaboration.structured_findings: true` (default false until the examples
   are migrated); `epistemic_tags` already supplies the tag vocabulary.
2. **Disagreement capture** falls out of (1): the orchestrator prompt lists claim pairs with
   `contradicts` set, and the Journalist brief requires a "Points of disagreement" section when
   any exist (`collaboration.surface_disagreements: true`).
3. **Critique round**: `collaboration.critique_round: true` dispatches each specialist once
   more with the peers' claims and asks only for objections, bounded by `max_agent_calls`.
   Useful for the toxicology Bias Auditor pattern; expensive, so off by default.
4. Full debate protocols are not recommended: the supervisor pattern plus (1)–(3) covers the
   value at a fraction of the token cost.

---

## 6. Quick wins (one PR, all S)

- B1: router returns path-map keys; `END` before a draft → `Journalist`; remove `END` from the prompt.
- B3: `agent_assignments` becomes a replacing field.
- B6: tolerant verdict regex (`^\W*APPROVED`) until structured output lands.
- B7: `ToolNode(tools, handle_tool_errors=True)`; `langgraph>=1.0` in `pyproject.toml`.
- B8: `with_structured_output(..., include_raw=True)` and read `raw.usage_metadata`.
- B9 / R14: exact-name override parsing; free text appended to instructions.
- B10: add `append_traceability` to specialist tool lists (root template, six examples, builder templates); `doctor` warning.
- B11: `doctor` warning that orchestrator tools are ignored.
- B12: escape `|` in matrix rows.
- R4: `compress=True` for the orchestrator's findings (constant first, config key later).
- R7: pass an explicit `recursion_limit` computed from the config.
- R8: `timeout=20` on both `search_pubmed` requests.
- R11: exclude the Journalist from "specialist findings"; label the previous draft.
- R12 / R13: log dropped or invalid targets in the orchestrator message.
- R15: `model.pricing` with today's values as default.
- Docstring at `graph.py:22` ("compressed findings summary") corrected to match behaviour.
- Remove the corresponding `xfail` markers in `tests/test_orchestration.py` as each fix lands
  (pytest will flag them as XPASS(strict) otherwise).

---

## 7. Not reproduced / out of scope

- **Matrix append race** (Q5): 800 concurrent 3 KB rows from 8 threads all intact. Rows are
  single `O_APPEND` writes; only rows above the kernel's atomic-write size could interleave.
  Low risk, no fix proposed beyond B12.
- **Recursion limit on older langgraph** (R7): the 25-step default is enforced only when a
  `recursion_limit` key is present on 1.2.18. Behaviour on the 0.2.x line that
  `pyproject.toml` still permits is **unverified**.
- `automation/ingest.py`, `search_knowledge_base`, the builder wizard and the `report`
  command's templates were skimmed only; nothing orchestration-related found.
