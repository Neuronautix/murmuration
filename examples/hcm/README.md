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
