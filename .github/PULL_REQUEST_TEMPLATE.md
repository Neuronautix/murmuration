## Summary

<!-- What does this PR do? Describe the change in 2–3 sentences. -->

## Type of change

- [ ] Bug fix — corrects broken behavior without changing the interface
- [ ] New tool — adds a `@tool`-decorated function to `automation/tools.py`
- [ ] New example domain — adds a complete configuration under `examples/`
- [ ] Framework change — modifies `automation/`, `swarm_config.yml` template, or `pyproject.toml`
- [ ] Documentation — updates README, QUICKSTART, TIPS, or other docs

## Checklist

### All PRs
- [ ] `python -m automation.main doctor` passes with no errors
- [ ] No `.env` files or real API keys included
- [ ] No hardcoded domain-specific content added to `automation/` (framework must stay domain-agnostic)

### Framework changes (`automation/`, `pyproject.toml`, `Makefile`)
- [ ] `ruff check automation/` passes with no errors
- [ ] New dependencies added to `pyproject.toml` (not to `automation/requirements.txt`)
- [ ] New tools are registered in `swarm_config.yml` and documented in `README.md`

### New example domain (`examples/<domain>/`)
- [ ] Includes `swarm_config.yml` (complete, not a placeholder)
- [ ] Includes `agents/` folder with at least `Orchestrator/persona.md` and one specialist
- [ ] Includes `agents/*/KB/` directories (may be empty with `.gitkeep`)
- [ ] Includes `README.md` following the pattern of `examples/piu_psychiatry/README.md`
- [ ] README includes at least three ready-to-run example prompts

### New tool
- [ ] Function is in `automation/tools.py` and decorated with `@tool`
- [ ] Function is synchronous (not `async def`)
- [ ] Function returns a `str`
- [ ] Registered in `swarm_config.yml` under `tools:` with `module`, `function`, `description`
- [ ] Documented in the `tools.py` module docstring
- [ ] `.env.example` updated if a new API key is required
