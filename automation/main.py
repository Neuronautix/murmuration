"""
main.py — CLI Entry Point for Murmuration

Usage:
    python -m automation.main init
    python -m automation.main execute "Your research prompt here"
    python -m automation.main report "Your prompt" --mode narrative-review
    python -m automation.main blueprints
    python -m automation.main info

All behavior is driven by swarm_config.yml. Edit that file to
customize personas, tools, reviewer constraints, and LLM backend.

Human-in-the-Loop (HITL)
-------------------------
When hitl.enabled = true in swarm_config.yml the swarm pauses at configured
checkpoints and prompts the user via the CLI. The graph resumes when the user
presses Enter or provides a redirect answer. This uses LangGraph interrupt()
and Command(resume=...) with a MemorySaver checkpointer.

Stream loop design:
  1. Stream graph with stream_mode="updates" so interrupt events surface
     in the event dict under the "__interrupt__" key.
  2. When an "__interrupt__" event is detected, extract the payload, display
     a formatted prompt, read the user's answer, and resume with
     Command(resume=answer).
  3. When hitl is disabled the loop runs once with no interrupt handling
     and no checkpointer overhead.
"""

import asyncio
import datetime
import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from automation.builder.blueprints import (
    apply_blueprint_overrides,
    default_blueprint_path,
    export_swarm_blueprint,
    load_swarm_blueprint,
)
from automation.builder.doctor import apply_safe_fixes, inspect_swarm, preview_existing_swarm, render_doctor_report
from automation.builder.generator import generate_swarm_project, preview_generation_diff
from automation.builder.loader import load_swarm_spec_from_disk
from automation.builder.templates import (
    build_starter_swarm_spec,
    build_swarm_spec_from_blueprint,
    get_blueprint_descriptions,
    get_blueprint_names,
)
from automation.builder.wizard import (
    build_persona_interactively,
    build_swarm_spec_interactively,
    build_tool_spec_interactively,
    configure_metadata_interactively,
    configure_model_interactively,
    configure_reviewer_interactively,
    configure_team_interactively,
    configure_tools_interactively,
    preview_swarm_spec,
    remove_tool_from_spec,
    upsert_tool_in_spec,
)
from automation.config import (
    findings_slug,
    get_checkpointer_config,
    get_hitl_config,
    get_run_log_path,
    load_config,
    open_checkpointer,
    resolve_swarm_path,
    validate_env,
)
from automation.exporters import export_hcm_mapppp_bundle
from automation.graph import (  # Command re-exported from graph.py
    Command,
    _message_text,
    build_graph,
    compute_recursion_limit,
)
from automation.hcm_pilot import run_hcm_pilot

# Load environment configuration (.env)
load_dotenv()

console = Console()

app = typer.Typer(
    name="swarm",
    help="Murmuration CLI — A configurable multi-agent research system built on LangGraph.",
    add_completion=False,
)
blueprint_app = typer.Typer(help="Export or import portable blueprint files for reuse across repos.")
persona_app = typer.Typer(help="Add or edit personas in the current swarm.")
team_app = typer.Typer(help="Configure orchestrator, routing, and HITL settings.")
review_app = typer.Typer(help="Configure reviewer policy and adversarial checks.")
model_app = typer.Typer(help="Configure model provider, model name, and env key.")
metadata_app = typer.Typer(help="Configure swarm metadata, output paths, and epistemic tags.")
tools_app = typer.Typer(help="Curate the active tool registry for the current swarm.")
export_app = typer.Typer(help="Export scientific proposal bundles.")
pilot_app = typer.Typer(help="Run deterministic domain pilots.")
app.add_typer(blueprint_app, name="blueprint")
app.add_typer(persona_app, name="persona")
app.add_typer(team_app, name="team")
app.add_typer(review_app, name="review")
app.add_typer(model_app, name="model")
app.add_typer(metadata_app, name="metadata")
app.add_typer(tools_app, name="tools")
app.add_typer(export_app, name="export")
app.add_typer(pilot_app, name="pilot")

# ── Report-mode templates ─────────────────────────────────────────────────
REPORT_MODES = {
    "scoping-review": (
        "[REPORT MODE: SCOPING REVIEW] Follow JBI scoping review methodology. "
        "Include: scope and eligibility criteria, search strategy and databases used, "
        "a results charting table, and a discussion of evidence coverage and gaps. "
        "Do NOT provide clinical recommendations — summarise what the literature covers "
        "and where evidence is absent. "
    ),
    "narrative-review": (
        "[REPORT MODE: NARRATIVE REVIEW] Write a structured narrative review suitable "
        "for a psychiatric journal. Include: abstract, introduction, thematic synthesis "
        "of evidence organised by sub-topic, discussion, limitations, and conclusions. "
        "Use formal academic register throughout. "
    ),
    "evidence-brief": (
        "[REPORT MODE: EVIDENCE BRIEF] Write a concise evidence brief (target ~800 words) "
        "for a clinical or policy audience. Use plain language. Structure: "
        "3–5 key findings (bullet points), brief methods note, "
        "2–3 actionable clinical or policy implications, and a caveats paragraph. "
        "Avoid jargon; spell out all acronyms on first use. "
    ),
    # ── Population-specific screening modes ──────────────────────────────
    "pediatric-screen": (
        "[REPORT MODE: PEDIATRIC POPULATION REVIEW] Focus on the evidence base for the "
        "0–18 age group. Address: developmental context and age-specific risk windows; "
        "validated screening instruments for children and adolescents; sex differences "
        "in prevalence and presentation; school-based and family-based intervention options; "
        "safeguarding and consent considerations. State instrument name and cut-off for "
        "every prevalence figure. Distinguish normative developmental variation from "
        "clinically significant findings explicitly. "
    ),
    "young-adult-screen": (
        "[REPORT MODE: YOUNG ADULT POPULATION REVIEW] Focus on the evidence base for "
        "university and college students (18–25). Address: the autonomy transition as "
        "a risk window; academic performance interactions; sleep disruption; ADHD "
        "comorbidity as a vulnerability factor; university counselling service delivery "
        "models; peer-support and digital self-help options. "
        "Name instrument and cut-off for all prevalence claims. "
    ),
    "general-adult-screen": (
        "[REPORT MODE: GENERAL ADULT POPULATION REVIEW] Focus on the evidence base for "
        "adults (18+), distinguishing from presentations in younger populations where "
        "evidence differs. Address: occupational and relationship impairment; "
        "adult-normed measurement instruments; workplace intervention and EAP referral "
        "pathways. Name instrument and cut-off for all prevalence claims. "
    ),
    # ── Specialist prompt packs ───────────────────────────────────────────
    "grant-support": (
        "[REPORT MODE: GRANT SUPPORT] Produce a structured evidence synthesis suitable as "
        "background material for a research grant application. Structure: "
        "(1) Significance — size of the problem, burden of disease, unmet clinical need; "
        "(2) State of Knowledge — key findings, consensus, and gaps; "
        "(3) Innovation — what is unknown and why the proposed work is novel; "
        "(4) Rigour concerns — limitations in existing literature that the proposed study "
        "would address; (5) References — full citations in Vancouver style. "
        "Write in third-person academic register. Flag every prevalence figure with "
        "instrument name and cut-off. Use precise epistemic language (e.g., 'evidence "
        "suggests' rather than 'it is established'). "
    ),
    "manuscript-draft": (
        "[REPORT MODE: MANUSCRIPT DRAFT] Produce a full draft of a peer-reviewable "
        "manuscript section by section. Follow standard psychiatric journal structure: "
        "Abstract (background, methods, results, conclusions; max 250 words), "
        "Introduction (context, gap, aim), Methods (search strategy, inclusion criteria, "
        "data extraction), Results (organised by theme with evidence tables), "
        "Discussion (interpretation, limitations, future directions), "
        "Conclusions, References (Vancouver format). "
        "Write in passive/third-person academic register throughout. "
        "Flag every claim with in-text citation. Ensure limitations section addresses "
        "cross-sectional designs, instrument heterogeneity, and sampling bias. "
    ),
    "journalistic-brief": (
        "[REPORT MODE: JOURNALISTIC BRIEF] Write a plain-language journalism-ready "
        "background brief (target ~600 words) for a science journalist or public "
        "health communicator. Structure: lead paragraph summarising the key takeaway "
        "in two sentences; 'What the research shows' (3–4 bullet points); "
        "'What experts caution' (2–3 bullet points noting caveats and uncertainty); "
        "'What this means in practice' (1–2 sentences); 'Key numbers to use' "
        "(prevalence figures with instrument noted in parentheses). "
        "Avoid clinical jargon. Do not overstate causal claims. "
        "Explicitly flag any findings that are preliminary or from small samples. "
    ),
}

