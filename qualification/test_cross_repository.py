"""Qualification against the real pinned OntoHistoCurator validator and staging workflow.

OntoHistoCurator is private and AGPL-3.0. It is never imported by the runtime
and never vendored: it is installed only into a separate qualification
environment (see ``qualification/README.md``). When it is not installed, the
whole module is reported as skipped with an ``UNVERIFIED`` reason, unless
``RESEARCH_SWARM_REQUIRE_QUALIFICATION=1`` is set, in which case it fails.
"""

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
CONSTRAINTS = ROOT / "constraints" / "ontohistoria-qualification.txt"
UNVERIFIED = ("UNVERIFIED: OntoHistoCurator (private) is not installed; cross-repository "
              "qualification did not run. Run it manually, see qualification/README.md.")

try:
    import ontohistoria  # noqa: F401
except ImportError:
    if os.environ.get("RESEARCH_SWARM_REQUIRE_QUALIFICATION") == "1":
        raise
    pytest.skip(UNVERIFIED, allow_module_level=True)

from mapppp.legacy import convert_bundle  # noqa: E402
from mapppp.validators import validate_bundle  # noqa: E402
from ontohistoria.config import Settings  # noqa: E402
from ontohistoria.services import WorkflowValidationError  # noqa: E402
from ontohistoria.storage import Stores  # noqa: E402
from ontohistoria.workflow import OntoHistoriaWorkflow  # noqa: E402

from automation.exporters.mapppp import export_hcm_mapppp_bundle  # noqa: E402
from automation.hcm_pilot import run_hcm_pilot  # noqa: E402
from tests.test_hcm_pilot import _fake_paper  # noqa: E402
from tests.test_mapppp_export import _write_hcm_fixture_files  # noqa: E402


def _pinned_commit():
    for line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        if line.startswith("ontohistoria @ ") and "@" in line.rsplit("/", 1)[-1]:
            return line.rsplit("@", 1)[-1].strip()
    raise AssertionError(f"No pinned ontohistoria commit in {CONSTRAINTS}")


def test_installed_ontohistocurator_is_the_pinned_commit():
    from importlib.metadata import distribution

    direct_url = distribution("ontohistoria").read_text("direct_url.json")
    assert direct_url, "ontohistoria must be installed from a git URL so its commit is recorded"
    commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id")
    assert commit == _pinned_commit(), (
        f"Installed OntoHistoCurator commit {commit!r} is not the pinned {_pinned_commit()!r}")


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
    optional = run_hcm_pilot(FIXTURES / "hcm_circadian_mouse_demo.yaml", tmp_path / "pilot",
                             suggest_hcmo=True)
    suggested = validate_bundle(optional["bundle"].read_bytes())
    assert all(item.status == "proposed" for item in suggested.mapping_assertions)
    assert ingest.ingest_mapppp_bundle_file(optional["bundle"])["workflow_state"] == "staged"
    assert ingest.stores.accepted.list_all() == []
    assert list(ingest.stores.settings.graphs_dir.rglob("*")) == []


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
                if family == "mapping_assertions":
                    item.pop("confidence", None)
    assert actual == donor


def test_pilot_report_export_keeps_pmid_anchors_and_mapppp_contract(tmp_path, monkeypatch):
    outputs, _ = generated(tmp_path, monkeypatch)
    exported = export_hcm_mapppp_bundle(
        ROOT / "examples/hcm/swarm_config.yml", outputs["draft"], outputs["traceability_matrix"],
        suggest_hcmo=True, coverage_path=tmp_path / "coverage.json",
    )
    normalized = validate_bundle(exported.model_dump(mode="json", exclude_none=True)).normalized()
    assert normalized["package_metadata"]["contract_version"] == "0.1.0"
    assert all(item["status"] == "proposed" for item in normalized["mapping_assertions"])
    assert all(item["provenance"]["source_id"].startswith("PMID:")
               for item in normalized["evidence_assertions"]
               if item["assertion_id"].startswith("evidence-table-"))
    assert {item["provenance"]["source_id"] for item in normalized["evidence_assertions"]} <= {
        "seed_corpus", *(f"PMID:{pmid}" for pmid in ("39880968", "39371613", "39763604", "39296476", "39534023"))}
    assert (tmp_path / "coverage.json").exists()
