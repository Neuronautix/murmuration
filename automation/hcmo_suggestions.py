"""Optional, evidence-linked term suggestions for the pinned HCMO 0.4.1 release.

This is a small reviewed crosswalk, not an ontology copy or a SHACL validator.
Unknown fields and incomplete context remain coverage gaps for human review.
"""

from __future__ import annotations

import re
from typing import Any

HCMO_VERSION = "0.4.1"
HCMO_RELEASE_COMMIT = "9503ae4827525d3c82cb0a2cc56a1ce52da39159"
HCMO_VERSION_IRI = "https://w3id.org/hcmo/ontology/hcm/0.4.1"
HCMO_SOURCE = {"source_id": "HCMO:0.4.1", "source_type": "other", "uri": HCMO_VERSION_IRI}
# Fixed review-priority heuristic, not a calibrated probability of mapping correctness.
HCMO_SUGGESTION_CONFIDENCE = 0.7

# Verified against HCMO v0.4.1 dist/profile.json and the source modules.
# These are mapping suggestions to existing terms, never instance assertions.
_CROSSWALK = {
    "species": (
        "https://w3id.org/hcmo/ontology/hcm/bio#hasSpecies",
        "A reported subject species may populate hasSpecies after the subject is identified.",
    ),
    "sensor_modality": (
        "https://w3id.org/hcmo/ontology/hcm/tech#hasSensorTechnology",
        "A reported sensor modality may populate hasSensorTechnology after a sensor is identified.",
    ),
    "software_version": (
        "https://w3id.org/hcmo/ontology/hcm/tech#hasVersion",
        "A reported software or classifier version may populate hasVersion after the software component is identified.",
    ),
    "light_dark_schedule": (
        "https://w3id.org/hcmo/ontology/hcm/env#LightCycle",
        "An explicit light/dark timing may inform a LightCycle description; no instance is asserted.",
    ),
}

_GAP_REASONS = {
    "monitoring_platform": "No direct active HCMO 0.4.1 class for a named commercial platform; inspect system vocabulary and components.",
    "housing_context": "Group housing alone does not establish a timed HousingAssignment to a monitored enclosure.",
    "housing_specifics": "Housing wording lacks the subject, enclosure, and validity interval needed for HousingAssignment.",
    "endpoint": "A named endpoint alone is not a BehaviorObservation or a measured result.",
    "activity_metric_definition": "A metric label alone does not establish an HCMO observation, result, or behavior type.",
    "temporal_structure": "Circadian wording alone does not supply a LightCycle timing or an ObservationWindow.",
    "monitoring_duration": "A narrative duration is not yet a typed HCMO temporal interval.",
    "strain": "No reviewed direct HCMO 0.4.1 term mapping for this extracted field.",
    "sex": "No reviewed direct HCMO 0.4.1 term mapping for this extracted field.",
    "age": "No reviewed direct HCMO 0.4.1 term mapping for this extracted field.",
    "genotype_or_model": "No reviewed direct HCMO 0.4.1 term mapping for this extracted field.",
}


def suggest_hcmo_mappings(metadata_assertions: list[dict[str, Any]]) -> dict[str, Any]:
    """Suggest a few pinned terms; expose every unsupported or incomplete field."""
    mappings: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for item in metadata_assertions:
        field = item["field"]
        value = str(item.get("value") or "")
        source_id = item["provenance"]["source_id"]
        eligible = item.get("epistemic_label") in {"reported", "extracted"} and value.strip()
        reason = None
        if not eligible:
            reason = "Only reported or extracted source content receives a term suggestion."
        elif field == "light_dark_schedule" and not re.search(r"\b\d+\s*[:/]\s*\d+\b", value):
            reason = "Light/dark wording lacks explicit phase timing for an HCMO LightCycle suggestion."
        elif field not in _CROSSWALK:
            reason = _GAP_REASONS.get(field, "No reviewed HCMO 0.4.1 term mapping for this field.")
        if reason:
            gaps.append({"assertion_id": item["assertion_id"], "field": field,
                         "source_id": source_id, "reason": reason})
            continue
        iri, rationale = _CROSSWALK[field]
        provenance = dict(item["provenance"])
        provenance["proposed_by"] = "METODAX HCMO term suggester"
        mappings.append({
            "assertion_id": f"hcmo-{item['assertion_id']}",
            "status": "proposed",
            "epistemic_label": "normalized",
            "provenance": provenance,
            "local_field": field,
            "target_schema": f"HCMO {HCMO_VERSION}",
            "target_field": iri,
            "mapping_rationale": f"{rationale} Source assertion: {item['assertion_id']}. Version: {HCMO_VERSION_IRI}.",
            "confidence": HCMO_SUGGESTION_CONFIDENCE,
        })
    return {
        "ontology": HCMO_VERSION_IRI,
        "release_commit": HCMO_RELEASE_COMMIT,
        "mappings": mappings,
        "gaps": gaps,
    }
