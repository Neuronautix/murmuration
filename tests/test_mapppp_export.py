import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from automation.exporters.mapppp import HCM_METADATA_PERSONA, export_hcm_mapppp_bundle
from automation.main import app


def _collect_keys(value):
    keys = set()
    if isinstance(value, dict):
        keys.update(value.keys())
        for item in value.values():
            keys.update(_collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _write_hcm_fixture_files(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    config_text = """
swarm:
  name: "HCMSAS"
  description: >
    Home Cage Monitoring Scientific Agent Squad.
    Investigates preclinical home cage monitoring research with emphasis on MNMS.
  output_dir: "./Drafts/HCMSAS"
  traceability_matrix: "./Knowledge_Traceability_Matrix_HCMSAS.md"

personas:
  - name: "Metadata Architect"
    role: "MNMS extraction and metadata structure mapping"
  - name: "Reproducibility and Bias Auditor"
    role: "Comparability and confounder review"
"""
    config_path = tmp_path / "swarm_config_hcmsas.yml"
    config_path.write_text(config_text.strip() + "\n", encoding="utf-8")

    matrix_text = """# HCMSAS Knowledge Traceability Matrix

## Traceability Log

| claim_id | claim | epistemic_tag | source | agent | mnms_category | notes | status |
|---|---|---|---|---|---|---|---|
| HCM-000 | Matrix initialized for HCMSAS swarm | [FACT] | internal | HCMSAS_DrNexus | provenance_and_versioning | Baseline entry | active |

---

*Append new entries below this line.*
| HCM-001 | Continuous cage-side HCM can capture circadian locomotor activity in mice [1] | [FACT] | DOI:10.1000/hcm.methods.2024 | Metadata Architect | temporal_structure | Anchored to the methods paper. | active |
| HCM-002 | Cross-platform comparisons require explicit sensor equivalence [2] | [INFERENCE] | DOI:10.1000/hcm.review.2025 | Reproducibility and Bias Auditor | acquisition_system | Comparability risk noted in the review. | active |
"""
    matrix_path = tmp_path / "Knowledge_Traceability_Matrix_HCMSAS.md"
    matrix_path.write_text(matrix_text, encoding="utf-8")

    draft_text = """## A. Research scope
This HCMSAS report focuses on preclinical home cage monitoring in mice [1].

## C. Evidence table with in-text citations
| claim | evidence_type | source_anchor | notes |
|---|---|---|---|
| [FACT] Continuous cage-side recording can capture circadian locomotor activity in mice [1] | methods_paper | DOI:10.1000/hcm.methods.2024 | Preserves the raw measurement versus interpretation distinction. |
| [INFERENCE] Cross-platform comparisons require explicit sensor equivalence [2] | review | DOI:10.1000/hcm.review.2025 | Sensor modality and classifier equivalence must be reported. |

## D. HCM MNMS mapping table
| metadata_field | value_or_status | MNMS_category | classification | source | notes |
|---|---|---|---|---|---|
| species | mouse | animal_characteristics | MNMS_core | DOI:10.1000/hcm.methods.2024 | Reported as C57BL/6J mice [1]. |
| classifier_version | missing | software_and_classifier | missing_in_source | DOI:10.1000/hcm.review.2025 | Review flags missing version disclosure [2]. |

## E. Missing metadata and ambiguity report
- [MISSING] Light cycle transition timing was not reported [1].
- [CONTESTED] Software classifier version disclosure remains inconsistent across HCM platforms [2].

## F. Knowledge graph schema proposal
| subject | predicate | object | evidence_level | confidence | source_id | notes |
|---|---|---|---|---|---|---|
| Study_001 | uses_system | HCM_System_DVC | [FACT] | medium | DOI:10.1000/hcm.methods.2024 | System provenance retained [1]. |
| HCM_System_DVC | confounded_by | Classifier_Version_Gap | [INFERENCE] | low | DOI:10.1000/hcm.review.2025 | Missing classifier version weakens comparability [2]. |

## G. Reproducibility and comparability risks
| risk_id | category | severity | description | affected_studies | recommended_action |
|---|---|---|---|---|---|
| R1 | software_and_classifier | Major | Classifier versions were omitted, limiting reproducibility [2]. | Study_001 | Require explicit software version reporting. |

## I. References
[1] Example Methods Consortium. Continuous HCM methods paper. 2024.
[2] Example Review Group. HCM comparability review. 2025.
"""
    draft_path = tmp_path / "hcm_report.md"
    draft_path.write_text(draft_text, encoding="utf-8")

    review_notes = [
        {
            "persona": HCM_METADATA_PERSONA,
            "note": "Keep MNMS mappings proposal-level because several core fields remain missing [1].",
            "note_type": "mnms_review",
            "source_anchor": "DOI:10.1000/hcm.methods.2024",
            "citations": ["[1]"],
        },
        {
            "persona": "Reviewer-2",
            "note": "Do not mark classifier-version mappings as accepted or curator-confirmed [2].",
            "note_type": "reviewer_feedback",
            "source_anchor": "DOI:10.1000/hcm.review.2025",
            "citations": ["[2]"],
        },
    ]
    notes_path = tmp_path / "review_notes.json"
    notes_path.write_text(json.dumps(review_notes, indent=2), encoding="utf-8")

    return config_path, matrix_path, draft_path, notes_path


def _assert_exact_keys(item: dict, expected_keys: set[str]) -> None:
    assert set(item) == expected_keys


def _assert_provenance_shape(item: dict) -> None:
    _assert_exact_keys(item["provenance"], {"source_id", "source_anchor", "proposed_by", "created_at"})
    assert "source_anchors" not in item["provenance"]


def _export_fixture_payload(tmp_path: Path) -> dict:
    config_path, matrix_path, draft_path, notes_path = _write_hcm_fixture_files(tmp_path)
    return export_hcm_mapppp_bundle(
        config_path=config_path,
        draft_path=draft_path,
        traceability_matrix_path=matrix_path,
        review_notes_path=notes_path,
        exported_at=datetime(2026, 4, 10, 13, 11, 28, tzinfo=timezone.utc),
    ).model_dump(mode="json", exclude_none=True)


def test_export_hcm_mapppp_bundle_matches_canonical_contract_shape(tmp_path: Path):
    payload = _export_fixture_payload(tmp_path)

    assert set(payload) == {
        "package_metadata",
        "sources",
        "study_context",
        "metadata_assertions",
        "evidence_assertions",
        "mapping_assertions",
        "graph_assertions",
        "review_notes",
    }
    _assert_exact_keys(
        payload["package_metadata"],
        {"package_id", "created_at", "created_by", "contract_name", "contract_version", "domain_type"},
    )
    assert payload["package_metadata"]["contract_name"] == "MAPPPP"
    assert payload["package_metadata"]["contract_version"] == "0.1.0"
    assert payload["package_metadata"]["domain_type"] == "hcm"

    _assert_exact_keys(payload["study_context"], {"species"})
    assert payload["study_context"]["species"] == "mouse"

    assert payload["metadata_assertions"]
    assert payload["evidence_assertions"]
    assert payload["mapping_assertions"]
    assert payload["graph_assertions"]
    assert payload["review_notes"]

    for item in payload["metadata_assertions"]:
        _assert_exact_keys(item, {"assertion_id", "status", "epistemic_label", "provenance", "field", "value"})
        assert item["status"] == "proposed"
        assert item["epistemic_label"] in {"reported", "extracted", "inferred", "normalized", "uncertain"}
        _assert_provenance_shape(item)
    for item in payload["evidence_assertions"]:
        _assert_exact_keys(item, {"assertion_id", "status", "epistemic_label", "provenance", "subject", "observation", "value"})
        assert item["status"] == "proposed"
        assert item["epistemic_label"] in {"reported", "extracted", "inferred", "normalized", "uncertain"}
        _assert_provenance_shape(item)
    for item in payload["mapping_assertions"]:
        _assert_exact_keys(
            item,
            {"assertion_id", "status", "epistemic_label", "provenance", "local_field", "target_schema", "target_field", "mapping_rationale", "confidence"},
        )
        assert item["target_schema"] == "MNMS"
        assert isinstance(item["confidence"], float)
        assert item["epistemic_label"] in {"reported", "extracted", "inferred", "normalized", "uncertain"}
        _assert_provenance_shape(item)
    for item in payload["graph_assertions"]:
        assert set(item).issubset({"assertion_id", "status", "epistemic_label", "provenance", "subject", "predicate", "object", "object_type"})
        assert {"assertion_id", "status", "epistemic_label", "provenance", "subject", "predicate", "object"}.issubset(item)
        assert item["epistemic_label"] in {"reported", "extracted", "inferred", "normalized", "uncertain"}
        _assert_provenance_shape(item)
    for item in payload["review_notes"]:
        _assert_exact_keys(item, {"note_id", "disposition", "note", "provenance", "related_assertion_ids"})
        assert item["disposition"] in {"flag", "comment", "request_changes"}
        assert isinstance(item["related_assertion_ids"], list)
        _assert_provenance_shape(item)

    legacy_keys = _collect_keys(payload)
    for legacy_key in (
        "schema_name",
        "schema_version",
        "source_package_metadata",
        "exported_at",
        "exporter",
        "swarm_name",
        "swarm_description",
        "swarm_output_dir",
        "domain",
        "standards",
        "output_sections",
        "inferred_from",
        "statement",
        "assertion_type",
        "persona",
        "in_text_citations",
        "notes",
        "mapping_id",
        "metadata_field",
        "value_or_status",
        "mnms_category",
        "classification",
        "evidence_level",
        "note_type",
    ):
        assert legacy_key not in legacy_keys


def test_export_hcm_mapppp_bundle_matches_canonical_example_family(tmp_path: Path):
    payload = _export_fixture_payload(tmp_path)

    expected_family = {
        "package_metadata": {
            "package_id": str,
            "created_at": str,
            "created_by": str,
            "contract_name": "MAPPPP",
            "contract_version": "0.1.0",
            "domain_type": "hcm",
        },
        "study_context": {"species": "mouse"},
        "metadata_assertions": list,
        "evidence_assertions": list,
        "mapping_assertions": list,
        "graph_assertions": list,
        "review_notes": list,
    }
    assert payload["package_metadata"]["contract_name"] == expected_family["package_metadata"]["contract_name"]
    assert payload["package_metadata"]["contract_version"] == expected_family["package_metadata"]["contract_version"]
    assert payload["package_metadata"]["domain_type"] == expected_family["package_metadata"]["domain_type"]
    assert payload["study_context"] == expected_family["study_context"]
    assert isinstance(payload["metadata_assertions"], expected_family["metadata_assertions"])
    assert isinstance(payload["evidence_assertions"], expected_family["evidence_assertions"])
    assert isinstance(payload["mapping_assertions"], expected_family["mapping_assertions"])
    assert isinstance(payload["graph_assertions"], expected_family["graph_assertions"])
    assert isinstance(payload["review_notes"], expected_family["review_notes"])


def test_export_hcm_mapppp_bundle_matches_validator_compatibility_expectations(tmp_path: Path):
    payload = _export_fixture_payload(tmp_path)

    assert payload["package_metadata"]["domain_type"] in {"generic", "hcm"}
    allowed_epistemic_labels = {"reported", "extracted", "inferred", "normalized", "uncertain"}
    for family in (
        payload["metadata_assertions"],
        payload["evidence_assertions"],
        payload["mapping_assertions"],
        payload["graph_assertions"],
    ):
        for item in family:
            assert item["epistemic_label"] in allowed_epistemic_labels
            assert set(item["provenance"]) == {"source_id", "source_anchor", "proposed_by", "created_at"}
            assert "source_anchors" not in item["provenance"]
    for item in payload["review_notes"]:
        assert set(item["provenance"]) == {"source_id", "source_anchor", "proposed_by", "created_at"}
        assert "source_anchors" not in item["provenance"]


def test_export_hcm_mapppp_bundle_matches_checked_in_pilot_baseline_fixture(tmp_path: Path):
    payload = _export_fixture_payload(tmp_path)
    fixture_path = Path(__file__).parent / "fixtures" / "hcm_mapppp_pilot_baseline_v0_1_0.json"
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))
    # Frozen donor content is unchanged; identity and anchors are deliberately strengthened.
    for item in (payload, expected):
        item.pop("sources", None)
        item["package_metadata"].pop("package_id", None)
        for family in ("metadata_assertions", "evidence_assertions", "mapping_assertions",
                       "graph_assertions", "review_notes"):
            for assertion in item[family]:
                assertion["provenance"].pop("source_id", None)
                assertion["provenance"].pop("source_anchor", None)
    assert payload == expected


