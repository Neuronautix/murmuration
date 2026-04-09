# Financial Modeling Example

A ready-to-run Research Swarm configuration for financial research and risk
analysis. Designed for conservative, evidence-based investment analysis with
built-in compliance guardrails.

## Team

| Agent | Role |
|-------|------|
| 📈 Chief Analyst | Orchestrator — strategy, task routing, synthesis |
| 🧮 Quant Researcher | Quantitative modelling, backtesting, factor analysis |
| 🛡️ Compliance Officer | Regulatory compliance, risk flagging |
| ✍️ Report Writer | Journalist — assembles the final report |

## Quick Start

```bash
# From the repo root:
cp examples/financial_modeling/swarm_config.yml swarm_config.yml
cp -r examples/financial_modeling/agents ./agents

# Install and run
make setup
make ingest   # optional: load KB documents first
make run PROMPT="Analyse the risk-adjusted return characteristics of
  momentum factor strategies in European equities since 2020."
```

## Customising

1. **Persona prompts** — edit `agents/*/persona.md` to target a specific
   asset class, strategy type, or investment mandate.
2. **Compliance rules** — extend `reviewer.banned_words` with language that
   violates your regulatory obligations (e.g. MiFID II, Reg BI).
3. **Knowledge Base** — add research reports, 10-K filings, or factor model
   papers to `agents/*/KB/` and run `make ingest`.
4. **Epistemic tags** — defaults are `[MARKET_DATA]`, `[ESTIMATE]`,
   `[PROJECTION]`, `[REGULATION]`.

## Example Prompts

- *"Compare the historical Sharpe ratios of value vs quality factor strategies
  across market cycles from 2000 to 2024."*
- *"What does the academic literature say about the decay of momentum returns
  after transaction costs?"*
- *"Summarise SEC enforcement actions related to investment adviser conflicts
  of interest in the past two years."*

> **Disclaimer**: This swarm is a research tool. Nothing it produces constitutes
> investment advice. Always consult a qualified financial professional.
