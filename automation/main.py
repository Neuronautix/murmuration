"""
main.py — CLI Entry Point for the Research Swarm

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
    load_config,
    validate_env,
    get_hitl_config,
)
from automation.graph import build_graph, Command  # Command re-exported from graph.py

# Load environment configuration (.env)
load_dotenv()

console = Console()

app = typer.Typer(
    name="swarm",
    help="Research Swarm CLI — A configurable multi-agent research system built on LangGraph.",
    add_completion=False,
)
blueprint_app = typer.Typer(help="Export or import portable blueprint files for reuse across repos.")
persona_app = typer.Typer(help="Add or edit personas in the current swarm.")
team_app = typer.Typer(help="Configure orchestrator, routing, and HITL settings.")
review_app = typer.Typer(help="Configure reviewer policy and adversarial checks.")
model_app = typer.Typer(help="Configure model provider, model name, and env key.")
metadata_app = typer.Typer(help="Configure swarm metadata, output paths, and epistemic tags.")
tools_app = typer.Typer(help="Curate the active tool registry for the current swarm.")
app.add_typer(blueprint_app, name="blueprint")
app.add_typer(persona_app, name="persona")
app.add_typer(team_app, name="team")
app.add_typer(review_app, name="review")
app.add_typer(model_app, name="model")
app.add_typer(metadata_app, name="metadata")
app.add_typer(tools_app, name="tools")

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

def _ensure_matrix_header(config: dict, task: str) -> None:
    """Create the traceability matrix with its table header if it does not exist,
    then append a run-separator line so each run's entries are clearly delimited."""
    matrix_path = Path(
        config["swarm"].get("traceability_matrix", "./Knowledge_Traceability_Matrix.md")
    )
    if not matrix_path.exists():
        matrix_path.write_text(MATRIX_HEADER, encoding="utf-8")

    run_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    task_preview = task[:80] + "…" if len(task) > 80 else task
    with open(matrix_path, "a", encoding="utf-8") as f:
        f.write(f"\n### Run: {run_stamp} | Task: {task_preview}\n\n")


def _write_run_metrics(config: dict, task: str, token_usage: dict) -> None:
    """Write per-run token/cost metrics to Drafts/run_metrics.json."""
    drafts_dir = Path(config["swarm"].get("output_dir", "./Drafts"))
    drafts_dir.mkdir(parents=True, exist_ok=True)

    input_price_per_m = 2.50   # gpt-4o list pricing $/M tokens (2025)
    output_price_per_m = 10.00
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
        "cost_note": "Estimate uses gpt-4o list pricing ($2.50/M input, $10/M output). "
                     "Adjust for your model and negotiated rates.",
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


async def _run_graph_async(config: dict, prompt: str, graph, run_config: dict) -> dict:
    """Async inner loop: streams the compiled graph and handles HITL interrupts.

    Returns accumulated token_usage dict.
    Specialist and Journalist nodes are async (using _run_tool_loop_async) so
    multiple tool calls within a single agent turn run concurrently.
    """
    final_token_usage: dict = {}
    current_input: dict | Command = {
        "task": prompt,
        "messages": [],
        "agent_outputs": {},
        "agent_assignments": {},
        "next_agents": [],
        "next_instructions": "",
        "agent_call_count": 0,
        "reviewer_approved": False,
        "revision_count": 0,
        "token_usage": {},
    }

    typer.secho("\n🧠 [SWARM ACTIVE] Streaming agent interactions...\n", fg=typer.colors.BLUE)

    while True:
        interrupted = False

        async for event in graph.astream(
            current_input,
            config=run_config,
            stream_mode="updates",
        ):
            # Accumulate token_usage from any node update that carries it
            for node_name, update in event.items():
                if isinstance(update, dict) and "token_usage" in update:
                    for k, v in update["token_usage"].items():
                        if isinstance(v, (int, float)):
                            final_token_usage[k] = final_token_usage.get(k, 0) + v

            # Check for interrupt before displaying messages
            if "__interrupt__" in event:
                interrupt_objs = event["__interrupt__"]
                answer = _handle_hitl_interrupt(interrupt_objs)
                current_input = Command(resume=answer)
                interrupted = True
                break

            _display_event_messages(event)

        if not interrupted:
            break  # Graph completed naturally

    return final_token_usage


def _run_graph(config: dict, prompt: str) -> None:
    """Core graph execution shared by `execute` and `report` commands.

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

    _ensure_matrix_header(config, prompt)

    hitl_cfg = get_hitl_config(config)
    hitl_enabled = hitl_cfg.get("enabled", False)

    # ── Pre-flight HITL: simple CLI confirm, no graph interrupt needed ───
    if hitl_enabled and "pre_flight" in hitl_cfg.get("checkpoints", []):
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

    graph = build_graph(config)

    # Thread config: required for MemorySaver interrupt/resume; empty dict otherwise.
    run_config = {"configurable": {"thread_id": str(uuid.uuid4())}} if hitl_enabled else {}

    try:
        final_token_usage = asyncio.run(
            _run_graph_async(config, prompt, graph, run_config)
        )
    except Exception as e:
        typer.secho(f"\nExecution Error: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("\n✅ Task complete.", fg=typer.colors.GREEN)
    output_dir = config["swarm"].get("output_dir", "./Drafts")
    typer.secho(f"📂 Outputs saved to '{output_dir}/'", fg=typer.colors.CYAN)

    _write_run_metrics(config, prompt, final_token_usage)


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
def execute(prompt: str):
    """
    Execute an autonomous research task with the swarm.

    Pass a natural-language prompt describing what you want the agents to do.
    The swarm will search, reason, validate, and write outputs to the Drafts/ folder.

    Example:
        python -m automation.main execute "Review the literature on [your research question]."
    """
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    _run_graph(config, prompt)


@app.command()
def report(
    prompt: str,
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
        config = load_config()
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"CONFIG ERROR: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    if mode not in REPORT_MODES:
        typer.secho(
            f"Unknown mode '{mode}'. Choose from: {', '.join(REPORT_MODES)}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

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

    typer.secho(f"\n👥 Personas:")
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


if __name__ == "__main__":
    app()