def test_export_hcm_mapppp_bundle_raises_for_missing_explicit_review_notes_path(tmp_path: Path):
    config_path, matrix_path, draft_path, _ = _write_hcm_fixture_files(tmp_path)

    missing_notes_path = tmp_path / "missing-review-notes.json"

    try:
        export_hcm_mapppp_bundle(
            config_path=config_path,
            draft_path=draft_path,
            traceability_matrix_path=matrix_path,
            review_notes_path=missing_notes_path,
        )
    except FileNotFoundError as exc:
        assert str(missing_notes_path) in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError for missing explicit review notes path")


def test_export_mapppp_hcm_cli_writes_canonical_bundle_json(tmp_path: Path):
    config_path, matrix_path, draft_path, notes_path = _write_hcm_fixture_files(tmp_path)
    output_path = tmp_path / "mapppp_bundle.json"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "export",
            "mapppp-hcm",
            "--config-path",
            str(config_path),
            "--draft-path",
            str(draft_path),
            "--traceability-matrix",
            str(matrix_path),
            "--review-notes-path",
            str(notes_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["package_metadata"]["contract_name"] == "MAPPPP"
    assert payload["package_metadata"]["contract_version"] == "0.1.0"
    assert payload["package_metadata"]["domain_type"] == "hcm"
    assert "source_package_metadata" not in payload
    assert "schema_name" not in payload
    assert "schema_version" not in payload
    assert payload["review_notes"][-1]["disposition"] == "request_changes"
    assert set(payload["metadata_assertions"][0]["provenance"]) == {"source_id", "source_anchor", "proposed_by", "created_at"}
    assert "source_anchors" not in payload["metadata_assertions"][0]["provenance"]