MATRIX_HEADER = (
    "# Knowledge Traceability Matrix\n\n"
    "| Source | Author/Agent | Claim | Method | Epistemic Tag |\n"
    "|--------|-------------|-------|--------|---------------|\n"
)


# ── Helper functions ──────────────────────────────────────────────────────

def _ensure_matrix_header(config: dict, task: str, resumed_thread: str = "", thread_id: str = "") -> None:
    """Create the traceability matrix with its table header if it does not exist,
    then append a run-separator line so each run's entries are clearly delimited."""
    matrix_path = resolve_swarm_path(config, "traceability_matrix")
    matrix_path.parent.mkdir(parents=True, exist_ok=True)
    if not matrix_path.exists():
        matrix_path.write_text(MATRIX_HEADER, encoding="utf-8")

    run_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    task_preview = task[:80] + "…" if len(task) > 80 else task
    # The thread id lets the reviewer's matrix excerpt find this run's rows again after a resume.
    if resumed_thread:
        separator = f"### Resumed run {resumed_thread}: {run_stamp} | Task: {task_preview}"
    elif thread_id:
        separator = f"### Run: {run_stamp} | Thread: {thread_id} | Task: {task_preview}"
    else:
        separator = f"### Run: {run_stamp} | Task: {task_preview}"
    with open(matrix_path, "a", encoding="utf-8") as f:
        f.write(f"\n{separator}\n\n")


def _write_run_metrics(config: dict, task: str, token_usage: dict) -> None:
    """Write per-run token/cost metrics to <swarm.output_dir>/run_metrics.json."""
    drafts_dir = resolve_swarm_path(config, "output_dir")
    drafts_dir.mkdir(parents=True, exist_ok=True)

    # Defaults are gpt-4o list prices ($/M tokens, 2025). Override per model with
    # model.pricing: {input_per_m: ..., output_per_m: ...} in swarm_config.yml.
    pricing = config["model"].get("pricing") or {}
    input_price_per_m = float(pricing.get("input_per_m", 2.50))
    output_price_per_m = float(pricing.get("output_per_m", 10.00))
    input_tok = token_usage.get("input_tokens", 0)
    output_tok = token_usage.get("output_tokens", 0)
    est_cost = (input_tok * input_price_per_m + output_tok * output_price_per_m) / 1_000_000

    metrics = {
        "run_date": datetime.datetime.now().isoformat(timespec="seconds"),
        "task": task[:100] + "…" if len(task) > 100 else task,
        "model": f"{config['model']['provider']}/{config['model']['name']}",
        "input_tokens": input_tok,
        "output_tokens": output_tok,
        "total_tokens": token_usage.get("total_tokens", input_tok + output_tok),
        "estimated_cost_usd": round(est_cost, 4),
        "cost_note": (
            f"Estimate uses ${input_price_per_m:.2f}/M input and ${output_price_per_m:.2f}/M output"
            + (" (model.pricing from swarm_config.yml)." if pricing else
               " (gpt-4o list prices; set model.pricing in swarm_config.yml for your model).")
        ),
    }

    metrics_path = drafts_dir / "run_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    typer.secho(
        f"\n📊 Token usage: {input_tok:,} in / {output_tok:,} out "
        f"(est. ${est_cost:.4f})",
        fg=typer.colors.CYAN,
    )


def _display_event_messages(event: dict) -> None:
    """Print progress messages extracted from a stream 'updates' event."""
    for node_name, update in event.items():
        if node_name == "__interrupt__":
            continue
        if not isinstance(update, dict):
            continue
        msgs = update.get("messages", [])
        if not msgs:
            continue
        msg = msgs[-1]
        if not hasattr(msg, "content") or not msg.content:
            continue
        content = str(msg.content)

        if content.startswith("[Orchestrator"):
            typer.secho(f"  ↳ {content}", fg=typer.colors.CYAN)
        elif content.startswith("[Reviewer"):
            colour = typer.colors.GREEN if "APPROVED" in content else typer.colors.RED
            typer.secho(f"  {content}", fg=colour)
        elif content.startswith("[") and "]: " in content:
            typer.secho(f"  {content}", fg=typer.colors.GREEN)
        elif hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                typer.secho(
                    f"    🔧 {tc['name']} ← {list(tc['args'].keys())}",
                    fg=typer.colors.YELLOW,
                )


