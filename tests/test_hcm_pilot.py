import json
from pathlib import Path

from automation.hcm_pilot import CachedPaper, run_hcm_pilot


def _fake_paper(pmid: str) -> CachedPaper:
    fixtures = {
        "39880968": CachedPaper(
            pmid="39880968",
            title="Evaluation of the Digital Ventilated Cage system for circadian phenotyping in mice.",
            abstract="Mice were monitored in a Digital Ventilated Cage system to quantify circadian locomotor activity.",
            journal="Scientific Reports",
            year="2025",
            doi="10.1038/example39880968",
        ),
        "39371613": CachedPaper(
            pmid="39371613",
            title="Accurate locomotor activity profiles of group-housed mice derived from home cage monitoring data.",
            abstract="Group-housed mice showed locomotor activity profiles suitable for home cage monitoring analyses.",
            journal="Frontiers in Neuroscience",
            year="2024",
            doi="10.3389/example39371613",
        ),
        "39763604": CachedPaper(
            pmid="39763604",
            title="Detection of aberrant locomotor activity in a mouse model of lung cancer via home cage monitoring.",
            abstract="A mouse lung cancer model was followed with home cage locomotor activity monitoring.",
            journal="Frontiers in Oncology",
            year="2024",
            doi="10.3389/example39763604",
        ),
        "39296476": CachedPaper(
            pmid="39296476",
            title="Continuous locomotor activity monitoring to assess animal welfare following intracranial surgery in mice.",
            abstract="Post-surgical mice underwent continuous locomotor activity monitoring to assess welfare.",
            journal="Frontiers in Behavioral Neuroscience",
            year="2024",
            doi="10.3389/example39296476",
        ),
        "39534023": CachedPaper(
            pmid="39534023",
            title="Automated home cage monitoring of an aging colony of mice.",
            abstract="An aging colony of mice was assessed with automated home cage monitoring and locomotor activity endpoints.",
            journal="Frontiers in Neuroscience",
            year="2024",
            doi="10.3389/example39534023",
        ),
    }
    return fixtures[pmid]


def test_run_hcm_pilot_writes_draft_traceability_and_bundle(tmp_path: Path, monkeypatch):
    spec_path = Path("tests/fixtures/hcm_circadian_mouse_demo.yaml")
    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", _fake_paper)

    outputs = run_hcm_pilot(spec_path=spec_path, output_root=tmp_path)

    assert outputs["draft"].exists()
    assert outputs["traceability_matrix"].exists()
    assert outputs["bundle"].exists()

    draft_text = outputs["draft"].read_text(encoding="utf-8")
    matrix_text = outputs["traceability_matrix"].read_text(encoding="utf-8")
    bundle = json.loads(outputs["bundle"].read_text(encoding="utf-8"))

    assert "## D. Optional HCMO 0.4.1 term suggestions" in draft_text
    assert "| claim_id | claim | epistemic_tag | source | agent | mnms_category | notes | status |" in matrix_text
    assert bundle["package_metadata"]["contract_version"] == "0.1.0"
    assert bundle["package_metadata"]["domain_type"] == "hcm"
    assert bundle["metadata_assertions"]
    assert bundle["evidence_assertions"]
    assert bundle["mapping_assertions"] == []
    assert bundle["graph_assertions"] == []
    assert outputs["coverage"].exists()
    assert bundle["review_notes"]
    assert len(bundle["evidence_assertions"]) > 5
    assert len(bundle["review_notes"]) >= 4


def test_run_hcm_pilot_resumes_from_cached_intermediates(tmp_path: Path, monkeypatch):
    spec_path = Path("tests/fixtures/hcm_circadian_mouse_demo.yaml")
    fetch_calls: list[str] = []

    def fake_fetch(pmid: str) -> CachedPaper:
        fetch_calls.append(pmid)
        return _fake_paper(pmid)

    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", fake_fetch)
    first_outputs = run_hcm_pilot(spec_path=spec_path, output_root=tmp_path)
    assert len(fetch_calls) == 5
    first_bundle = first_outputs["bundle"].read_bytes()

    def fail_fetch(_pmid: str) -> CachedPaper:
        raise AssertionError("cache was not reused")

    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", fail_fetch)
    second_outputs = run_hcm_pilot(spec_path=spec_path, output_root=tmp_path)

    assert first_outputs["manifest"].read_text(encoding="utf-8") == second_outputs["manifest"].read_text(encoding="utf-8")
    assert second_outputs["bundle"].exists()
    assert second_outputs["bundle"].read_bytes() == first_bundle
    assert second_outputs["draft"].exists()


def test_run_hcm_pilot_v2_improves_assertion_quality_over_frozen_v1(tmp_path: Path, monkeypatch):
    spec_path = Path("tests/fixtures/hcm_circadian_mouse_demo.yaml")
    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", _fake_paper)

    outputs = run_hcm_pilot(spec_path=spec_path, output_root=tmp_path)
    current = json.loads(outputs["bundle"].read_text(encoding="utf-8"))
    baseline = json.loads((Path("tests/fixtures/hcm_circadian_mouse_demo_pilot_v1_bundle.json")).read_text(encoding="utf-8"))

    assert len(current["evidence_assertions"]) > len(baseline["evidence_assertions"])
    assert len(current["review_notes"]) > len(baseline["review_notes"])

    current_fields = {item["field"] for item in current["metadata_assertions"]}
    baseline_fields = {item["field"] for item in baseline["metadata_assertions"]}
    expected_fields = baseline_fields - {"classifier_version", "light_cycle_parameters"}
    assert current_fields.issuperset(expected_fields)
    assert {"strain", "sex", "age", "genotype_or_model", "monitoring_duration", "light_dark_schedule", "housing_specifics", "activity_metric_definition"} & current_fields
    assert "processing_and_analysis_details" not in current_fields
    assert "light_cycle_parameters" not in current_fields
    assert "classifier_version" not in current_fields

    sample_claims = [item["observation"] for item in current["evidence_assertions"][:3]]
    assert all(len(claim) < 400 for claim in sample_claims)


def test_pilot_optional_hcmo_suggestions_are_pinned_and_source_linked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", _fake_paper)
    outputs = run_hcm_pilot("tests/fixtures/hcm_circadian_mouse_demo.yaml", tmp_path, suggest_hcmo=True)
    bundle = json.loads(outputs["bundle"].read_text(encoding="utf-8"))
    coverage = json.loads(outputs["coverage"].read_text(encoding="utf-8"))
    assert coverage["suggestions_enabled"] is True
    assert coverage["ontology"] == "https://w3id.org/hcmo/ontology/hcm/0.4.1"
    assert bundle["mapping_assertions"]
    assert all(item["target_schema"] == "HCMO 0.4.1" and item["status"] == "proposed"
               and item["provenance"]["source_id"].startswith("PMID:")
               for item in bundle["mapping_assertions"])
    assert bundle["graph_assertions"] == []
    assert coverage["gaps"]
