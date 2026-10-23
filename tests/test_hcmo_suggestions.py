import json
from pathlib import Path

import pytest

from automation.hcm_pilot import CachedPaper, _build_metadata_and_evidence, _build_review_notes
from automation.hcmo_suggestions import HCMO_RELEASE_COMMIT, HCMO_VERSION_IRI, suggest_hcmo_mappings

CASES = json.loads((Path(__file__).parent / "fixtures/hcmo_synthetic_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["pmid"])
def test_synthetic_hcm_extraction_and_optional_term_suggestions(case):
    paper = CachedPaper(pmid=case["pmid"], title=case["title"], abstract=case["abstract"])
    extracted = _build_metadata_and_evidence([paper], "2026-10-23T00:00:00Z")
    metadata = extracted["metadata_assertions"]
    evidence = extracted["evidence_assertions"]
    assert [item["field"] for item in metadata] == case["expected_fields"]
    assert all(item["provenance"]["source_id"] == f"PMID:{paper.pmid}" for item in metadata + evidence)
    assert not any(item["value"].startswith("not consistently reported") for item in metadata)

    result = suggest_hcmo_mappings(metadata)
    assert result["ontology"] == HCMO_VERSION_IRI
    assert result["release_commit"] == HCMO_RELEASE_COMMIT
    assert sorted(item["local_field"] for item in result["mappings"]) == sorted(case["expected_mapped_fields"])
    assert all(item["status"] == "proposed" and item["provenance"]["source_id"] == f"PMID:{paper.pmid}"
               for item in result["mappings"])
    assert all(item["target_field"].startswith("https://w3id.org/hcmo/ontology/hcm/") for item in result["mappings"])
    notes = _build_review_notes([paper], metadata, evidence, "2026-10-23T00:00:00Z")["review_notes"]
    review_gaps = ["light_dark_schedule" if note["note_id"].endswith("light-timing") else "software_version"
                   for note in notes if note["note_id"].startswith(f"review-{paper.pmid}-")]
    assert review_gaps == case["expected_review_gaps"]
    assert all("conformance" not in note["note"].lower() or "no" in note["note"].lower()
               for note in notes)


def test_unreported_values_and_unknown_fields_never_get_invented_iris():
    metadata = [{
        "assertion_id": "a1", "field": "unknown_platform", "value": "vendor X",
        "epistemic_label": "reported",
        "provenance": {"source_id": "PMID:42", "source_anchor": "abstract", "proposed_by": "test",
                       "created_at": "2026-10-23T00:00:00Z"},
    }, {
        "assertion_id": "a2", "field": "species", "value": "not reported",
        "epistemic_label": "uncertain",
        "provenance": {"source_id": "PMID:42", "source_anchor": "abstract", "proposed_by": "test",
                       "created_at": "2026-10-23T00:00:00Z"},
    }]
    result = suggest_hcmo_mappings(metadata)
    assert result["mappings"] == []
    assert {gap["field"] for gap in result["gaps"]} == {"unknown_platform", "species"}
