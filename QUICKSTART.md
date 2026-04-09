# Quickstart: Run Research Swarm

This guide takes you from zero to your first AI-generated research document.

**Not a programmer?** Follow these steps exactly as written. Each command can be copy-pasted into your terminal. If you get stuck, see [FAQ.md](FAQ.md) or open a [GitHub issue](https://github.com/dhuzard/piu-psych-swarm/issues).

---

## Before You Start

You need three things:

### 1. An AI API Key (required)

The swarm uses an AI model to think and write. The default is OpenAI (GPT-4o).

**How to get an OpenAI API key:**
1. Go to [platform.openai.com](https://platform.openai.com) and create an account
2. Go to **Billing** → add a payment method
3. Set a **spending limit** (recommended: $20/month for testing) at [platform.openai.com/settings/organization/limits](https://platform.openai.com/settings/organization/limits)
4. Go to **API keys** → **Create new secret key** → copy it (starts with `sk-`)

**Cost estimate:** A typical research question costs **$1–$5**. A complex multi-topic synthesis costs $5–$15.

Prefer Anthropic or Google instead? See [FAQ.md](FAQ.md).

### 2. A Terminal (required)

- **Mac:** Open the "Terminal" app (search with Cmd+Space)
- **Windows:** Open "PowerShell" (right-click Start menu → "Windows PowerShell")
- **Linux:** Any terminal emulator

### 3. Python 3.10 or later (required)

Check in your terminal:
```bash
python --version
# or
python3 --version
```

If you see `Python 3.10.x` or higher, you're good. If Python is missing or older:
- **Mac:** Install from [python.org/downloads](https://www.python.org/downloads/)
- **Linux/WSL:** `sudo apt update && sudo apt install -y python3 python3-venv make`
- **Windows:** Install from [python.org/downloads](https://www.python.org/downloads/) — check "Add to PATH" during install

---

## Step 1: Download the Repository

### Linux, Mac, or Windows WSL

```bash
git clone https://github.com/dhuzard/piu-psych-swarm.git
cd piu-psych-swarm
make setup
```

If `make` is not found on Linux/WSL:
```bash
sudo apt update && sudo apt install -y make
```

### Windows (PowerShell — no WSL needed)

```powershell
git clone https://github.com/dhuzard/piu-psych-swarm.git
cd piu-psych-swarm
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

> **Don't have git?** Download the ZIP from the GitHub page (green "Code" button → "Download ZIP"), unzip it, and open a terminal in that folder.

---

## Step 2: Add Your API Key

Open the `.env` file in a text editor (Notepad, TextEdit, VS Code — anything works).

Replace `sk-your-openai-key-here` with your actual key:

```
OPENAI_API_KEY=sk-your-actual-key-goes-here
```

Save the file. That's it — the swarm loads it automatically.

---

## Step 3: Choose Your Team

The repository contains a blank template with placeholder agents. You have three options:

---

### Option A: Use the ready-made psychiatry example (fastest)

This deploys a complete 6-specialist team for psychiatric and behavioral medicine research — no configuration needed.

```bash
cp examples/piu_psychiatry/swarm_config.yml .
cp -r examples/piu_psychiatry/agents .
```

Skip to Step 4.

---

### Option B: Use the interactive builder (recommended for new domains)

```bash
python -m automation.main init
```

The wizard asks you about your domain, team roles, and reviewer standards. It generates all the configuration files for you. Takes about 5–10 minutes.

---

### Option C: Edit the template manually

Open `swarm_config.yml` in a text editor. Follow the comments marked with arrows to customize:

- Change `name: "My-Research-Swarm"` to your project name
- Change `description: "Autonomous research swarm for [your domain]"` — fill in your domain
- Rename `Specialist1` and `Specialist2` to match your domain experts (e.g., `EpiSpecialist`, `ClinicalLead`)
- Rename the matching folders in `agents/` to match (e.g., rename `agents/Specialist1/` to `agents/EpiSpecialist/`)

Then open `agents/Orchestrator/persona.md` and each specialist's `persona.md` and fill in the domain-specific instructions.

See [TIPS.md](TIPS.md) for guidance on writing effective persona instructions.

---

## Step 4: Add Knowledge Base Documents (optional but recommended)

Drop PDF, Markdown, or text files into the KB folder of the relevant agent:

```
agents/Specialist1/KB/    ← papers for your first specialist
agents/Specialist2/KB/    ← papers for your second specialist
agents/LitScout/KB/       ← landmark reviews, reading lists
agents/Orchestrator/KB/   ← scope notes, shared bibliographies
```

Then build the knowledge base index:

### Linux, Mac, or WSL
```bash
make ingest
```

### Windows (PowerShell)
```powershell
.\.venv\Scripts\python -m automation.ingest
```

If you skip this step, the swarm still works — it will use live search tools instead of your local documents.

---

## Step 5: Verify and Run

Check your configuration is correct:

### Linux, Mac, or WSL
```bash
make info
```

### Windows (PowerShell)
```powershell
.\.venv\Scripts\python -m automation.main info
```

You should see your team listed with their tools. If you see errors, run `make doctor` (or the PowerShell equivalent) to get a diagnosis.

**Run your first research question:**

### Linux, Mac, or WSL
```bash
make run PROMPT="Review the prevalence and risk factors of burnout in emergency physicians."
```

### Windows (PowerShell)
```powershell
.\.venv\Scripts\python -m automation.main execute "Review the prevalence and risk factors of burnout in emergency physicians."
```

The swarm will run for 3–8 minutes depending on the question. Outputs appear in the `Drafts/` folder.

---

## Step 6: Get Structured Output Formats

Use `report` instead of `execute` to request a specific document format:

```bash
# Evidence brief (~800 words, plain-language)
python -m automation.main report "Your question" --mode evidence-brief

# Scoping review outline (JBI methodology)
python -m automation.main report "Your question" --mode scoping-review

# Grant background section
python -m automation.main report "Your question" --mode grant-support

# Full manuscript draft
python -m automation.main report "Your question" --mode manuscript-draft

# Journalistic brief (~600 words, plain language)
python -m automation.main report "Your question" --mode journalistic-brief
```

Available modes: `scoping-review`, `narrative-review`, `evidence-brief`, `manuscript-draft`, `grant-support`, `journalistic-brief`, `pediatric-screen`, `young-adult-screen`, `general-adult-screen`

---

## What Gets Produced

After a run, check these locations:

- **`Drafts/`** — your generated documents (Markdown files, one per section or complete draft)
- **`Knowledge_Traceability_Matrix.md`** — a table of every claim the agents made, its source, and epistemic status
- **`Drafts/run_metrics.json`** — token usage and estimated cost for this run

---

## Troubleshooting

| Problem | Solution |
| :--- | :--- |
| `CONFIG ERROR` | Ensure `swarm_config.yml` exists and is valid YAML. Run `python -m automation.main doctor`. |
| `ENV ERROR: missing OPENAI_API_KEY` | Open `.env` and add your API key |
| `No Knowledge Base found` | Run `make ingest` (or the PowerShell equivalent) |
| `Tool import failures` | Run `make setup` again |
| Persona name mismatch | Ensure agent names in `swarm_config.yml` exactly match folder names in `agents/` |
| Empty `Drafts/` folder | Run `make info` to verify tool wiring; check the terminal for error messages |
| `make: command not found` (Windows) | Use the PowerShell commands instead — see each step above |
| API rate limit error | Wait 60 seconds and retry; upgrade your OpenAI tier if it recurs |

Run `python -m automation.main doctor` at any time for a full configuration health check.

For more help: see [FAQ.md](FAQ.md) or open a [GitHub issue](https://github.com/dhuzard/piu-psych-swarm/issues).
