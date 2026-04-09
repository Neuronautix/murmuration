# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | ✅         |
| < 0.2   | ❌         |

## Scope

Research Swarm is a local orchestration framework. It does not run a server,
expose network ports, or store credentials. The primary security surface is:

- **API keys** — loaded from `.env` via `python-dotenv`. Never committed.
- **`scrape_webpage` tool** — fetches arbitrary URLs passed from the LLM. Do not
  route untrusted user input directly to this tool without sanitisation.
- **`git_commit_snapshot` tool** — stages only `Drafts/` and the traceability
  matrix; it does not run `git add -A`.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Use one of the following:

1. **GitHub private vulnerability reporting** — open the *Security* tab of this
   repository and click *Report a vulnerability*.
2. **Email** — contact the maintainer directly via the email linked to the
   repository's GitHub account.

We aim to acknowledge reports within **5 business days** and to provide a
resolution or mitigation plan within **30 days**.

## Out of Scope

- Vulnerabilities in third-party packages (LangGraph, LangChain, ChromaDB,
  OpenAI SDK) — please report those upstream.
- Issues requiring physical access to the machine running the swarm.
- Denial-of-service via crafted research prompts (LLM token exhaustion is an
  inherent property of LLM systems, not a bug in this framework).
