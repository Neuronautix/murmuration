"""Qualification against the real pinned OntoHistoCurator validator and staging workflow."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mapppp.legacy import convert_bundle
from mapppp.validators import validate_bundle
from ontohistoria.config import Settings
from ontohistoria.services import WorkflowValidationError
from ontohistoria.storage import Stores
from ontohistoria.workflow import OntoHistoriaWorkflow

from automation.exporters.mapppp import export_hcm_mapppp_bundle
from automation.hcm_pilot import run_hcm_pilot
from tests.test_hcm_pilot import _fake_paper
from tests.test_mapppp_export import _write_hcm_fixture_files

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def workflow(path):
    settings = Settings(data_dir=path / "ontohistoria", fuseki_data_endpoint=None,
                        minio_endpoint=None, minio_access_key=None,
                        minio_secret_key=None, minio_bucket="qualification")
    settings.ensure_directories()
    return OntoHistoriaWorkflow(stores=Stores(settings))


def generated(tmp_path, monkeypatch):
    monkeypatch.setattr("automation.hcm_pilot._fetch_pubmed_paper", _fake_paper)
    outputs = run_hcm_pilot(FIXTURES / "hcm_circadian_mouse_demo.yaml", tmp_path / "pilot")
    payload = json.loads(outputs["bundle"].read_text(encoding="utf-8"))
    return outputs, payload


def test_pilot_native_validation_staging_retry_and_no_promotion(tmp_path, monkeypatch):
    outputs, payload = generated(tmp_path, monkeypatch)
    validated = validate_bundle(payload)
    assert validated.package_metadata.contract_version == "0.1.0"
    assert all(a.status == "proposed" for family in (
        validated.metadata_assertions, validated.evidence_assertions,
        validated.mapping_assertions, validated.graph_assertions) for a in family)
    ingest = workflow(tmp_path)
    first = ingest.ingest_mapppp_bundle_file(outputs["bundle"])
    second = ingest.ingest_mapppp_bundle_file(outputs["bundle"])
    assert first == second
    assert first["workflow_state"] == "staged"
    assert first["review_status"] == "proposed"
    assert first["graph_role"] == "proposal_bundle_graph"
    assert len(ingest.list_staged_mapppp()) == 1
    assert ingest.stores.accepted.list_all() == []
    assert list(ingest.stores.settings.graphs_dir.rglob("*")) == []
    retry = run_hcm_pilot(FIXTURES / "hcm_circadian_mouse_demo.yaml", tmp_path / "pilot")
    assert retry["bundle"].read_bytes() == outputs["bundle"].read_bytes()


def test_distinct_same_second_and_immutable_conflict(tmp_path):
    config, matrix, draft, notes = _write_hcm_fixture_files(tmp_path)
    kwargs = dict(config_path=config, draft_path=draft, traceability_matrix_path=matrix,
                  review_notes_path=notes, exported_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
    first = export_hcm_mapppp_bundle(**kwargs).model_dump(mode="json", exclude_none=True)
    draft.write_text(draft.read_text(encoding="utf-8").replace(
        "Light cycle transition timing was not reported", "Light cycle and temperature were not reported"),
        encoding="utf-8")
    second = export_hcm_mapppp_bundle(**kwargs).model_dump(mode="json", exclude_none=True)
    validate_bundle(first)
    validate_bundle(second)
    assert first["package_metadata"]["created_at"] == second["package_metadata"]["created_at"]
    assert first["package_metadata"]["package_id"] != second["package_metadata"]["package_id"]
    ingest = workflow(tmp_path)
    ingest.ingest_mapppp_bundle(first)
    ingest.ingest_mapppp_bundle(second)
    assert len(ingest.list_staged_mapppp()) == 2
    conflict = deepcopy(first)
    conflict["evidence_assertions"][0]["value"] = "Different content with the same immutable ID"
    with pytest.raises(WorkflowValidationError, match="different content"):
        ingest.ingest_mapppp_bundle(conflict)
    assert len(ingest.list_staged_mapppp()) == 2


def test_historical_sas_fixtures_and_legacy_converter(tmp_path):
    ingest = workflow(tmp_path)
    for name in ("hcm_mapppp_pilot_baseline_v0_1_0.json",
                 "hcm_circadian_mouse_demo_pilot_v1_bundle.json"):
        path = FIXTURES / name
        assert validate_bundle(path.read_bytes()).package_metadata.domain_type == "hcm"
        assert ingest.ingest_mapppp_bundle_file(path)["workflow_state"] == "staged"
    legacy = {
        "package_metadata": {"exported_at": "2026-04-10T09:06:37+00:00",
                             "swarm_name": "HCMSAS", "exporter": "SAS"},
        "study_context": {"domain": "preclinical home cage monitoring", "species": "mouse"},
        "source_package_metadata": {"references": {"[1]": "Historical HCM report"}},
        "metadata_assertions": [], "evidence_assertions": [], "review_notes": [],
        "candidate_mnms_mappings": [], "candidate_graph_assertions": [],
    }
    converted = convert_bundle(legacy)
    assert validate_bundle(converted).package_metadata.contract_version == "0.1.0"
    path = tmp_path / "sas-legacy.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")
    assert ingest.ingest_mapppp_bundle_file(path)["workflow_state"] == "staged"


def test_producer_citation_failure_and_real_validation(tmp_path):
    config = ROOT / "examples/hcm/swarm_config.yml"
    draft = tmp_path / "draft.md"
    matrix = tmp_path / "matrix.md"
    matrix.write_text("| claim_id | claim | epistemic_tag | source | agent | mnms_category | notes | status |\n"
                      "|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    draft.write_text("## E. Missing metadata and ambiguity report\n- [MISSING] Timing absent [9].\n"
                     "## I. References\n[1] A source.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unresolved citation reference"):
        export_hcm_mapppp_bundle(config, draft, matrix)
    draft.write_text(draft.read_text(encoding="utf-8").replace("[9]", "[1]"), encoding="utf-8")
    bundle = export_hcm_mapppp_bundle(config, draft, matrix,
                                      exported_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
    assert validate_bundle(bundle.model_dump(mode="json", exclude_none=True))


def test_actual_producer_content_matches_frozen_sas_fixture(tmp_path):
    config, matrix, draft, notes = _write_hcm_fixture_files(tmp_path)
    actual = export_hcm_mapppp_bundle(
        config, draft, matrix, notes,
        exported_at=datetime(2026, 4, 10, 13, 11, 28, tzinfo=timezone.utc),
    ).model_dump(mode="json", exclude_none=True)
    validate_bundle(actual)
    donor = json.loads((FIXTURES / "hcm_mapppp_pilot_baseline_v0_1_0.json").read_text(encoding="utf-8"))
    for bundle in (actual, donor):
        bundle.pop("sources", None)
        bundle["package_metadata"].pop("package_id", None)
        for family in ("metadata_assertions", "evidence_assertions", "mapping_assertions",
                       "graph_assertions", "review_notes"):
            for item in bundle[family]:
                item["provenance"].pop("source_id", None)
                item["provenance"].pop("source_anchor", None)
    assert actual == donor
