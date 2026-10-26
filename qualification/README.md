# Cross-repository qualification (manual, offline)

`qualification/` checks HCM MAPPPP bundles produced by this repository against
the real MAPPPP 0.1.0 validator and ingestion workflow of
[Neuronautix/OntoHistoCurator](https://github.com/Neuronautix/OntoHistoCurator),
pinned to one exact commit in
[`constraints/ontohistoria-qualification.txt`](../constraints/ontohistoria-qualification.txt).

## Why it is not run in CI

OntoHistoCurator is a private repository licensed AGPL-3.0. By ecosystem rule,
this public MIT repository holds no GitHub App, deploy key or secret for it.
CI therefore never checks it out. The `mapppp-qualification` CI job installs
this package without OntoHistoCurator and asserts that every qualification test
is **skipped with an `UNVERIFIED` reason**. It emits a warning annotation and a
step-summary line. A green CI run means "not verified in CI", not "qualified".

## Licence boundary

- The runtime (`automation/`) never imports `ontohistoria` or `mapppp`; only
  `qualification/test_cross_repository.py` does.
- No OntoHistoCurator code, schema or validator file is vendored here.
- OntoHistoCurator is installed only into the separate `.venv-qualification`
  environment (git-ignored), never into the runtime environment or `[project]`
  dependencies.

## Running it locally

You need your own read access to `Neuronautix/OntoHistoCurator` (your normal git
credentials; nothing is stored in this repository).

Windows PowerShell:

```powershell
./scripts/qualify-hcm.ps1            # or: ./scripts/qualify-hcm.ps1 -Python py
```

Linux/macOS:

```sh
./scripts/qualify-hcm.sh             # or: PYTHON=python3.11 ./scripts/qualify-hcm.sh
```

Both scripts create `.venv-qualification`, install OntoHistoCurator from the
pinned git commit, install the pinned LangChain test set, and run
`pytest -q -rs qualification` with `RESEARCH_SWARM_REQUIRE_QUALIFICATION=1`.
With that variable set, a missing OntoHistoCurator install fails instead of
skipping. `test_installed_ontohistocurator_is_the_pinned_commit` also fails if
the installed commit (from pip's `direct_url.json`) differs from the pin, so
install from the git URL (`git+https://...@<commit>` or
`git+file:///path/to/clone@<commit>`), not from a plain directory.

To change the pin, edit `constraints/ontohistoria-qualification.txt`, rerun the
script, and record the commit and results in `qualification/REPORT.md`.
Report qualification results by hand in the PR (commit, date, pass count).
