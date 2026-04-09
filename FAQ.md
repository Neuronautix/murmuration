# Frequently Asked Questions

## For Researchers New to This Tool

---

### What does Research Swarm actually produce?

It produces **formatted Markdown documents** — literature reviews, evidence briefs, scoping review outlines, grant background sections, and manuscript drafts. Each output includes in-text citations (e.g., [1], [2]) and a full References section at the bottom. Outputs are saved to the `Drafts/` folder.

A typical run on a medium-complexity research question produces a 1,000–3,000 word structured document in 3–8 minutes.

---

### Do I need to know how to code?

No. You need to:
1. Edit one YAML configuration file (`swarm_config.yml`) — this is like filling out a form with your domain details.
2. Add text documents to a folder (`agents/*/KB/`).
3. Type commands like `make run PROMPT="Your question"` into a terminal.

If you have never opened a terminal before, it takes about 15–30 minutes to get comfortable. See [QUICKSTART.md](QUICKSTART.md) for step-by-step instructions.

---

### How much does it cost?

It depends on which AI model you use and how complex your question is. Rough estimates using **GPT-4o** (the default):

| Task | Approximate cost |
| :--- | :--- |
| Simple factual query | $0.50–$1.50 |
| Evidence brief (800 words) | $1–$4 |
| Narrative review (2,000 words) | $3–$10 |
| Full manuscript draft section | $5–$15 |

These are estimates based on GPT-4o list pricing as of 2025 ($2.50/M input tokens, $10/M output tokens). Costs vary based on the complexity of the question and how many agents are engaged.

**Important:** OpenAI requires a payment method on file before issuing an API key. Set a **monthly spending limit** in your [OpenAI account settings](https://platform.openai.com/settings/organization/limits) to avoid unexpected charges.

---

### How do I get an API key?

**OpenAI (default):**
1. Go to [platform.openai.com](https://platform.openai.com) and create an account.
2. Add a payment method under **Billing**.
3. Go to **API Keys** → **Create new secret key**.
4. Copy the key (it starts with `sk-`) — you will only see it once.
5. Paste it into your `.env` file: `OPENAI_API_KEY=sk-your-key-here`

**Anthropic (alternative):**
1. Go to [console.anthropic.com](https://console.anthropic.com) and create an account.
2. Go to **API Keys** → **Create Key**.
3. In `swarm_config.yml`, change `provider: "openai"` to `provider: "anthropic"` and `name: "gpt-4o"` to `name: "claude-opus-4-6"`.
4. Add to `.env`: `ANTHROPIC_API_KEY=your-key-here`

**You.com (optional — enables live web search):**
1. Go to [you.com/api](https://you.com/api) and request a key.
2. Add to `.env`: `YOU_API_KEY=your-key-here`
3. Without this key, the swarm will still run — it just uses PubMed and Semantic Scholar only.

---

### Is the output publication-ready?

Not directly. The swarm produces a well-structured **draft** with literature-backed claims and proper citations. You should:
- Verify individual citations (the agents do not guarantee citation accuracy)
- Review the arguments and add your own expert interpretation
- Adapt the language to your target journal's style
- Add any domain knowledge or unpublished data the agents cannot access

Think of it as a very thorough research assistant that produces a strong first draft, not a finished manuscript.

---

### Can I trust the citations?

The swarm uses real APIs (PubMed, Semantic Scholar, Crossref) to retrieve papers. Citations come from actual indexed literature. However:
- Always verify DOIs and abstracts before including citations in your work
- The agents may occasionally misattribute a finding to the wrong paper
- Very recent papers (last 3–6 months) may not yet be indexed on PubMed
- The `Knowledge_Traceability_Matrix.md` file logs every fact and its source so you can audit the evidence trail

---

### Can I use this for clinical decision-making?

No. Research Swarm produces literature synthesis for research and educational purposes only. It is not a clinical decision support tool. All outputs should be reviewed by qualified clinicians before any clinical application.

---

### What research domains does it work for?

Any domain with peer-reviewed literature indexed on PubMed or Semantic Scholar. It has been tested for:

- Psychiatric and behavioral medicine research
- Epidemiology and public health
- Clinical trial synthesis
- Policy and intervention research
- Neuroscience and cognitive science

See `examples/` for ready-made team configurations for specific domains.

---

### Can I run it without the You.com key?

Yes. The `YOU_API_KEY` is optional. Without it:
- `search_web` (live web search) will return an error or empty results
- `you_research` (multi-step web synthesis) will not function
- All PubMed, Semantic Scholar, and DOI-based tools still work normally

For literature-only research, You.com is not required.

---

### The swarm produced empty output — what happened?

Common causes:
1. **No API key set** — check your `.env` file has `OPENAI_API_KEY=sk-your-key`
2. **KB not ingested** — run `make ingest` before `make run`
3. **Persona file missing** — run `make doctor` to check for config errors
4. **Token limit hit** — try a more focused prompt or reduce `max_agent_calls` in `swarm_config.yml`
5. **Rate limit** — OpenAI rate limits new accounts; wait a minute and retry

Run `python -m automation.main doctor` — it will diagnose most setup problems and tell you what to fix.

---

### How do I add my own papers to the agents' knowledge?

1. Copy your paper (PDF, Markdown, or plain text) into the relevant agent's KB folder. Example: `agents/Specialist1/KB/my_paper.pdf`.
2. Run `make ingest` to vectorize the new document.
3. Run `make info` to confirm the document appears in the agent's KB.

See [agents/how_to_enrich.md](agents/how_to_enrich.md) for a guide on which documents go where.

---

### Can I use this on Windows without WSL?

Yes. Use PowerShell. See [QUICKSTART.md](QUICKSTART.md) for Windows PowerShell instructions that do not require WSL or `make`.

---

### How do I save my team configuration to reuse in another project?

```bash
python -m automation.main blueprint export --name "MyDomainTeam"
```

This saves a portable blueprint file under `blueprints/`. You can copy it to another repository and import it:

```bash
python -m automation.main blueprint import ./blueprints/MyDomainTeam.swarm-blueprint.yml
```

---

### Can multiple people run the swarm on the same project?

Yes. The swarm outputs to `Drafts/` and `Knowledge_Traceability_Matrix.md`, which are tracked in git. Multiple team members can:
- Each run the swarm locally with their own API keys
- Commit their `Drafts/` outputs to the repo
- Review each other's runs via the traceability matrix

API keys stay local in `.env` (which is gitignored) and are never shared through the repository.

---

### Where can I get help?

- Open a [GitHub issue](https://github.com/dhuzard/piu-psych-swarm/issues) for bugs or questions
- See [TIPS.md](TIPS.md) for guidance on building effective swarms
- See [CONTRIBUTING.md](CONTRIBUTING.md) to share a domain example or contribute a fix
