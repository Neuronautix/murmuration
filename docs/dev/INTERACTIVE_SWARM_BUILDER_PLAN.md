# Interactive Swarm Builder Plan

This document defines the next major module for the repository: a robust interactive tool that lets users create their own personas, configure orchestration behavior, and generate a valid swarm without manual YAML editing.

## Goal

Turn the current repo from a manually configured specialist swarm into a reusable swarm-construction framework.

The builder should let a user:

- create a new swarm identity
- add and edit personas interactively
- choose which tools each persona can access
- define orchestrator behavior and safety limits
- configure reviewer rules and HITL checkpoints
- preview the resulting team before files are written
- validate the generated setup before first run

## Recommendation

Recommended stack:

- `Typer` for top-level commands and argument handling
- `Rich` for tables, panels, previews, validation output, and diffs
- `Questionary` or `InquirerPy` for interactive wizard prompts
- `Pydantic` models for the internal swarm schema and validation layer

Why this stack:

- `Typer` already matches the current codebase and keeps the CLI consistent
- `Rich` produces a significantly better terminal UX than plain prompts
- `Questionary` or `InquirerPy` is enough for guided builders without introducing a full TUI runtime immediately
- `Pydantic` gives a strong contract between interactive inputs and file generation

Recommended non-goal for the first iteration:

- do not start with a full `Textual` TUI

Reason:

- the hard problem is not drawing screens, it is modeling a correct swarm, validating it, and writing safe files
- if the builder core is clean, a richer TUI can be added later without redoing the business logic

## Product Direction

The builder should replace the current thin `scaffold` flow with a proper guided workflow.

Current gap:

- `scaffold` creates generic folders and boilerplate persona files
- it does not guide tool assignment, orchestration choices, reviewer rules, or validation
- it does not help users understand the consequences of their team design

Target experience:

1. User runs `swarm init`
2. CLI asks whether to start from a blank swarm or a template
3. CLI asks for swarm name, domain, model provider, and output style
4. User adds personas one by one with role templates and tool scopes
5. CLI asks who is orchestrator, who is journalist, and which reviewer rules apply
6. CLI renders a preview of the final topology and config diff
7. User confirms
8. Files are written and validated
9. CLI shows exact next commands: ingest, info, execute

## Architecture

The builder should be split into four layers.

### 1. Domain Model Layer

Create a new module, for example:

- `automation/builder/models.py`

This layer should define typed objects such as:

- `SwarmSpec`
- `PersonaSpec`
- `ToolAssignment`
- `OrchestratorSpec`
- `ReviewerSpec`
- `HitlSpec`

Responsibilities:

- represent the full intended swarm in memory
- enforce validation rules before any file writes happen
- normalize defaults and field formats

Examples of validation rules:

- orchestrator must exist in personas
- journalist must exist in personas
- persona names must be unique
- configured tools must exist in the tool registry
- reviewer constraints must be structurally valid
- persona file paths and KB paths must not collide

### 2. Template and File Generation Layer

Create a new module, for example:

- `automation/builder/generator.py`

Responsibilities:

- render `swarm_config.yml`
- render persona markdown files from templates
- create KB directories
- preserve existing files safely unless the user confirms overwrite
- support dry-run preview mode

This layer should not ask questions. It should only accept a validated `SwarmSpec` and write files.

### 3. Interactive Wizard Layer

Create a new module, for example:

- `automation/builder/wizard.py`

Responsibilities:

- ask the user guided questions
- provide defaults and templates
- allow backtracking before writing files
- translate answers into a `SwarmSpec`

Wizard sections:

- swarm identity
- model selection
- persona creation
- tool assignment
- orchestrator configuration
- reviewer configuration
- HITL configuration
- preview and confirm

### 4. Validation and Repair Layer

Create a new module, for example:

- `automation/builder/doctor.py`

Responsibilities:

- validate an existing swarm on disk
- report missing persona files, invalid tools, and broken references
- suggest repairs
- optionally offer interactive fixes

This should power a future command like `swarm doctor`.

## CLI Shape

