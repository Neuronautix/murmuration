# How to Enrich Your Swarm

Teach your agents by adding documents to their Knowledge Base (KB) folders. Each agent reads only its own KB, so place each document with the specialist most likely to use it.

## Directory Structure

```
agents/
    ├── Orchestrator/       (Scope notes, shared bibliographies, construct disambiguation)
    │   └── KB/         <-- Drop project goals, scope definitions, or shared reference lists here
    ├── Specialist1/        (Your first domain specialist — rename to match its role)
    │   └── KB/         <-- Drop primary literature, review papers, or data notes here
    ├── Specialist2/        (Your second domain specialist — rename to match its role)
    │   └── KB/         <-- Drop mechanistic papers, theoretical frameworks, or methods notes here
    ├── LitScout/           (Citation mapping, landmark papers, author-trail notes)
    │   └── KB/         <-- Drop landmark reviews, meta-analyses, or reading lists here
    └── Journalist/         (Style guides, report templates, reference formatting)
            └── KB/         <-- Drop style guides or formatting templates here
```

## How to Add a Document

1. **Identify the right agent** — ask: which specialist needs this information to do their job?
2. **Drop the file** into the corresponding `KB/` folder. Supported formats: PDF, Markdown (`.md`), plain text (`.txt`).
3. **Re-ingest** to vectorize the new document:

   ```bash
   make ingest
   # or on Windows PowerShell:
   .\.venv\Scripts\python -m automation.ingest
   ```

4. **Confirm** the KB is updated by running `make info` and checking that the new document appears.

## Tips for High-Quality Knowledge Bases

- **Prefer structured documents** — review papers, consensus statements, and methods papers give agents more reliable reference material than informal notes.
- **Add context at the top** — begin each KB file with a one-paragraph summary of why it is relevant and what the agent should do with it (e.g., "Use this to verify prevalence figures when the main databases return conflicting estimates.").
- **Keep KB files focused** — one topic per file. A 5-page focused note is more useful than a 100-page omnibus document.
- **Name files descriptively** — `cbt_intervention_trials_2020_2024.md` is more useful than `notes.md`.
- **Version your KB** — if you update a KB file substantially, commit the change to git so the audit trail reflects which version the agent had access to.

## Agent-Specific Guidance

| Agent | Best document types |
| :--- | :--- |
| Orchestrator | Project scope notes, construct disambiguation, shared bibliographies |
| Specialist1 | Primary studies, review papers, instrument manuals |
| Specialist2 | Mechanism papers, theoretical frameworks, methods literature |
| LitScout | Landmark reviews, meta-analyses, citation maps |
| Journalist | Style guides, report templates, formatting conventions |

Rename the agent folders and update this table to match the actual role names in your `swarm_config.yml`.
