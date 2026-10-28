import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from automation.exporters.oratlas import (
    SCHEMA,
    _citation,
    _deduplicate,
    _merge_citation,
    canonical,
    check_export,
    export_oratlas,
    main,
    normalize_doi,
    safe_path,
    schema_with_ai_synthesis_compatibility_override,
)

FIXTURE = Path(__file__).parent / "fixtures" / "oratlas"
OPTIONS = dict(repository_url="https://github.com/dhuzard/murmuration-oratlas-export-test",
               title="HCM circadian mouse pilot",
               abstract="Source-linked abstract extraction from the fixed five-PMID HCM circadian mouse pilot corpus. Abstract-level proposals require full-method review.",
               license="MIT", keywords=["hcm", "circadian"])


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def export(tmp_path):
    result = export_oratlas(FIXTURE / "hcm-run", tmp_path, **OPTIONS)
    assert result["counts"] == {"claims": 58, "citations": 5, "relations": 58}
    return result


def test_real_hcm_golden(tmp_path):
    result = export(tmp_path)
    assert snapshot(tmp_path) == snapshot(FIXTURE / "golden")
    assert len(result["skipped"]) == 58
    assert {s["record"] for s in result["skipped"]} >= {"HCM-001", "HCM-103", "HCM-116"}
    assert "trustAssessments" not in (tmp_path / "review-manifest.json").read_text()


def test_determinism(tmp_path):
    export(tmp_path / "first")
    export(tmp_path / "second")
    assert snapshot(tmp_path / "first") == snapshot(tmp_path / "second")
    for name, data in snapshot(tmp_path / "first").items():
        assert data.endswith(b"\n") and b"\r" not in data
        data.decode("utf-8")
        if name.endswith(".jsonl"):
            assert all(canonical(json.loads(line)) == line for line in data.decode().splitlines())


@pytest.mark.parametrize("path", ["/abs", "../outside", "a/../b", "a\\b", "https://host/a", "C:/abs", "a//b", "a/./b", "a\n", "~user/a"])
def test_unsafe_paths(tmp_path, path):
    with pytest.raises(ValueError, match="Unsafe"):
        safe_path(tmp_path, path)


@pytest.mark.parametrize("value", ["doi:10.1038/S41598-025-87530-6", "https://doi.org/10.1038/S41598-025-87530-6", "HTTP://DX.DOI.ORG/10.1038/S41598-025-87530-6"])
def test_doi_normalization(value):
    assert normalize_doi(value) == "10.1038/s41598-025-87530-6"


def test_no_example_dois():
    with pytest.raises(ValueError, match="Example DOI"):
        normalize_doi("https://doi.org/10.5555/example")


def test_transitive_citation_deduplication():
    citations, ids = _deduplicate([{"doi": "10.1038/s41598-025-87530-6"}, {"pmid": "39880968"},
                                 {"doi": "10.1038/s41598-025-87530-6", "pmid": "39880968"}])
    assert len(citations) == 1 and len(set(ids)) == 1
    with pytest.raises(ValueError, match="Conflicting"):
        _deduplicate([{"doi": "10.1038/a", "pmid": "1"}, {"doi": "10.1038/b", "pmid": "1"}])
    with pytest.raises(ValueError, match="Conflicting"):
        _merge_citation({"doi": "10.1038/a", "pmid": "1"}, {"doi": "10.1038/b", "pmid": "1"})


def test_dangling_relation_and_cli_failure(tmp_path, capsys):
    export(tmp_path)
    path = tmp_path / "knowledge/oratlas/relations.jsonl"
    relation = json.loads(path.read_text().splitlines()[0])
    relation["claimId"] = "missing"
    path.write_bytes((canonical(relation) + "\n").encode())
    with pytest.raises(ValueError, match="Dangling"):
        check_export(tmp_path)
    assert main(["--check", "--out", str(tmp_path)]) == 1
    assert "Dangling" in capsys.readouterr().err


@pytest.mark.parametrize("mutation", ["unsafe", "enum", "duplicate", "encoding", "missing"])
def test_check_rejects_bad_output(tmp_path, mutation):
    export(tmp_path)
    manifest_path = tmp_path / "review-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    claims = tmp_path / manifest["artifacts"]["claims"]
    if mutation == "unsafe":
        manifest["artifacts"]["claims"] = "../secret.jsonl"
        manifest_path.write_bytes((canonical(manifest) + "\n").encode())
    elif mutation == "enum":
        record = json.loads(claims.read_text().splitlines()[0])
        record["claimType"] = "invented"
        claims.write_bytes((canonical(record) + "\n").encode())
    elif mutation == "duplicate":
        claims.write_bytes(claims.read_bytes() * 2)
    elif mutation == "encoding":
        claims.write_bytes(claims.read_bytes().replace(b"\n", b"\r\n"))
    else:
        claims.unlink()
    with pytest.raises((ValueError, ValidationError, OSError)):
        check_export(tmp_path)


def test_override_is_exactly_one_enum_addition():
    upstream = json.loads(SCHEMA.read_text())
    expected = json.loads(SCHEMA.read_text())
    expected["properties"]["review"]["properties"]["reviewType"]["enum"].append("ai-synthesis")
    assert schema_with_ai_synthesis_compatibility_override() == expected
    assert json.loads(SCHEMA.read_text()) == upstream


