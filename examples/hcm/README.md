# HCM domain pack 1.0.0

Eight Home Cage Monitoring personas and their scientific review rules are
adapted from Neuronautix/Science-Agent-Squad at `3c7479f93ef1c87e9800c208893029b698bc78dd`.
That donor repository had no license file at this commit. The source and
authorship are recorded here; reuse rights should be checked before archival.

The pack runs on METODAX's existing graph, tools, builder, ingestion, and CLI.
It adds no runtime engine. To use it:

```shell
python -m automation.main execute "Review HCM metadata gaps" --config examples/hcm/swarm_config.yml
```

The selected configuration keeps proposal language, MNMS classification,
epistemic tags, reviewer rules, and human review checkpoints. It needs the
usual model and search credentials for a live run. Offline routing checks
are in `tests/test_hcm_pack.py`.

## Deterministic pilot and MAPPPP producer

```shell
python -m automation.main pilot hcm --spec-path tests/fixtures/hcm_circadian_mouse_demo.yaml --output-root Drafts/hcm_pilot
python -m automation.main export mapppp-hcm --draft-path Drafts/HCMSAS/final_report.md --traceability-matrix Knowledge_Traceability_Matrix_HCMSAS.md
```

The pilot caches PubMed records by PMID and keeps extraction intermediates for
resumption. The producer retains scientific assertions, uncertainty labels,
review notes, and proposal-only statuses. Its source registry persists beside
the draft by default. Numbered citations resolve through the draft References
section and traceability matrix to stable DOI, PMID, or reference hashes. An
unresolved citation fails export. Package IDs are SHA-256 hashes of canonical
content, and the registry retains the first creation time for identical
retries. The registry and pilot manifest are run bookkeeping, not MAPPPP
fields. OntoHistoCurator owns MAPPPP validation, schema, staging, and promotion.

The producer and pilot logic are adapted from the same donor commit listed
above. Frozen donor fixtures remain in `tests/fixtures` for comparison.
