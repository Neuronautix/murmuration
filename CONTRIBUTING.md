# Contributing to Research Swarm

Thank you for your interest in contributing. Contributions from researchers, data scientists, and software engineers are all welcome.

---

## Ways to Contribute

### 1. Report a Bug

Open a [GitHub issue](https://github.com/dhuzard/piu-psych-swarm/issues) with the **Bug Report** template. Include:

- What you were trying to do
- What happened instead
- The exact error message (copy-paste from terminal)
- Your OS, Python version, and provider (OpenAI / Anthropic / Google)

### 2. Share a Domain Example

If you have configured the swarm for a new research domain and it works well, share it as an example:

1. Create a folder under `examples/your_domain_name/`
2. Include: `swarm_config.yml`, `agents/*/persona.md`, `README.md`
3. Optionally include sample KB files (anonymized if needed)
4. Open a pull request with a short description of the domain and what the team does

See `examples/piu_psychiatry/` for the reference format.

### 3. Suggest a Feature or Improvement

Open a [GitHub issue](https://github.com/dhuzard/piu-psych-swarm/issues) with the **Feature Request** template. Describe:

- The research workflow or use case you are trying to support
- Why the current behavior does not cover it
- What the ideal behavior would be

### 4. Fix a Bug or Implement a Feature

1. Fork the repository.
2. Create a branch: `git checkout -b fix/description` or `git checkout -b feature/description`.
3. Make your changes.
4. Run validation: `python -m automation.main doctor`.
5. Run the linter: `ruff check automation/` (install with `pip install ruff`).
6. Open a pull request against `main`.

---

## Development Setup

```bash
git clone https://github.com/dhuzard/piu-psych-swarm.git
cd piu-psych-swarm
make setup          # creates .venv and installs all dev dependencies
cp .env.example .env
# Add your API key to .env
make ingest
make doctor         # confirm environment is healthy
```

---

## Code Conventions

- **The Python code must remain domain-agnostic.** All domain-specific behavior belongs in `swarm_config.yml` and `agents/*/persona.md`, not in `automation/`.
- **Tool functions must be synchronous** (`def`, not `async def`). Concurrency is handled by the graph.
- **New tool functions** must be registered in `swarm_config.yml` and documented in `README.md`.
- **Python formatting:** `ruff` with `line-length = 120`. Run `ruff check automation/` before submitting.
- **YAML files:** 2-space indentation, no tabs.

See [CLAUDE.md](CLAUDE.md) for detailed architectural conventions.

---

## Pull Request Checklist

- [ ] Changes run without errors on a local `make run PROMPT="test"`
- [ ] `python -m automation.main doctor` passes
- [ ] No domain-specific code added to `automation/`
- [ ] New tools are registered in `swarm_config.yml` and documented in `README.md`
- [ ] New example domains include a `README.md` explaining the team design
- [ ] No `.env` files or real API keys included

---

## Questions?

Open a [discussion](https://github.com/dhuzard/piu-psych-swarm/discussions) or file an issue with the **Question** label.