class _RunRecorder:
    """Writes the optional run artefacts of one run as the graph streams.

    swarm.run_log           one JSON line per node update (plus run start/end
                            and HITL answers), appended as it happens.
    swarm.persist_findings  <output_dir>/findings/<persona-slug>.md, rewritten
                            after every specialist update.

    Both are off by default. Each write opens and closes its file, so a crash
    keeps everything recorded up to that point. A write that fails (locked file,
    full disk) disables that output with a warning instead of stopping the run.
    """

    def __init__(self, config: dict, thread_id: str = ""):
        self.log_path = get_run_log_path(config)
        self.findings_dir = (
            resolve_swarm_path(config, "output_dir") / "findings"
            if (config.get("swarm") or {}).get("persist_findings") else None
        )
        self.journalist = (config.get("orchestrator") or {}).get("journalist")
        # Lines from several runs can share one file: every line carries the run id.
        self.run_id = thread_id or uuid.uuid4().hex
        self.started = datetime.datetime.now().isoformat(timespec="seconds")
        self.pending_answer = None

    def _disable(self, what: str, error: OSError) -> None:
        typer.secho(f"⚠️  Could not write {what} ({error}); it is disabled for the rest of this run.",
                    fg=typer.colors.YELLOW)

    def _write(self, record: dict) -> None:
        if self.log_path is None:
            return
        record = {"timestamp": datetime.datetime.now().isoformat(timespec="milliseconds"),
                  "run_id": self.run_id, **record}
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            self._disable(f"swarm.run_log {self.log_path}", e)
            self.log_path = None

    def start(self, task: str, resumed: bool = False) -> None:
        self._write({"event": "run_resume" if resumed else "run_start", "task": task})

    def hitl_answer(self, checkpoint: str, answer: str) -> None:
        # Attached to the checkpoint node's update, which is the first one after the resume.
        self.pending_answer = {"checkpoint": checkpoint, "answer": answer}

    def node(self, node_name: str, update: dict) -> None:
        record = {"event": "node", "node": node_name}
        for key, field_name in (("next_agents", "next_agents"), ("routing_reasoning", "routing_reasoning"),
                                ("agent_assignments", "assignments"), ("token_usage", "token_delta"),
                                ("final_status", "final_status")):
            if key in update:
                record[field_name] = update[key]
        if "reviewer_approved" in update:
            approved = update["reviewer_approved"]
            record["verdict"] = update.get("final_status") or ("approved" if approved else "rejected")
            feedback = update.get("reviewer_feedback") or update.get("next_instructions")
            if not approved and feedback:
                record["feedback"] = feedback
            if update.get("review_route"):
                record["review_route"] = update["review_route"]
        if update.get("agent_outputs"):
            record["outputs_from"] = list(update["agent_outputs"])
        if update.get("messages"):
            record["message"] = _message_text(update["messages"][-1])[:500]
        if self.pending_answer is not None:
            record["hitl"] = self.pending_answer
            self.pending_answer = None
        self._write(record)
        self._persist_findings(update.get("agent_outputs") or {})

    def _persist_findings(self, outputs: dict) -> None:
        if self.findings_dir is None:
            return
        try:
            for name, text in outputs.items():
                if name == self.journalist:
                    continue
                self.findings_dir.mkdir(parents=True, exist_ok=True)
                (self.findings_dir / f"{findings_slug(name)}.md").write_text(
                    f"# Findings — {name}\n\n<!-- run {self.run_id}, started {self.started} -->\n\n{text}\n",
                    encoding="utf-8",
                )
        except OSError as e:
            self._disable(f"swarm.persist_findings in {self.findings_dir}", e)
            self.findings_dir = None

    def end(self, final_status: str, token_usage: dict, error: BaseException = None) -> None:
        record = {"event": "run_end", "final_status": final_status, "token_usage": token_usage}
        if error is not None:
            record["error"] = f"{type(error).__name__}: {error}"
        self._write(record)


def _handle_hitl_interrupt(interrupt_objs) -> str:
    """Display HITL checkpoint prompt and return the user's answer."""
    # interrupt_objs is a tuple/list of Interrupt(value=...) from the stream
    # We handle the last interrupt if multiple (shouldn't happen in practice)
    payload = {}
    for intr in interrupt_objs:
        payload = intr.value if hasattr(intr, "value") else intr

    checkpoint = payload.get("checkpoint", "checkpoint")
    question = payload.get("question", "Continue?")
    default = payload.get("default", "")

    typer.secho(f"\n{'─' * 55}", fg=typer.colors.CYAN)
    typer.secho(f"  ⏸  HITL Checkpoint: {checkpoint}", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"{'─' * 55}", fg=typer.colors.CYAN)
    for line in question.splitlines():
        typer.secho(f"  {line}", fg=typer.colors.WHITE)
    typer.secho(f"{'─' * 55}\n", fg=typer.colors.CYAN)

    answer = typer.prompt(
        "Your response",
        default=default,
        show_default=bool(default),
    )
    return answer


class ResumeError(ValueError):
    """A saved run cannot be continued (unknown thread, or a configuration that no longer matches)."""


async def _run_graph_async(config: dict, prompt: str, graph, run_config: dict,
                           resume: bool = False) -> tuple[dict, str]:
    """Async inner loop: streams the compiled graph and handles HITL interrupts.

    Returns (accumulated token_usage dict, final_status). final_status is the
    last value any node reported: approved | force_approved | overridden |
    completed | failed, or "" if no node set one.
    Specialist and Journalist nodes are async (using _run_tool_loop_async) so
    multiple tool calls within a single agent turn run concurrently.

    With ``resume`` the run continues the thread in ``run_config`` from its last
    checkpoint instead of starting from ``prompt``: a pending HITL question is
    asked again, otherwise the steps that had not finished are re-run. Steps that
    finished before a crash are not repeated.
    """
    final_token_usage: dict = {}
    final_status = ""
    current_input: dict | Command = {
        "task": prompt,
        "messages": [],
        "agent_outputs": {},
        "agent_assignments": {},
        "next_agents": [],
        "next_instructions": "",
        "journalist_instructions": "",
        "user_framing": "",
        "routing_reasoning": "",
        "plan_reviewed": False,
        "agent_call_count": 0,
        "reviewer_approved": False,
        "revision_count": 0,
        "reviewer_feedback": "",
        "reviewer_requests": [],
        "review_route": "",
        "evidence_loop_count": 0,
        "specialist_rounds": 0,
        "claims": {},
        "critique_done": False,
        "final_status": "",
        "token_usage": {},
    }

    thread_id = ((run_config or {}).get("configurable") or {}).get("thread_id", "")
    if resume:
        snapshot = await graph.aget_state(run_config)
        if not snapshot.values:
            raise ResumeError(f"No saved run found for thread '{thread_id}' in the configured checkpointer.")
        missing = [name for name in snapshot.next if name not in graph.nodes]
        if not snapshot.next and not snapshot.values.get("final_status"):
            # Every finished run sets final_status; an empty `next` without one means the
            # rebuilt graph has no route from where the run stopped.
            missing = ["(a step the current configuration does not build)"]
        if missing:
            raise ResumeError(
                f"Thread '{thread_id}' stopped before {missing}, which the current configuration does not "
                "build (was swarm_config.yml edited, e.g. HITL turned off or a persona renamed?). "
                "Resume with the configuration the run was started with."
            )
        prompt = snapshot.values.get("task", prompt)
        final_token_usage = dict(snapshot.values.get("token_usage") or {})
        final_status = snapshot.values.get("final_status", "") or ""
        if not snapshot.next:
            typer.secho(f"Thread {thread_id} has already finished; nothing to resume.", fg=typer.colors.YELLOW)
            return final_token_usage, final_status
        pending = [intr for task in snapshot.tasks for intr in (getattr(task, "interrupts", None) or ())]
        current_input = None
        if pending:
            current_input = Command(resume=_handle_hitl_interrupt(pending))

    recorder = _RunRecorder(config, thread_id)
    recorder.start(prompt, resumed=resume)
    if resume and current_input is not None:
        recorder.hitl_answer(_interrupt_checkpoint(pending), current_input.resume)

    typer.secho("\n🧠 [SWARM ACTIVE] Streaming agent interactions...\n", fg=typer.colors.BLUE)

    try:
        while True:
            interrupted = False

            async for event in graph.astream(
                current_input,
                config=run_config,
                stream_mode="updates",
            ):
                meta = event.get("__metadata__")
                if isinstance(meta, dict) and meta.get("cached"):
                    # On resume, LangGraph replays the saved writes of steps that finished
                    # before the crash; they are already in the checkpoint's totals.
                    continue
                # Accumulate token_usage from any node update that carries it
                for node_name, update in event.items():
                    if node_name.startswith("__") or not isinstance(update, dict):
                        continue
                    if "token_usage" in update:
                        for k, v in update["token_usage"].items():
                            if isinstance(v, (int, float)):
                                final_token_usage[k] = final_token_usage.get(k, 0) + v
                    if update.get("final_status"):
                        final_status = update["final_status"]
                    recorder.node(node_name, update)

                # Check for interrupt before displaying messages
                if "__interrupt__" in event:
                    interrupt_objs = event["__interrupt__"]
                    answer = _handle_hitl_interrupt(interrupt_objs)
                    recorder.hitl_answer(_interrupt_checkpoint(interrupt_objs), answer)
                    current_input = Command(resume=answer)
                    interrupted = True
                    break

                _display_event_messages(event)

            if not interrupted:
                break  # Graph completed naturally
        if resume:
            final_state = await graph.aget_state(run_config)
            if final_state.next:
                raise ResumeError(f"The resumed run stopped before {list(final_state.next)} without finishing.")
            # The checkpoint holds the whole run's total, including steps finished before the crash.
            final_token_usage = dict(final_state.values.get("token_usage") or final_token_usage)
    except BaseException as e:  # Ctrl-C and cancellation are logged too
        recorder.end("interrupted" if isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)) else "error",
                     final_token_usage, error=e)
        raise

    recorder.end(final_status, final_token_usage)
    return final_token_usage, final_status