def test_stable_claim_ids_when_matrix_order_changes(tmp_path):
    export(tmp_path / "first")
    matrix = (FIXTURE / "hcm-run/traceability.md").read_text(encoding="utf-8")
    lines = matrix.splitlines()
    reordered = tmp_path / "reordered.md"
    start = next(i for i, line in enumerate(lines) if line.startswith("|---")) + 1
    reordered.write_text("\n".join(lines[:start] + list(reversed(lines[start:]))) + "\n", encoding="utf-8")
    export_oratlas(FIXTURE / "hcm-run", tmp_path / "second", matrix=reordered, **OPTIONS)
    for family in ("claims", "citations", "relations"):
        path = f"knowledge/oratlas/{family}.jsonl"
        assert (tmp_path / "first" / path).read_bytes() == (tmp_path / "second" / path).read_bytes()


def test_version_and_concept_doi_are_separate(tmp_path):
    export_oratlas(FIXTURE / "hcm-run", tmp_path, version_doi="doi:10.5281/zenodo.123",
                   concept_doi="https://doi.org/10.5281/zenodo.456", **OPTIONS)
    manifest = json.loads((tmp_path / "review-manifest.json").read_text())
    assert manifest["publication"] == {"versionDoi": "10.5281/zenodo.123", "conceptDoi": "10.5281/zenodo.456"}


def test_legacy_live_search_matrix_and_qualification(tmp_path):
    (tmp_path / "final_report.md").write_text("## Results\n[INFERENCE] The result may generalize [1].\n\n## References\n[1] PMID:39880968\n")
    matrix = tmp_path / "traceability.md"
    matrix.write_text("| Source | Agent | Claim | Method | Epistemic Tag |\n|---|---|---|---|---|\n"
                      "| *Live Search:* PMID:39880968 | **Scout** | The result may generalize | Search | **[INFERENCE]** |\n")
    result = export_oratlas(tmp_path, tmp_path / "out", matrix=matrix, **OPTIONS)
    assert result["counts"] == {"claims": 1, "citations": 1, "relations": 1}
    claim = json.loads((tmp_path / "out/knowledge/oratlas/claims.jsonl").read_text())
    assert "may" in claim["text"] and "[INFERENCE]" in claim["qualification"]


@pytest.mark.parametrize("source", ["[1]", "doi:10.1038/example", "https://doi.org/10.1038/example"])
@pytest.mark.parametrize("doi", ["10.1038/example", "10.1038/example(part)"])
def test_markdown_doi_reference_preserves_suffix_parentheses(tmp_path, source, doi):
    source = source.replace("10.1038/example", doi)
    (tmp_path / "final_report.md").write_text(
        f"## Results\nThe result may generalize [1].\n\n## References\n[1] [Paper](https://doi.org/{doi})\n",
        encoding="utf-8",
    )
    (tmp_path / "traceability.md").write_text(
        "| claim | source |\n|---|---|\n" + f"| The result may generalize | {source} |\n",
        encoding="utf-8",
    )
    result = export_oratlas(tmp_path, tmp_path / "out", **OPTIONS)
    assert result == {"counts": {"claims": 1, "citations": 1, "relations": 1}, "skipped": []}
    citation = json.loads((tmp_path / "out/knowledge/oratlas/citations.jsonl").read_text())
    assert citation["doi"] == doi


@pytest.mark.parametrize("sources", [
    ["PMID:123", "PMID:456"],
    ["doi:10.1038/first", "doi:10.1038/second(part)"],
    ["[First](https://doi.org/10.1038/first)", "[Second](https://doi.org/10.1038/second(part))"],
    ["https://example.org/first", "https://example.org/second"],
    ["doi:10.1038/first", "PMID:456"],
])
def test_report_can_cite_multiple_works_with_separate_matrix_rows(tmp_path, sources):
    (tmp_path / "final_report.md").write_text(
        "## Results\n| claim | sources |\n|---|---|\n"
        + f"| The result may generalize | {' '.join(sources)} |\n", encoding="utf-8",
    )
    (tmp_path / "traceability.md").write_text(
        "| claim | source |\n|---|---|\n"
        + "".join(f"| The result may generalize | {source} |\n" for source in sources), encoding="utf-8",
    )
    result = export_oratlas(tmp_path, tmp_path / "out", **OPTIONS)
    assert result == {"counts": {"claims": 1, "citations": 2, "relations": 2}, "skipped": []}
    assert check_export(tmp_path / "out") == result["counts"]


@pytest.mark.parametrize("source", [
    "[Paper](https://doi.org/10.1038/example(outer(inner)))",
    '[Paper](<https://doi.org/10.1038/example(outer(inner))> "Title")',
    r"[Paper](https://doi.org/10.1038/example\(outer\(inner\)\))",
    "doi:10.1038/example(outer(inner))",
])
def test_doi_suffix_parentheses_are_not_stripped(source):
    assert _citation(source) == {"doi": "10.1038/example(outer(inner))"}
