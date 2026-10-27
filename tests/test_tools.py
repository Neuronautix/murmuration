"""
Tests for automation/tools.py

All external API calls are mocked — these tests do not require API keys.
"""

from unittest.mock import MagicMock, patch


def test_search_pubmed_returns_string():
    """search_pubmed should return a string even when the API returns empty results."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = """<?xml version="1.0"?>
    <PubmedArticleSet></PubmedArticleSet>"""

    with patch("requests.get", return_value=mock_response):
        from automation.tools import search_pubmed
        result = search_pubmed.invoke({"query": "test query", "max_results": 3})
        assert isinstance(result, str)


def test_lookup_doi_returns_string():
    """lookup_doi should return a string for any input."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "message": {
            "title": ["Test Paper"],
            "author": [{"given": "Jane", "family": "Smith"}],
            "published-print": {"date-parts": [[2024]]},
            "container-title": ["Test Journal"],
            "DOI": "10.1234/test",
            "abstract": "Test abstract."
        }
    }

    with patch("requests.get", return_value=mock_response):
        from automation.tools import lookup_doi
        result = lookup_doi.invoke({"query": "10.1234/test"})
        assert isinstance(result, str)


def test_append_traceability_matrix_returns_string(tmp_path):
    """append_traceability_matrix always returns a string (success or failure message)."""
    from automation.tools import append_traceability_matrix

    # The tool resolves the matrix path relative to tools.py, so patch the
    # file existence check and file read to simulate a properly initialised matrix.
    matrix_content = (
        "# Knowledge Traceability Matrix\n\n"
        "| Source | Author/Agent | Claim | Method | Epistemic Tag |\n"
        "|--------|-------------|-------|--------|---------------|\n"
    )
    with patch("automation.tools.os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock(return_value=MagicMock(
             __enter__=MagicMock(return_value=MagicMock(
                 read=MagicMock(return_value=matrix_content),
                 write=MagicMock()
             )),
             __exit__=MagicMock(return_value=False)
         ))), \
         patch("pathlib.Path.read_text", return_value=matrix_content):
        result = append_traceability_matrix.invoke({
            "fact": "Test claim about internet use.",
            "source": "PubMed PMID:12345",
            "epistemic_tag": "[FACT]",
        })
    assert isinstance(result, str)


def _write_config_with_paths(tmp_path):
    return {
        "swarm": {
            "output_dir": str(tmp_path / "Drafts" / "Pack"),
            "traceability_matrix": str(tmp_path / "Matrix_Pack.md"),
        }
    }


def test_write_manuscript_section_honors_configured_output_dir(tmp_path):
    """write_manuscript_section writes into swarm.output_dir, not the root Drafts/."""
    from automation.tools import write_manuscript_section

    with patch("automation.config.load_config", return_value=_write_config_with_paths(tmp_path)):
        result = write_manuscript_section.invoke({"section_name": "Intro", "content": "Hello"})

    assert result.startswith("SUCCESS")
    assert (tmp_path / "Drafts" / "Pack" / "intro.md").read_text(encoding="utf-8") == "Hello"


def test_append_traceability_matrix_honors_configured_path(tmp_path):
    """append_traceability_matrix appends to swarm.traceability_matrix."""
    from automation.tools import append_traceability_matrix

    matrix = tmp_path / "Matrix_Pack.md"
    matrix.write_text(
        "| Source | Author/Agent | Claim | Method | Epistemic Tag |\n"
        "|--------|-------------|-------|--------|---------------|\n",
        encoding="utf-8",
    )
    with patch("automation.config.load_config", return_value=_write_config_with_paths(tmp_path)):
        result = append_traceability_matrix.invoke({
            "fact": "Pack-specific claim.",
            "source": "PMID:1",
            "epistemic_tag": "[FACT]",
        })

    assert result.startswith("SUCCESS")
    assert "Pack-specific claim." in matrix.read_text(encoding="utf-8")


def test_resolve_swarm_path_relative_to_project_root():
    """Relative paths resolve against the project root; missing keys use defaults."""
    from automation.config import PROJECT_ROOT, resolve_swarm_path

    cfg = {"swarm": {"output_dir": "./Drafts/TOCSAS"}}
    assert resolve_swarm_path(cfg, "output_dir") == PROJECT_ROOT / "Drafts" / "TOCSAS"
    assert resolve_swarm_path(cfg, "traceability_matrix") == PROJECT_ROOT / "Knowledge_Traceability_Matrix.md"


def test_git_commit_snapshot_stages_only_existing_in_repo_paths(tmp_path):
    """git_commit_snapshot skips missing or out-of-repo paths and force-adds the rest."""
    from automation.config import PROJECT_ROOT
    from automation.tools import git_commit_snapshot

    paths = {
        "output_dir": PROJECT_ROOT / "Drafts",  # tracked via .gitkeep, always exists
        "traceability_matrix": tmp_path / "outside_repo.md",
    }
    paths["traceability_matrix"].write_text("x", encoding="utf-8")
    run = MagicMock(return_value=MagicMock(stdout=""))
    with patch("automation.config.resolve_swarm_path", side_effect=lambda _cfg, key: paths[key]), \
         patch("automation.tools.subprocess.run", run):
        result = git_commit_snapshot.invoke({"message": "test"})

    assert run.call_args_list[0].args[0] == ["git", "add", "--force", "--", "Drafts"]
    assert result == "No changes to commit in swarm output paths."
