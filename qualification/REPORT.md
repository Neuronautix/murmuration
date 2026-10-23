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
| METODAX suite under compatible pinned LangChain versions | 23 passed |
| Ruff on changed Python files | Passed |
| Clean virtual environment, OntoHistoCurator pinned at `c426705` | 5 passed; one dependency deprecation warning |
| Real MAPPPP 0.1.0 validator on generated pilot and exporter bundles | Passed |
| Native ingestion / proposal-only staging / no graph promotion | Passed |
| Repeated identical export and ingestion | Passed, identical payload and staged record |
| Two distinct scientific exports in one second | Passed, distinct content IDs |
| Unresolved citation reference | Rejected before export |
| Same package ID with changed content | Rejected without new staged record |
| Historical SAS native fixtures and legacy conversion | Passed |
| Generated output against frozen SAS exporter fixture | Matched scientific content; only identity and source anchors changed |

## Donor archival decision: NO-GO

Keep both donor repositories active until the three METODAX PRs are reviewed
and merged, OntoHistoCurator's immutable-ingest commit is available on its
default branch or in a release, and the source/licence record for SAS is
resolved. The SAS donor commit has no license file; METODAX records attribution
but this report does not infer a licence grant. The qualification tests use the
unmerged OntoHistoCurator branch commit, so they do not establish the same
immutable-ID behavior for `main` at `199ef02`.

Neither donor repository was modified or archived. No PR was merged.