def _interrupt_checkpoint(interrupt_objs) -> str:
    """Name of the checkpoint answered by _handle_hitl_interrupt (which answers the last interrupt)."""
    checkpoint = "checkpoint"
    for intr in interrupt_objs:
        payload = intr.value if hasattr(intr, "value") else intr
        checkpoint = payload.get("checkpoint", "checkpoint") if isinstance(payload, dict) else "checkpoint"
    return checkpoint


def _run_graph(config: dict, prompt: str, resume_thread: str | None = None) -> None:
    """Core graph execution shared by `execute` and `report` commands.

    ``resume_thread`` continues a saved run (checkpointer.type: sqlite) instead
    of starting one; ``prompt`` is then ignored in favour of the saved task.

    Delegates to _run_graph_async via asyncio.run so that specialist nodes
    can execute multiple tool calls concurrently within each agent turn.
    Uses astream (stream_mode="updates") so interrupt events appear under
    the "__interrupt__" key in the event dict.
    """
    try:
        warnings = validate_env(config)
        for w in warnings:
            typer.secho(f"⚠️  {w}", fg=typer.colors.YELLOW)
    except EnvironmentError as e:
        typer.secho(f"ENV ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    checkpointer_cfg = get_checkpointer_config(config)
    if resume_thread and checkpointer_cfg["type"] != "sqlite":
        typer.secho(
            "RESUME ERROR: --resume needs a checkpointer that outlives the process. "
            "Set `checkpointer: {type: sqlite}` in swarm_config.yml (and install the 'persist' extra).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    # A thread id is needed whenever there is a checkpointer; it also tags this run's matrix section.
    thread_id = resume_thread or (str(uuid.uuid4()) if checkpointer_cfg["type"] != "none" else "")
    if not resume_thread:
        _ensure_matrix_header(config, prompt, thread_id=thread_id)

    hitl_cfg = get_hitl_config(config)
    hitl_enabled = hitl_cfg.get("enabled", False)

    # ── Pre-flight HITL: simple CLI confirm, no graph interrupt needed ───
    if not resume_thread and hitl_enabled and "pre_flight" in hitl_cfg.get("checkpoints", []):
        typer.secho("\n⏸  HITL Pre-flight", fg=typer.colors.CYAN, bold=True)
        typer.secho(f"  Task: {prompt[:120]}", fg=typer.colors.WHITE)
        if not typer.confirm("  Proceed with this task?", default=True):
            typer.secho("Aborted by user.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)

    swarm_name = config["swarm"]["name"]
    typer.secho(f"\n🚀 Initializing {swarm_name}...", fg=typer.colors.CYAN)
    if hitl_enabled:
        typer.secho(
            f"   HITL active — checkpoints: {hitl_cfg.get('checkpoints', [])}",
            fg=typer.colors.CYAN,
        )

    # A thread id is required whenever there is a checkpointer. The recursion
    # limit is derived from the configured safety valves so LangGraph's own step
    # guard can never fire before max_agent_calls / max_revision_loops do.
    run_config = {"recursion_limit": compute_recursion_limit(config)}
    if thread_id:
        run_config["configurable"] = {"thread_id": thread_id}
        typer.secho(f"🧵 Thread: {thread_id}", fg=typer.colors.CYAN)
    resume_hint = (
        f"↻  Resume with: python -m automation.main execute --resume {thread_id}"
        if checkpointer_cfg["type"] == "sqlite" else ""
    )

    async def _execute():
        # The checkpointer (an aiosqlite connection for sqlite) must live in this event loop.
        async with open_checkpointer(config) as saver:
            graph = build_graph(config, checkpointer=saver)
            task, finished = prompt, False
            if resume_thread:
                snapshot = await graph.aget_state(run_config)
                task = (snapshot.values or {}).get("task", "")
                finished = bool(snapshot.values) and not snapshot.next and bool(
                    snapshot.values.get("final_status"))
                if snapshot.values and not finished:
                    _ensure_matrix_header(config, task, resumed_thread=resume_thread)
            usage, status = await _run_graph_async(config, prompt, graph, run_config, resume=bool(resume_thread))
            return usage, status, task, finished

    try:
        final_token_usage, final_status, prompt, already_finished = asyncio.run(_execute())
    except Exception as e:
        typer.secho(f"\nExecution Error: {e}", fg=typer.colors.RED)
        if get_run_log_path(config):
            typer.secho(f"🧾 Run log: {get_run_log_path(config)}", fg=typer.colors.CYAN)
        if resume_hint and not isinstance(e, ResumeError):
            typer.secho(resume_hint, fg=typer.colors.CYAN)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        if resume_hint:
            typer.secho(f"\nInterrupted. {resume_hint}", fg=typer.colors.CYAN)
        raise

    if already_finished:
        return

    output_dir = resolve_swarm_path(config, "output_dir")
    _write_run_metrics(config, prompt, final_token_usage)
    run_log = get_run_log_path(config)
    if run_log:
        typer.secho(f"🧾 Run log: {run_log}", fg=typer.colors.CYAN)

    if final_status == "failed":
        typer.secho(
            "\n❌ Run finished without a usable draft: the Journalist produced no text, or Reviewer-2 "
            "rejected the final draft (reviewer.on_max_revisions: fail). See the messages above.",
            fg=typer.colors.RED,
        )
        typer.secho(f"📂 Partial outputs, if any, are in '{output_dir}/'", fg=typer.colors.CYAN)
        raise typer.Exit(code=1)

    if final_status == "force_approved":
        typer.secho(
            "\n⚠️  Task complete, but Reviewer-2 never approved the draft: it was force-approved "
            "after reviewer.max_revision_loops rejections. Review it before use.",
            fg=typer.colors.YELLOW,
        )
    elif final_status == "overridden":
        typer.secho("\n✅ Task complete (draft approved by user override).", fg=typer.colors.GREEN)
    else:
        typer.secho("\n✅ Task complete.", fg=typer.colors.GREEN)
    typer.secho(f"📂 Outputs saved to '{output_dir}/'", fg=typer.colors.CYAN)


def _supported_blueprints_text() -> str:
    return ", ".join(get_blueprint_names())


def _build_init_spec(
    domain: str,
    name: str,
    description: str,
    template: str,
    interactive: bool,
):
    if template not in get_blueprint_names():
        raise ValueError(
            f"Unknown template '{template}'. Supported templates: {_supported_blueprints_text()}"
        )

    if interactive:
        return build_swarm_spec_interactively(
            domain=domain or None,
            swarm_name=name or None,
            description=description or None,
            blueprint=template,
        )

    domain_value = domain or "General Research"
    return build_swarm_spec_from_blueprint(
        blueprint=template,
        domain=domain_value,
        swarm_name=name or f"{domain_value} Swarm",
        swarm_description=description or f"Autonomous multi-agent research swarm for {domain_value}",
        model_provider="openai",
        model_name="gpt-4o",
        model_env_key="OPENAI_API_KEY",
    )


def _write_swarm_from_spec(spec, root_dir: Path, force: bool, yes: bool, confirm_text: str) -> None:
    preview_generation_diff(spec, root_dir)

    if not yes and not typer.confirm(confirm_text, default=True):
        typer.secho("Init aborted before writing files.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        result = generate_swarm_project(spec, root_dir=root_dir, force=force)
        load_config(result.config_path)
    except Exception as e:
        typer.secho(f"INIT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("\nSwarm files generated successfully.", fg=typer.colors.GREEN)
    for path in result.written_files:
        typer.secho(f"  • {path.relative_to(root_dir)}", fg=typer.colors.CYAN)

    typer.secho(
        "\nNext steps:\n"
        "  1. Review swarm_config.yml and the generated persona files\n"
        "  2. Add any reference files to agents/*/KB/\n"
        "  3. Run: python -m automation.ingest\n"
        "  4. Run: python -m automation.main info",
        fg=typer.colors.CYAN,
    )


# ── CLI commands ──────────────────────────────────────────────────────────

@app.command()
def init(
    domain: str = typer.Option("", help="Target research domain for the swarm."),
    name: str = typer.Option("", help="Swarm name to generate."),
    description: str = typer.Option("", help="Short swarm description."),
    template: str = typer.Option(
        "research-core",
        help="Starter team template. Run 'python -m automation.main blueprints' to view options.",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Use guided prompts instead of default starter values.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite generated files if they already exist.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the final confirmation prompt.",
    ),
):
    """
    Create a new swarm configuration and persona files from a builder-backed spec.

    This command uses the builder core plus the interactive wizard layer. It
    generates a valid swarm_config.yml plus persona markdown files and KB
    directories.
    """
    root_dir = Path.cwd()
    config_path = root_dir / "swarm_config.yml"

    if config_path.exists() and not force:
        typer.secho(
            "swarm_config.yml already exists in the current directory. "
            "Use --force to overwrite generated files.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    try:
        spec = _build_init_spec(
            domain=domain,
            name=name,
            description=description,
            template=template,
            interactive=interactive,
        )
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if not interactive:
        preview_swarm_spec(spec)

    _write_swarm_from_spec(spec, root_dir, force=force, yes=yes, confirm_text="Write these files to the current repo?")


@app.command()
def blueprints():
    """
    List the available starter blueprints for the builder-backed init flow.
    """
    descriptions = get_blueprint_descriptions()
    table = Table(title="Swarm Blueprints")
    table.add_column("Name")
    table.add_column("Description")
    for name in get_blueprint_names():
        table.add_row(name, descriptions.get(name, ""))
    console.print(table)


@blueprint_app.command("export")
def blueprint_export(
    output: str = typer.Option("", help="Blueprint file path. Defaults to ./blueprints/<name>.swarm-blueprint.yml"),
    name: str = typer.Option("", help="Blueprint display name. Defaults to the current swarm name."),
    description: str = typer.Option("", help="Blueprint description. Defaults to the current swarm description."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Overwrite an existing blueprint file."),
):
    """
    Export the current swarm as a portable blueprint YAML file.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
    except Exception as e:
        typer.secho(f"BLUEPRINT EXPORT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    blueprint_name = name or spec.swarm_name
    output_path = Path(output).expanduser() if output else default_blueprint_path(root_dir, blueprint_name)
    if output_path.exists() and not overwrite:
        typer.secho(
            f"Blueprint file already exists: {output_path}. Use --overwrite to replace it.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    try:
        written_path = export_swarm_blueprint(
            spec,
            output_path=output_path,
            blueprint_name=blueprint_name,
            blueprint_description=description or spec.swarm_description,
        )
    except Exception as e:
        typer.secho(f"BLUEPRINT EXPORT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Exported blueprint to {written_path}", fg=typer.colors.GREEN)


@blueprint_app.command("import")
def blueprint_import(
    file: str = typer.Argument(..., help="Path to a .swarm-blueprint.yml file."),
    name: str = typer.Option("", help="Override swarm name when importing."),
    description: str = typer.Option("", help="Override swarm description when importing."),
    domain: str = typer.Option("", help="Override domain label when importing."),
    force: bool = typer.Option(False, "--force", help="Overwrite generated files if they already exist."),
    yes: bool = typer.Option(False, "--yes", help="Skip the final confirmation prompt."),
):
    """
    Import a portable blueprint file and generate a swarm in the current repo.
    """
    root_dir = Path.cwd()
    config_path = root_dir / "swarm_config.yml"
    if config_path.exists() and not force:
        typer.secho(
            "swarm_config.yml already exists in the current directory. Use --force to overwrite generated files.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    blueprint_path = Path(file).expanduser()
    try:
        blueprint_meta, spec = load_swarm_blueprint(blueprint_path)
        spec = apply_blueprint_overrides(
            spec,
            swarm_name=name or None,
            swarm_description=description or None,
            domain=domain or None,
        )
    except Exception as e:
        typer.secho(f"BLUEPRINT IMPORT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(
        f"Importing blueprint: {blueprint_meta.get('name', blueprint_path.name)}",
        fg=typer.colors.CYAN,
    )
    preview_swarm_spec(spec)
    _write_swarm_from_spec(spec, root_dir, force=force, yes=yes, confirm_text="Write these files to the current repo?")


@app.command()
def preview():
    """
    Render a structured preview of the current swarm configuration.
    """
    root_dir = Path.cwd()
    try:
        preview_existing_swarm(root_dir)
    except Exception as e:
        typer.secho(f"PREVIEW ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command()
def doctor(
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Apply safe automatic fixes for missing KB directories and .gitkeep files.",
    ),
):
    """
    Validate the current swarm configuration, persona files, and tool wiring.
    """
    root_dir = Path.cwd()
    if fix:
        try:
            created = apply_safe_fixes(root_dir)
            if created:
                typer.secho("Applied safe fixes:", fg=typer.colors.CYAN)
                for path in created:
                    typer.secho(f"  • {path.relative_to(root_dir)}", fg=typer.colors.CYAN)
        except Exception as e:
            typer.secho(f"DOCTOR FIX ERROR: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
    report = inspect_swarm(root_dir)
    render_doctor_report(report)
    if not report.ok:
        raise typer.Exit(code=1)


@persona_app.command("add")
def persona_add(
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Add a new persona to the current swarm and preview the resulting file diffs.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
    except Exception as e:
        typer.secho(f"PERSONA ADD ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    new_persona = build_persona_interactively(spec.domain)
    if any(persona.name == new_persona.name for persona in spec.personas):
        typer.secho(f"Persona '{new_persona.name}' already exists.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    spec.personas.append(new_persona)

    preview_swarm_spec(spec)
    preview_generation_diff(spec, root_dir)
    if not yes and not typer.confirm("Write these persona changes?", default=True):
        typer.secho("Persona add aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(spec, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"PERSONA ADD ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Added persona '{new_persona.name}'.", fg=typer.colors.GREEN)


@persona_app.command("edit")
def persona_edit(
    name: str = typer.Option("", "--name", help="Existing persona name to edit."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Edit an existing persona in the current swarm and preview the file diffs.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
    except Exception as e:
        typer.secho(f"PERSONA EDIT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    persona_names = [persona.name for persona in spec.personas]
    target_name = name or typer.prompt("Persona to edit", default=persona_names[0])
    matching = next((persona for persona in spec.personas if persona.name == target_name), None)
    if matching is None:
        typer.secho(f"Persona '{target_name}' not found.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    updated = build_persona_interactively(spec.domain, existing_persona=matching)
    old_name = matching.name
    for index, persona in enumerate(spec.personas):
        if persona.name == old_name:
            spec.personas[index] = updated
            break

    if spec.orchestrator_agent == old_name:
        spec.orchestrator_agent = updated.name
    if spec.journalist_agent == old_name:
        spec.journalist_agent = updated.name

    preview_swarm_spec(spec)
    preview_generation_diff(spec, root_dir)
    if not yes and not typer.confirm("Write these persona edits?", default=True):
        typer.secho("Persona edit aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(spec, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"PERSONA EDIT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Updated persona '{updated.name}'.", fg=typer.colors.GREEN)


@team_app.command("configure")
def team_configure(
    orchestrator: str = typer.Option("", help="Persona name to use as orchestrator."),
    journalist: str = typer.Option("", help="Persona name to use as journalist/final writer."),
    max_agent_calls: int = typer.Option(0, help="Maximum specialist dispatches; 0 keeps current value."),
    max_tool_rounds: int = typer.Option(0, help="Maximum tool rounds per specialist; 0 keeps current value."),
    hitl: str = typer.Option("", help="HITL mode: keep, enable, or disable."),
    checkpoints: str = typer.Option("", help="Comma-separated HITL checkpoints."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Configure orchestrator selection, routing limits, and HITL settings.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
    except Exception as e:
        typer.secho(f"TEAM CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    hitl_mode = hitl or None
    checkpoint_list = [item.strip() for item in checkpoints.split(",") if item.strip()] or None
    try:
        updated = configure_team_interactively(
            spec,
            orchestrator_name=orchestrator or None,
            journalist_name=journalist or None,
            max_agent_calls=max_agent_calls or None,
            max_tool_rounds=max_tool_rounds or None,
            hitl_mode=hitl_mode,
            hitl_checkpoints=checkpoint_list,
        )
    except Exception as e:
        typer.secho(f"TEAM CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write these team configuration changes?", default=True):
        typer.secho("Team configure aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"TEAM CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("Updated team configuration.", fg=typer.colors.GREEN)


@review_app.command("configure")
def review_configure(
    mode: str = typer.Option("", help="Reviewer mode: keep, enable, or disable."),
    max_revision_loops: int = typer.Option(0, help="Reviewer max revision loops; 0 keeps current value."),
    tone: str = typer.Option("", help="Reviewer tone override."),
    banned_words: str = typer.Option("", help="Comma-separated banned words list."),
    required_elements: str = typer.Option("", help="Comma-separated required elements list."),
    rejection_patterns: str = typer.Option("", help="Comma-separated rejection patterns list."),
    reviewer_model_name: str = typer.Option("", help="Reviewer model name; blank keeps current or inherited model."),
    reviewer_model_temperature: float = typer.Option(-1.0, help="Reviewer model temperature; negative keeps current value."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Configure reviewer policy, tone, bans, and adversarial model overrides.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
    except Exception as e:
        typer.secho(f"REVIEW CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    banned_list = [item.strip() for item in banned_words.split(",") if item.strip()] or None
    required_list = [item.strip() for item in required_elements.split(",") if item.strip()] or None
    rejection_list = [item.strip() for item in rejection_patterns.split(",") if item.strip()] or None
    reviewer_temp = reviewer_model_temperature if reviewer_model_temperature >= 0 else None
    reviewer_name_value = reviewer_model_name if reviewer_model_name else None

    try:
        updated = configure_reviewer_interactively(
            spec,
            reviewer_mode=mode or None,
            max_revision_loops=max_revision_loops or None,
            tone=tone or None,
            banned_words=banned_list,
            required_elements=required_list,
            rejection_patterns=rejection_list,
            reviewer_model_name=reviewer_name_value,
            reviewer_model_temperature=reviewer_temp,
        )
    except Exception as e:
        typer.secho(f"REVIEW CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write these reviewer configuration changes?", default=True):
        typer.secho("Review configure aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"REVIEW CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("Updated reviewer configuration.", fg=typer.colors.GREEN)


@model_app.command("configure")
def model_configure(
    provider: str = typer.Option("", help="Model provider: openai, anthropic, or google."),
    name: str = typer.Option("", help="Primary model name."),
    temperature: float = typer.Option(-1.0, help="Primary model temperature; negative keeps current value."),
    env_key: str = typer.Option("", help="Environment variable name for the model API key."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Configure the primary model provider, model name, temperature, and env key.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        updated = configure_model_interactively(
            spec,
            provider=provider or None,
            model_name=name or None,
            temperature=temperature if temperature >= 0 else None,
            env_key=env_key or None,
        )
    except Exception as e:
        typer.secho(f"MODEL CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write these model configuration changes?", default=True):
        typer.secho("Model configure aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"MODEL CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("Updated model configuration.", fg=typer.colors.GREEN)


@metadata_app.command("configure")
def metadata_configure(
    swarm_name: str = typer.Option("", help="Swarm display name."),
    description: str = typer.Option("", help="Swarm description."),
    domain: str = typer.Option("", help="Domain label used by the builder."),
    output_dir: str = typer.Option("", help="Output directory path."),
    traceability_matrix: str = typer.Option("", help="Traceability matrix path."),
    epistemic_tags: str = typer.Option("", help="Comma-separated epistemic tags list."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Configure swarm-level metadata, paths, and epistemic tags.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        updated = configure_metadata_interactively(
            spec,
            swarm_name=swarm_name or None,
            swarm_description=description or None,
            domain=domain or None,
            output_dir=output_dir or None,
            traceability_matrix=traceability_matrix or None,
            epistemic_tags=[item.strip() for item in epistemic_tags.split(",") if item.strip()] or None,
        )
    except Exception as e:
        typer.secho(f"METADATA CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write these metadata changes?", default=True):
        typer.secho("Metadata configure aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"METADATA CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("Updated swarm metadata.", fg=typer.colors.GREEN)


@tools_app.command("configure")
def tools_configure(
    enabled_tools: str = typer.Option("", help="Comma-separated active tool registry keys."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Curate the active tool registry and sync persona tool scopes to the selected set.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        enabled = [item.strip() for item in enabled_tools.split(",") if item.strip()] or None
        updated = configure_tools_interactively(spec, enabled_tools=enabled)
    except Exception as e:
        typer.secho(f"TOOLS CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write these tool registry changes?", default=True):
        typer.secho("Tools configure aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"TOOLS CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("Updated tool registry configuration.", fg=typer.colors.GREEN)


@tools_app.command("add")
def tools_add(
    key: str = typer.Option("", help="Tool registry key to add."),
    builtin: str = typer.Option("", help="Built-in tool key to import into the active registry."),
    module: str = typer.Option("", help="Python module path for a custom tool."),
    function: str = typer.Option("", help="Function name for a custom tool."),
    description: str = typer.Option("", help="Description for the tool."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Add a built-in or custom tool to the active swarm registry.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        tool_key, tool_spec = build_tool_spec_interactively(
            spec,
            tool_key=key or None,
            builtin_key=builtin or None,
            module=module or None,
            function=function or None,
            description=description or None,
        )
        updated = upsert_tool_in_spec(spec, tool_key, tool_spec)
    except Exception as e:
        typer.secho(f"TOOLS ADD ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write this tool addition?", default=True):
        typer.secho("Tools add aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"TOOLS ADD ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Added tool '{tool_key}'.", fg=typer.colors.GREEN)


@tools_app.command("edit")
def tools_edit(
    key: str = typer.Option("", help="Existing tool registry key to edit."),
    module: str = typer.Option("", help="Updated module path."),
    function: str = typer.Option("", help="Updated function name."),
    description: str = typer.Option("", help="Updated description."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Edit an existing tool registry entry.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        tool_keys = list(spec.tool_registry.keys())
        target_key = key or typer.prompt("Tool to edit", default=tool_keys[0])
        existing_spec = spec.tool_registry.get(target_key)
        if existing_spec is None:
            raise ValueError(f"tool '{target_key}' not found in active registry")
        new_key, tool_spec = build_tool_spec_interactively(
            spec,
            tool_key=target_key,
            existing_spec=existing_spec,
            module=module or None,
            function=function or None,
            description=description or None,
        )
        updated = spec.model_copy(deep=True)
        if new_key != target_key:
            del updated.tool_registry[target_key]
            for persona in updated.personas:
                persona.tools = [new_key if tool == target_key else tool for tool in persona.tools]
        updated = upsert_tool_in_spec(updated, new_key, tool_spec)
    except Exception as e:
        typer.secho(f"TOOLS EDIT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write this tool edit?", default=True):
        typer.secho("Tools edit aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"TOOLS EDIT ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Updated tool '{new_key}'.", fg=typer.colors.GREEN)


@tools_app.command("remove")
def tools_remove(
    key: str = typer.Option("", help="Existing tool registry key to remove."),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation after preview."),
):
    """
    Remove a tool from the active registry if all personas retain at least one tool.
    """
    root_dir = Path.cwd()
    try:
        spec = load_swarm_spec_from_disk(root_dir)
        tool_keys = list(spec.tool_registry.keys())
        target_key = key or typer.prompt("Tool to remove", default=tool_keys[0])
        updated = remove_tool_from_spec(spec, target_key)
    except Exception as e:
        typer.secho(f"TOOLS REMOVE ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    preview_swarm_spec(updated)
    preview_generation_diff(updated, root_dir)
    if not yes and not typer.confirm("Write this tool removal?", default=True):
        typer.secho("Tools remove aborted.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    try:
        generate_swarm_project(updated, root_dir=root_dir, force=True)
    except Exception as e:
        typer.secho(f"TOOLS REMOVE ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho(f"Removed tool '{target_key}'.", fg=typer.colors.GREEN)

@app.command()
def execute(
    prompt: Optional[str] = typer.Argument(None, help="Research prompt (omit with --resume)."),
    config_path: Path = typer.Option(None, "--config", help="Swarm configuration YAML"),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Continue a saved run by its thread id (needs checkpointer.type: sqlite).",
    ),
):
    """
    Execute an autonomous research task with the swarm.

    Pass a natural-language prompt describing what you want the agents to do.
    The swarm will search, reason, validate, and write outputs to the Drafts/ folder.

    Example:
        python -m automation.main execute "Review the literature on [your research question]."
        python -m automation.main execute --resume 3f1c…   # continue a crashed or paused run
    """
    try:
        config = load_config(config_path) if config_path else load_config()
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _run_graph(config, _prompt_or_resume(prompt, resume), resume_thread=resume)


def _prompt_or_resume(prompt: Optional[str], resume: Optional[str]) -> str:
    """A new run needs a prompt; a resumed run reads its task from the checkpoint."""
    if resume:
        if prompt:
            typer.secho("Note: the prompt is ignored with --resume; the saved task is used.",
                        fg=typer.colors.YELLOW)
        return ""
    if not prompt:
        typer.secho("Give a research prompt, or --resume <thread_id>.", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    return prompt


@app.command()
def report(
    prompt: Optional[str] = typer.Argument(None, help="Research prompt (omit with --resume)."),
    config_path: Path = typer.Option(None, "--config", help="Swarm configuration YAML"),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Continue a saved run by its thread id (needs checkpointer.type: sqlite).",
    ),
    mode: str = typer.Option(
        "narrative-review",
        "--mode", "-m",
        help=(
            "Output format: scoping-review | narrative-review | evidence-brief | "
            "pediatric-screen | young-adult-screen | general-adult-screen | "
            "grant-support | manuscript-draft | journalistic-brief"
        ),
    ),
):
    """
    Execute a structured research task with an explicit report format.

    Prepends a mode-specific template instruction to the prompt so the Journalist
    agent produces output in the correct format for the chosen report type.

    Academic modes:
      scoping-review        — JBI scoping review (coverage map, no recommendations)
      narrative-review      — Full academic narrative review with abstract and sections
      evidence-brief        — ~800-word plain-language brief for clinicians / policymakers
      manuscript-draft      — Full peer-reviewable manuscript draft with all sections

    Population-specific screening modes:
      pediatric-screen      — Review focused on the 0–18 age group
      young-adult-screen    — Review focused on university and college students (18–25)
      general-adult-screen  — Review focused on adults 18+, adult-normed instruments

    Specialist prompt packs:
      grant-support         — Significance/innovation synthesis for grant background
      journalistic-brief    — ~600-word plain-language press/communications brief

    Example:
        python -m automation.main report "anxiety in adolescents" --mode pediatric-screen
        python -m automation.main report "CBT for depression" --mode grant-support
    """
    try:
        config = load_config(config_path) if config_path else load_config()
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if mode not in REPORT_MODES:
        typer.secho(
            f"Unknown mode '{mode}'. Choose from: {', '.join(REPORT_MODES)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    prompt = _prompt_or_resume(prompt, resume)
    if resume:
        _run_graph(config, prompt, resume_thread=resume)
        return
    typer.secho(f"📋 Report mode: {mode}", fg=typer.colors.CYAN)
    _run_graph(config, REPORT_MODES[mode] + prompt)


@app.command(hidden=True)
def scaffold(domain: str):
    """
    Deprecated alias for `swarm init --no-interactive`.

    Example:
        python -m automation.main scaffold "Climate Science"
    """
    root_dir = Path.cwd()
    config_path = root_dir / "swarm_config.yml"
    if config_path.exists():
        typer.secho(
            "'swarm scaffold' is deprecated and now routes through the builder-backed init flow. "
            "Use 'swarm init' for new work.",
            fg=typer.colors.YELLOW,
        )
        typer.secho(
            "swarm_config.yml already exists in the current directory. Use 'swarm preview' or the configure commands to evolve it.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    typer.secho(
        "'swarm scaffold' is deprecated; generating a non-interactive research-core swarm via the builder.",
        fg=typer.colors.YELLOW,
    )
    spec = build_starter_swarm_spec(
        domain=domain,
        swarm_name=f"{domain} Swarm",
        swarm_description=f"Autonomous multi-agent research swarm for {domain}",
    )
    preview_swarm_spec(spec)
    _write_swarm_from_spec(spec, root_dir, force=False, yes=True, confirm_text="")


@app.command()
def info():
    """
    Display the current swarm configuration summary.
    """
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    swarm = config["swarm"]
    model = config["model"]

    typer.secho(f"\n{'═' * 55}", fg=typer.colors.CYAN)
    typer.secho(f"  {swarm['name']}", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"  {swarm.get('description', '')}", fg=typer.colors.WHITE)
    typer.secho(f"{'═' * 55}", fg=typer.colors.CYAN)

    typer.secho(f"\n📡 Model: {model['provider']}/{model['name']} (temp={model.get('temperature', 0.2)})")

    typer.secho("\n👥 Personas:")
    for p in config["personas"]:
        tools_str = ", ".join(p.get("tools", []))
        typer.secho(f"   {p.get('icon', '🤖')} {p['name']} — {p['role']} [{tools_str}]")

    typer.secho(f"\n🔧 Tools registered: {len(config['tools'])}")
    for name, spec in config["tools"].items():
        typer.secho(f"   • {name}: {spec.get('description', spec['function'])}")

    orch = config.get("orchestrator", {})
    typer.secho(
        f"\n🎯 Orchestrator: {orch.get('agent', 'Dr. Nexus')} "
        f"(journalist: {orch.get('journalist', 'Journalist')}, "
        f"max_agent_calls: {orch.get('max_agent_calls', 8)}, "
        f"max_tool_rounds: {orch.get('max_tool_rounds_per_agent', 5)})"
    )

    typer.secho(f"\n📋 Report modes: {', '.join(REPORT_MODES)}")

    hitl_cfg = get_hitl_config(config)
    hitl_status = "✅ Enabled" if hitl_cfg["enabled"] else "❌ Disabled"
    typer.secho(f"\n⏸  HITL: {hitl_status}")
    if hitl_cfg["enabled"]:
        typer.secho(f"   Checkpoints: {', '.join(hitl_cfg['checkpoints'])}")

    reviewer = config["reviewer"]
    r_status = "✅ Enabled" if reviewer.get("enabled", True) else "❌ Disabled"
    typer.secho(f"\n🔍 Reviewer-2: {r_status} (max {reviewer.get('max_revision_loops', 3)} loops)")
    typer.secho(f"   Banned words: {', '.join(reviewer.get('banned_words', []))}")
    typer.secho(f"   Tone: {reviewer.get('tone', 'neutral')}")

    typer.echo()


@export_app.command("mapppp-hcm")
def export_mapppp_hcm(
    draft_path: Path = typer.Option(..., "--draft-path"),
    traceability_matrix: Path = typer.Option(..., "--traceability-matrix"),
    config_path: Path = typer.Option(Path("examples/hcm/swarm_config.yml"), "--config-path"),
    output: Path = typer.Option(Path("Drafts/HCMSAS/mapppp_bundle.json"), "--output"),
    review_notes_path: Path | None = typer.Option(None, "--review-notes-path"),
    registry_path: Path | None = typer.Option(None, "--registry-path"),
    suggest_hcmo: bool = typer.Option(False, "--suggest-hcmo", help="Suggest terms from pinned HCMO 0.4.1"),
):
    """Package HCM draft evidence as proposal-only MAPPPP 0.1.0."""
    try:
        bundle = export_hcm_mapppp_bundle(
            config_path=config_path, draft_path=draft_path,
            traceability_matrix_path=traceability_matrix,
            review_notes_path=review_notes_path, registry_path=registry_path,
            suggest_hcmo=suggest_hcmo,
            coverage_path=(output.with_suffix(".hcmo-coverage.json") if suggest_hcmo else None),
        )
    except (FileNotFoundError, ValueError) as error:
        typer.secho(f"EXPORT ERROR: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(bundle.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")
    typer.echo(str(output))


@pilot_app.command("hcm")
def pilot_hcm(
    spec_path: Path = typer.Option(Path("tests/fixtures/hcm_circadian_mouse_demo.yaml"), "--spec-path"),
    output_root: Path | None = typer.Option(None, "--output-root"),
    refresh_cache: bool = typer.Option(False, "--refresh-cache"),
    refresh_intermediates: bool = typer.Option(False, "--refresh-intermediates"),
    suggest_hcmo: bool = typer.Option(False, "--suggest-hcmo", help="Suggest terms from pinned HCMO 0.4.1"),
):
    """Run the fixed PMID HCM pilot with resumable intermediates."""
    try:
        outputs = run_hcm_pilot(spec_path, output_root,
                                refresh_cache=refresh_cache,
                                refresh_intermediates=refresh_intermediates,
                                suggest_hcmo=suggest_hcmo)
    except (FileNotFoundError, ValueError) as error:
        typer.secho(f"PILOT ERROR: {error}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from error
    typer.echo(str(outputs["bundle"]))


if __name__ == "__main__":
    app()