Recommended command set:

### `swarm init`

Create a new swarm interactively.

Should support:

- `--template`
- `--output-dir`
- `--non-interactive`

### `swarm persona add`

Add a new persona to an existing swarm.

Should support:

- choosing a role template
- assigning tools
- creating persona file and KB folder
- updating `swarm_config.yml`

### `swarm persona edit`

Update a persona interactively.

Should support:

- changing role text
- changing tool scopes
- changing icon
- updating behavior template

### `swarm team configure`

Update team-level behavior.

Should support:

- orchestrator selection
- journalist selection
- max agent calls
- max tool rounds
- HITL checkpoints

### `swarm review configure`

Update reviewer policies.

Should support:

- banned words
- required elements
- rejection patterns
- reviewer model overrides

### `swarm preview`

Render a readable summary before file writes.

Should show:

- team roster
- per-persona tool scopes
- orchestrator settings
- reviewer rules
- file changes to be written

### `swarm doctor`

Validate the repo state.

Should check:

- missing files
- invalid config references
- unknown tools
- empty persona definitions
- KB directory mismatches
- runtime prerequisites

## Persona Templates

The builder should ship with reusable persona archetypes.

Suggested built-ins:

- Orchestrator
- Domain Specialist
- Literature Scout
- Methods Reviewer
- Adversarial Critic
- Scribe
- Intervention Specialist

Each template should include:

- starter role text
- starter behavior rules
- recommended tools
- a KB guidance block

This reduces low-quality teams built from blank text boxes.

## Team Creation Rules

To make the multi-agent module solid, enforce design constraints.

Minimum team rules:

- exactly one orchestrator
- exactly one final writer or journalist
- at least one research specialist
- no persona with zero responsibilities and zero tools
- no unrestricted tool assignment by default

Recommended heuristics:

- warn if more than seven specialists are created without a clear routing rationale
- warn if all specialists have identical tools and roles
- warn if reviewer is enabled but required elements are empty
- warn if HITL is enabled with no checkpoints

## File Layout Proposal

Recommended new package structure:

```text
automation/
  builder/
    __init__.py
    doctor.py
    generator.py
    models.py
    templates.py
    wizard.py
```

Responsibilities:

- `models.py`: typed schema and validators
- `templates.py`: persona archetypes and config defaults
- `wizard.py`: interactive input flows
- `generator.py`: rendering and writes
- `doctor.py`: validation and repair checks

## Framework Decision

Best first implementation:

- continue with `Typer`
- add `Rich`
- add `Questionary` or `InquirerPy`

Best future upgrade path:

- add a `Textual` TUI later, backed by the same `SwarmSpec` builder core

That is the right tradeoff between UX, implementation risk, and architectural durability.

## Phased Build Plan

### Phase 1: Schema and Generator

Deliverables:

- `SwarmSpec` and related typed models
- generator that writes config and persona files
- tests for valid and invalid team definitions

Success criteria:

- can generate a valid swarm non-interactively from a structured spec

### Phase 2: Interactive Wizard

Deliverables:

- `swarm init`
- `swarm persona add`
- `swarm preview`

Success criteria:

- user can create a functioning swarm without touching YAML manually

### Phase 3: Validation and Repair

Deliverables:

- `swarm doctor`
- overwrite protection
- diff previews
- repair suggestions

Success criteria:

- broken or partial swarms can be diagnosed and corrected quickly

### Phase 4: Advanced UX

Deliverables:

- role templates library
- import/export presets
- optional `Textual` TUI

Success criteria:

- users can design high-quality teams quickly and safely

## Recommended Immediate Implementation Order

If you want the strongest next move, implement in this order:

1. Add `automation/builder/models.py` with a strict `SwarmSpec`
2. Add `automation/builder/generator.py` to render files from that spec
3. Replace `scaffold` with `swarm init` backed by the new builder core
4. Add `swarm preview` before any write operation
5. Add `swarm doctor` to validate generated or hand-edited swarms

This keeps the interactive layer thin and puts the hard guarantees in the right place.