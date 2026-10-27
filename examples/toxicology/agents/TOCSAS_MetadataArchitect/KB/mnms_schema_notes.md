# MNMS Schema Notes for Toxicology Metadata Mapping

## MNMS Core Schema (mms_schema_v1.jsonld) — Coverage Summary

The current MNMS schema (v1) was designed for preclinical behavioral and sensor-based studies. Its coverage of toxicology-specific metadata is partial. The following assessment is based on the mms_schema_v1.jsonld in the schemas/ directory.

### MNMS Fields Present (Direct Toxicology Applicability)
| MNMS Field | Toxicology Applicability | Notes |
|---|---|---|
| StudyID | High | Maps directly to study identifier. |
| Objective | High | Maps to study purpose/objective. |
| GLPStatus | High | Direct mapping; critical for toxicology. |
| Site | High | Maps to performing laboratory. |
| ProtocolVersion | High | Maps to protocol/amendment version. |
| Species | High | Direct mapping. |
| Strain | High | Direct mapping. |
| Sex | High | Direct mapping. |
| Age | High | Maps to age at study start. |
| Source | High | Maps to animal vendor. |
| MicrobiologicalStatus | High | Maps to SPF/CV status. |
| CageSystem | Medium | Housing; less critical than dosing for tox. |
| GroupSize | High | Maps to n per group. |
| DietWaterAccess | Medium | Relevant to caloric intake and metabolism. |
| LDCycle | Low-Medium | Relevant for circadian-sensitive endpoints. |
| Timestamps | High | Maps to study start/end dates. |
| BatchID | High | Maps to batch/lot number of test article. |

### Major Toxicology Metadata NOT in MNMS Core
| Missing Element | Why Needed | Extension Priority |
|---|---|---|
| Dose levels and units | Cannot interpret dose-response without this | CRITICAL |
| Route of administration | Cannot interpret systemic exposure or target organ without this | CRITICAL |
| Vehicle/formulation composition | Vehicle effects confound findings | CRITICAL |
| Dosing schedule and frequency | AUC/Cmax depend on dosing interval | CRITICAL |
| Study type (DRF/subacute/subchronic/chronic) | Determines interpretive context | HIGH |
| GLP guideline (OECD/ICH number) | Regulatory compliance context | HIGH |
| NOAEL/LOAEL | Core toxicology output | HIGH |
| Histopathology findings | Primary endpoint in most tox studies | HIGH |
| Clinical pathology findings | Standard panel in repeat-dose studies | HIGH |
| Organ weights | Standard parameter; linked to histopath | HIGH |
| Toxicokinetics (AUC, Cmax) | Exposure-response relationship | HIGH |
| Recovery/satellite groups | Reversibility assessment | MEDIUM |
| Formulation stability | Nominal vs. actual dose validity | MEDIUM |
| Adversity criteria applied | Interpretive framework traceability | MEDIUM |
| Pathology grading system | Severity comparability | MEDIUM |

## MNMS Extension Priority Classes
- **CRITICAL**: absence makes toxicological interpretation impossible.
- **HIGH**: absence significantly limits cross-study comparison and MNMS reusability.
- **MEDIUM**: absence reduces FAIR data quality and provenance tracking.
- **LOW**: recommended for completeness; absence does not block primary interpretation.
