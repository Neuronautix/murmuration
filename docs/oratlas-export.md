# Export a swarm review to ORAtlas

The exporter transforms an existing, cited final report and its traceability
matrix into ORAtlas manifest 1.0.0 and claim/citation/relation JSONL. It makes no
network or model calls. Agent behavior and prompts are unchanged.

```sh
python -m automation.exporters.oratlas --run path/to/run --out path/to/review-repo --repository-url https://github.com/owner/review-repo --title "Review title" --abstract "Author-supplied abstract" --license CC-BY-4.0 --keyword neuroscience
python -m automation.exporters.oratlas --check --out path/to/review-repo
```

Supply metadata explicitly so the exporter does not invent a title, abstract,
license, repository identity or publication DOI. `--language` defaults to `en`;
repeat `--keyword` for multiple terms. Swarm output defaults to
`--review-type ai-synthesis`; use `computational-literature-review` only for an
actually human-curated computational review. Optional `--version-doi` and
`--concept-doi` remain distinct and accept bare, `doi:` or DOI-resolver forms.
They are normalized to lowercase bare DOIs; `10.5555/` identifiers are rejected.

`--check` validates existing files without regenerating or repairing them. It
checks the manifest with `jsonschema`, bounded record shapes and enums, duplicate
IDs/pairs, normalized DOIs, all relation endpoints, UTF-8/LF/trailing newlines,
and safe paths (including symlink escape). Errors produce a nonzero exit status.

## Existing data and conservative extraction

The general swarm holds claims and agent outputs in graph state; its run log
records events rather than a persistent, complete claim–citation graph. Its
traceability table stores source, agent, claim, method and epistemic tag. The HCM
pilot additionally writes structured MAPPPP metadata/evidence assertions and
provenance, cached paper metadata, a final report and an eight-column matrix.
MAPPPP mappings and graph proposals are not automatically ORAtlas review claims.
`tests/fixtures/hcm_circadian_mouse_demo.yaml` is a pilot specification, and
`examples/hcm/HCMO_REVIEW.md` is an implementation audit, not a finished review.

The adapter reads the existing Markdown tables, rather than interpreting report
prose as new knowledge. A matrix claim must match a report line or table cell
after whitespace, Markdown decoration, epistemic tags and citation markers are
removed. It must also have an explicit matching DOI, PMID or source URL at that
report occurrence, directly or through a numbered bibliography reference. This
supports both live-search and HCM matrices. A numbered source such as `[1]` in
the matrix resolves through that same report bibliography. Paraphrases, ambiguous
multi-work source cells, uncited claims and internal notes are skipped with a
record ID and reason. Use separate matrix rows for separate works cited by the
same claim. No fuzzy matching or inferred support judgments are performed.

Report discovery accepts one `final_report.md` at the run root or below
`Drafts/`. Matrix discovery accepts one `Knowledge_Traceability_Matrix*.md` or
`traceability.md`. Specify `--report` and `--matrix` for other layouts or when
there are multiple candidates. Explicit paths are relative to the working directory.
The run/example directory must contain completed output; a configuration-only
example is insufficient.

Claims retain hedges in their wording and epistemic tags in `qualification`.
An explicit matrix `claim_type` or `relation_type` is validated; missing claim
types are omitted and missing relation types become `unclear`. Merely citing a
work does not establish that it supports the claim. DOI/PMID aliases deduplicate
transitively, conflicting citation identifiers fail validation, and content-based
IDs do not depend on row order. No TRUST assessments are emitted.

## Files and reproducibility

The output root receives `review-manifest.json` and these files:

- `knowledge/oratlas/claims.jsonl`
- `knowledge/oratlas/citations.jsonl`
- `knowledge/oratlas/relations.jsonl`
- `knowledge/oratlas/provenance.json` (source hashes, contract pin, skipped rows;
  config hash, roster, model identity and existing run timestamp when available)
- `knowledge/oratlas/report.md` and `traceability.md` (traceable source copies)

All output uses UTF-8 without BOM, LF and a trailing newline. JSON keys and
record ordering are deterministic. No export-time timestamp is introduced. An
empty JSONL stream contains one newline. The exporter rejects streams above
ORAtlas's 5,000-record limit. Treat the output directory as exporter-owned: these
named files are replaced on re-export.

The unmodified upstream JSON Schema is packaged in
`automation/exporters/schemas/review-manifest.schema.json`, fetched from
[ORAtlas commit 8fba7b1](https://github.com/dhuzard/oratlas/tree/8fba7b16f4ca2c09d21d2be5ca1bfb94dd03b0fc).
Record shapes follow `packages/contracts/src/artifacts.ts`, the
[manifest documentation](https://github.com/dhuzard/oratlas/blob/main/docs/review-manifest.md)
and the [reference exporter](https://github.com/Neuronautix/ComputationalReviewTemplate_trust-knowledge/tree/main/scripts/oratlas).
ORAtlas code is MIT licensed; the schema retains its upstream contents.

**Temporary compatibility override:** the pinned JSON Schema omits
`ai-synthesis`, although ORAtlas's runtime enum accepts it.
`schema_with_ai_synthesis_compatibility_override()` adds only `ai-synthesis` to
`review.reviewType.enum` in memory. The vendored schema is not edited. Remove
this function's override when the fixed schema from
[ORAtlas PR #244](https://github.com/dhuzard/oratlas/pull/244) is released and the
pin is updated. No other validation exceptions apply.

## Publish and verify

Export into a review repository, run `--check`, commit the artifacts and source
report, then tag and publish a GitHub release. Optionally archive that release
with Zenodo. Re-export with the actual version/concept DOI values once assigned;
never publish example DOIs. Ensure the manifest's HTTPS repository URL identifies
the repository containing these files.

From a neighboring ORAtlas checkout, run:

```sh
pnpm ingest https://github.com/owner/review-repo
```

Confirm nonzero claims, citations and relations in `knowledgeCounts`, with no
extraction errors. Structural ingestion does not establish scientific validity.
To roll back this integration, remove the generated manifest/artifacts from the
review repository; no swarm or ORAtlas server state is changed by exporting.

The checked-in golden HCM fixture freezes the existing deterministic pilot's
five-PMID report and matrix. Its export contains 58 claims, 5 citations and 58
relations. It skips 44 metadata rows absent verbatim from the report, 4 internal
notes without scholarly identifiers and 10 notes lacking a matching citation at
their report occurrence. Every skipped row is listed in `provenance.json`.

Live interoperability check (2026-10-28):
[public test export](https://github.com/dhuzard/murmuration-oratlas-export-test/tree/fd44ab71cec060f70a6c6b427c348010d1e05025) was ingested with
`pnpm ingest https://github.com/dhuzard/murmuration-oratlas-export-test` from the
neighboring ORAtlas checkout. It returned 58 claims, 5 citations, 58 relations
and 0 TRUST records. A follow-up `extractKnowledgeWithOutcomes` check found zero
inspection/extraction warnings, zero skipped artifact records and no artifact
issues. The checked-out ORAtlas commit was
`f958a098c022d3e6a752a828adab1802dd185500`. Compatibility was
`partially-compatible`, with a recommendation to publish a GitHub release.
