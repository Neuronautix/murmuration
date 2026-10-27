import json
from datetime import datetime, timezone

import pytest

from automation.exporters.mapppp import export_hcm_mapppp_bundle
from tests.test_mapppp_export import _write_hcm_fixture_files


def test_repeated_export_uses_persistent_sources_and_identical_payload(tmp_path):
    config, matrix, draft, notes = _write_hcm_fixture_files(tmp_path)
    kwargs = dict(config_path=config, draft_path=draft, traceability_matrix_path=matrix,
                  review_notes_path=notes)
    first = export_hcm_mapppp_bundle(**kwargs, exported_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
    second = export_hcm_mapppp_bundle(**kwargs, exported_at=datetime(2026, 4, 11, tzinfo=timezone.utc))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.package_metadata.package_id.startswith("hcmsas-mapppp-sha256-")
    registry = json.loads((draft.parent / "source_registry.json").read_text(encoding="utf-8"))
    ids = set(registry["sources"])
    assert ids
    assert all(not source_id.startswith("[") for source_id in ids)
    assert all(item.provenance.source_id in ids for family in (
        first.metadata_assertions, first.evidence_assertions, first.mapping_assertions,
        first.graph_assertions, first.review_notes) for item in family)


def test_distinct_exports_in_same_second_get_distinct_ids(tmp_path):
    config, matrix, draft, notes = _write_hcm_fixture_files(tmp_path)
    kwargs = dict(config_path=config, draft_path=draft, traceability_matrix_path=matrix,
                  review_notes_path=notes, exported_at=datetime(2026, 4, 10, tzinfo=timezone.utc))
    first = export_hcm_mapppp_bundle(**kwargs)
    draft.write_text(draft.read_text(encoding="utf-8").replace(
        "Light cycle transition timing was not reported", "Light cycle and temperature were not reported"),
        encoding="utf-8")
    second = export_hcm_mapppp_bundle(**kwargs)
    assert first.package_metadata.package_id != second.package_metadata.package_id


def test_unresolved_citation_fails_closed(tmp_path):
    config, matrix, draft, notes = _write_hcm_fixture_files(tmp_path)
    draft.write_text(draft.read_text(encoding="utf-8").replace(
        "Light cycle transition timing was not reported [1]",
        "Light cycle transition timing was not reported [99]"), encoding="utf-8")
    with pytest.raises(ValueError, match="Unresolved citation reference"):
        export_hcm_mapppp_bundle(config, draft, matrix, notes)
