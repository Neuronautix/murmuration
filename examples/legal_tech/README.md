# Legal Tech Example

A ready-to-run Research Swarm configuration for legal research and brief
assembly. Produces structured legal memoranda with Bluebook citations,
adversarial review, and a mandatory Table of Authorities.

## Team

| Agent | Role |
|-------|------|
| ⚖️ Lead Counsel | Orchestrator — issue spotting, task routing, strategy |
| 📋 Paralegal | Case law and statutory research |
| 🛡️ Compliance | Regulatory compliance gap analysis |
| ✍️ Clerk | Journalist — assembles the final brief or memo |

## Quick Start

```bash
# From the repo root:
cp examples/legal_tech/swarm_config.yml swarm_config.yml
cp -r examples/legal_tech/agents ./agents

# Install and run
make setup
make ingest   # optional: load KB documents first
make run PROMPT="Memorandum: Does a SaaS vendor's standard limitation-of-liability
  clause bar recovery for gross negligence under New York law?"
```

## Customising

1. **Persona prompts** — edit `agents/*/persona.md` to target a specific
   practice area (M&A, employment, IP, criminal) or jurisdiction.
2. **Citation format** — the default is Bluebook; update `agents/Clerk/persona.md`
   to use OSCOLA, APA, or house style.
3. **Reviewer constraints** — add red-flag phrases to `reviewer.banned_words`
   (e.g. *"guaranteed outcome"*, *"obviously"*).
4. **Knowledge Base** — load case law PDFs, statutory compilations, or
   practice guides into `agents/*/KB/` and run `make ingest`.

## Example Prompts

- *"Analyse the enforceability of non-compete clauses under current California
  and FTC rule proposals."*
- *"What is the standard for piercing the corporate veil in Delaware, and how
  have courts applied it in the past five years?"*
- *"Research GDPR Article 17 right-to-erasure obligations for a SaaS company
  processing EU personal data."*

> **Disclaimer**: This swarm is a research tool. Output does not constitute
> legal advice. Always consult a qualified attorney.
