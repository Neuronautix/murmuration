# HCM consolidation qualification — 2026-10-23

## Source revisions

| Repository | Refreshed revision | Role |
|---|---|---|
| Neuronautix/Science-Agent-Squad | `3c7479f93ef1c87e9800c208893029b698bc78dd` | Donor personas, pilot, fixtures, producer |
| dhuzard/metodax | `ded77fe45ffaed6d4e872e0deb39019b3be845e2` | Baseline runtime |
| Neuronautix/OntoHistoCurator main | `199ef02717740ddeda2d61644f12faac1fd8f73d` | Current default branch |
| Neuronautix/OntoHistoCurator immutable-ingest branch | `c426705611df098256c15416ae07f084191c395e` | Pinned qualification target |

OntoHistoCurator had no published release at the time of qualification. The
exact installed commit was verified from the package's `direct_url.json`.

## Reproduction and results

The pre-change METODAX environment had `langchain 1.2.14` with
`langchain-core 0.3.84`. Three tool tests failed at `langchain.debug`; three
config fixtures also failed on Windows cp1252 when writing emoji. The coherent
`langchain 0.3.27`, `langchain-core 0.3.84`, `langgraph 0.6.11` set and
explicit UTF-8 fixture writes passed all 9 original tests. No test was skipped.

| Gate | Result |
|---|---|
| METODAX suite under compatible pinned LangChain versions | 29 passed after HCMO refinement |
| Ruff on changed Python files | Passed |
| Clean virtual environment, OntoHistoCurator pinned at `c426705` | 6 passed after HCMO refinement |
| Real MAPPPP 0.1.0 validator on generated pilot and exporter bundles | Passed |
| Native ingestion / proposal-only staging / no graph promotion | Passed |
| Repeated identical export and ingestion | Passed, identical payload and staged record |
| Two distinct scientific exports in one second | Passed, distinct content IDs |
| Unresolved citation reference | Rejected before export |
| Same package ID with changed content | Rejected without new staged record |
| Historical SAS native fixtures and legacy conversion | Passed |
| Generated output against frozen SAS exporter fixture | Matched scientific content; only identity and source anchors changed |

## Donor archival decision: NO-GO

Keep both donor repositories active until the four METODAX PRs are reviewed
and merged, the integrated `main` CI (including cross-repository qualification)
passes, OntoHistoCurator's immutable-ingest commit is available on its
default branch or in a release, and the source/licence record for SAS is
resolved. The SAS donor commit has no license file; METODAX records attribution
but this report does not infer a licence grant. The qualification tests use the
unmerged OntoHistoCurator branch commit, so they do not establish the same
immutable-ID behavior for `main` at `199ef02`.

Neither donor repository was modified or archived. No PR was merged.

## HCMO semantic refinement

HCMO v0.4.1 was inspected at tag commit `9503ae4827525d3c82cb0a2cc56a1ce52da39159`.
The pilot now separates source-linked extraction from optional term suggestions
and no longer emits abstract-derived graph triples or synthetic missingness
assertions. Six qualification tests include real MAPPPP validation of optional
HCMO suggestions and a pilot-report-to-exporter round trip. The exact concept
and constraint comparison is in `examples/hcm/HCMO_REVIEW.md`.

## Integration gate update — 2026-10-23

PR #1's GitHub CI passed Python 3.10, 3.11, and 3.12 after all Ruff findings
were fixed. PR #2's three matrix jobs also passed. PR #3 adds a pinned
OntoHistoCurator qualification job to the same workflow, which also runs on
pushes to `main`. Because OntoHistoCurator is private and its organization
disables deploy keys, that job requires a GitHub App installed for that
repository with `contents: read`, plus the
`ONTOHISTOCURATOR_READ_APP_CLIENT_ID` METODAX variable and
`ONTOHISTOCURATOR_READ_APP_PRIVATE_KEY` Actions secret. It fails at the
explicit credential check until these are configured. All six
qualification tests passed locally against the pinned commit. No PR or donor
repository has been merged or archived.
