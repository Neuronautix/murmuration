# HCM extraction against HCMO 0.4.1

Semantic reference: [HCMO v0.4.1](https://github.com/dhuzard/HCMO/tree/v0.4.1),
tag commit `9503ae4827525d3c82cb0a2cc56a1ce52da39159`, ontology version IRI
`https://w3id.org/hcmo/ontology/hcm/0.4.1`. The source manifest, active modules,
term inventory, and shapes were inspected at that tag. HCMO is CC BY 4.0;
this pack references four term IRIs and does not copy its ontology or shapes.

| Earlier quality-pass rule | HCMO 0.4.1 finding | METODAX decision |
|---|---|---|
| Extract species, strain, sex, age, housing, light/dark wording, sensor and software details, and concise claims from title/abstract | HCMO has a `bio:Subject` with `bio:hasSpecies`, `env:LightCycle`, `tech:Sensor`, `tech:Software`, and `obs:BehaviorObservation`; these are distinct concepts | Keep conservative text extraction, stable PMID source IDs, and source anchors. Fix female-only sex parsing. Add explicit sensor and software-version patterns. |
| Treat any “light/dark” mention as a schedule mapping | `env:LightCycle` describes phase timing and transitions; its shape limits phase properties but does not require each one | Suggest `env:LightCycle` only when a numeric phase schedule was extracted. Qualitative dark-phase wording remains a coverage gap. |
| Generate high-confidence MNMS mappings from field names or “core” labels | HCMO is not an MNMS mapping authority; neither a category label nor missingness establishes semantic fit | Remove the pilot's hardcoded MNMS crosswalk and confidence scores. Preserve explicit user-authored MNMS proposals in the exporter at neutral confidence pending review. |
| Mint `has_species`, `uses_system`, and `measures` graph assertions from keywords | These local predicates and a commercial platform name do not establish typed HCMO instances or reviewed property mappings | Stop automatic graph assertions. Keep the evidence and list platform, housing, metric, and temporal coverage gaps. |
| Add corpus-level “missing metadata” assertions under the first PMID | Absence from an abstract is not evidence that a full study or a typed HCMO instance violates a shape | Keep per-PMID review notes that ask for full-method inspection. Do not emit synthetic metadata values or compliance judgments. |

The optional allowlist maps a reported `species` field to `bio:hasSpecies`, an
explicit sensor modality to `tech:hasSensorTechnology`, a software version to
`tech:hasVersion`, and an explicit numeric light/dark schedule to the
`env:LightCycle` concept. Each suggestion is a MAPPPP 0.1.0 `proposed` mapping
with the original paper provenance and the pinned HCMO version. A suggestion
does not assert an ontology instance. Fields without a defensible term mapping
appear in `hcmo_coverage.json` with a source ID and reason.
The fixed `0.7` mapping confidence is an uncalibrated review-priority heuristic,
not a measured probability of semantic correctness or HCMO conformance.

HCMO's `bio:SubjectShape` requires `hasSpecies` **when a Subject instance is
validated**; its `env:LightCycleShape` does not require phase duration values.
`tech:SensorShape` requires a sensor label and identifier for a typed Sensor.
METODAX produces text and metadata proposals, not those RDF instances, and
does not run SHACL. OntoHistoCurator owns MAPPPP validation, staging, human
review, and promotion. hcmo-agent-lab owns semantic benchmarking.
