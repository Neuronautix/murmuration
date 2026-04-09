"""
Tests for automation/tools.py

All external API calls are mocked — these tests do not require API keys.
"""

import pytest
from unittest.mock import patch, MagicMock


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
