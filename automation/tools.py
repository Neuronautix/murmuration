"""
tools.py — Tool Functions for the Research Swarm

Each function decorated with @tool becomes available to the LangGraph agents.
Tools are registered in swarm_config.yml and dynamically loaded by config.py.

To add a custom tool:
  1. Create a function decorated with @tool in this file (or a new module)
  2. Register it in swarm_config.yml under the 'tools:' section
  3. Assign it to one or more persona's tool lists

Built-in Tools:
  - search_pubmed              : Search PubMed and return full title + abstract
  - search_semantic_scholar    : Search Semantic Scholar (indexes preprints + journals)
    - trace_literature_network   : Expand from a seed paper via references + lead authors
  - check_schema_org           : Validate terms against Schema.org vocabulary
  - write_manuscript_section   : Write a Markdown section to the Drafts/ directory
  - search_you_engine          : Search the live web via You.com API
  - append_traceability_matrix : Log a fact to the Knowledge Traceability Matrix
  - search_knowledge_base      : Search local vectorized KB documents
  - scrape_webpage             : Fetch full text content from a URL
  - git_commit_snapshot        : Auto-commit output files for version-controlled audit trail
"""

import os
import re
import subprocess
from pathlib import Path
import requests
from langchain_core.tools import tool




# ═══════════════════════════════════════════════════════
# RESEARCH TOOLS (Domain-Specific — swap these per domain)
# ═══════════════════════════════════════════════════════

SEMANTIC_SCHOLAR_HEADERS = {
    "User-Agent": "ResearchSwarm/1.0 (Academic Research)",
}

TOPIC_STOPWORDS = {
    "about", "among", "analysis", "article", "articles", "association", "associations",
    "between", "effect", "effects", "from", "have", "into", "paper", "papers",
    "research", "review", "study", "studies", "than", "that", "their", "there",
    "these", "this", "those", "under", "used", "using", "were", "where", "while",
    "which", "with", "without",
}


def _semantic_scholar_get(endpoint: str, params: dict) -> dict:
    resp = requests.get(
        f"https://api.semanticscholar.org{endpoint}",
        params=params,
        headers=SEMANTIC_SCHOLAR_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _normalise_topic_tokens(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {
        token for token in tokens
        if len(token) >= 4 and token not in TOPIC_STOPWORDS and not token.isdigit()
    }


def _format_authors(authors: list, limit: int = 3) -> str:
    names = [author.get("name", "") for author in authors[:limit] if author.get("name")]
    if not names:
        return "Unknown authors"
    if len(authors) > limit:
        names.append("et al.")
    return ", ".join(names)


def _paper_identifier(paper: dict) -> str:
    ext_ids = paper.get("externalIds", {}) or {}
    doi = ext_ids.get("DOI", "")
    if doi:
        return f"DOI: {doi}"
    paper_id = paper.get("paperId", "")
    return f"Semantic Scholar ID: {paper_id}" if paper_id else "Identifier unavailable"


def _paper_url(paper: dict) -> str:
    url = paper.get("url", "")
    if url:
        return url
    doi = (paper.get("externalIds", {}) or {}).get("DOI", "")
    return f"https://doi.org/{doi}" if doi else ""


def _paper_relevance_score(paper: dict, topic_tokens: set[str]) -> int:
    title_tokens = _normalise_topic_tokens(paper.get("title", ""))
    overlap = len(topic_tokens & title_tokens)
    citation_count = _safe_int(paper.get("citationCount"))
    return overlap * 1000 + citation_count


def _select_seed_paper(candidates: list[dict], seed_query: str) -> dict | None:
    if not candidates:
        return None

    clean_query = seed_query.strip().lower().removeprefix("https://doi.org/").removeprefix("doi.org/")
    if clean_query.startswith("10."):
        for paper in candidates:
            doi = ((paper.get("externalIds", {}) or {}).get("DOI", "")).lower()
            if doi == clean_query:
                return paper

    query_tokens = _normalise_topic_tokens(seed_query)
    ranked = sorted(
        candidates,
        key=lambda paper: (
            _paper_relevance_score(paper, query_tokens),
            _safe_int(paper.get("year")),
        ),
        reverse=True,
    )
    return ranked[0]


def _author_paper_items(payload: dict) -> list[dict]:
    papers = []
    for item in payload.get("data", []):
        if isinstance(item, dict) and "paper" in item and isinstance(item["paper"], dict):
            papers.append(item["paper"])
        elif isinstance(item, dict):
            papers.append(item)
    return papers


@tool
def search_pubmed(query: str, max_results: int = 3) -> str:
    """Searches PubMed for a given query and returns title, journal, PMID,
    and full abstract for each result. Use this for peer-reviewed biomedical
    literature. If results are empty or abstracts are missing, follow up with
    search_semantic_scholar or search_web for preprints and recent papers."""
    import xml.etree.ElementTree as ET

    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    try:
        search_resp = requests.get(
            base_url,
            params={
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": max_results,
            },
        )
        search_resp.raise_for_status()
        id_list = search_resp.json().get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return (
                "No results found on PubMed for this query. "
                "Try search_semantic_scholar or search_web for preprints and 2024–2026 papers."
            )

        # Fetch full records including abstracts via efetch
        fetch_resp = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": ",".join(id_list),
                "rettype": "abstract",
                "retmode": "xml",
            },
        )
        fetch_resp.raise_for_status()

        root = ET.fromstring(fetch_resp.text)
        output = []

        for article in root.findall(".//PubmedArticle"):
            citation = article.find("MedlineCitation")
            if citation is None:
                continue

            pmid_el = citation.find("PMID")
            pmid = pmid_el.text if pmid_el is not None else "Unknown"

            art = citation.find("Article")
            if art is None:
                continue

            title_el = art.find("ArticleTitle")
            title = "".join(title_el.itertext()) if title_el is not None else "No title"

            journal_el = art.find("Journal/Title")
            journal = journal_el.text if journal_el is not None else "Unknown journal"

            year_el = art.find(".//PubDate/Year")
            year = year_el.text if year_el is not None else ""

            # AbstractText may be a single element or multiple labelled sections
            abstract_parts = []
            for ab in art.findall(".//AbstractText"):
                label = ab.get("Label")
                text = "".join(ab.itertext()).strip()
                if label:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
            abstract = " ".join(abstract_parts) if abstract_parts else "No abstract available."

            output.append(
                f"Title: {title}\n"
                f"Journal: {journal} ({year})\n"
                f"PMID: {pmid}\n"
                f"Abstract: {abstract}"
            )

        if not output:
            return (
                "PubMed returned records but abstracts could not be parsed. "
                "Try search_semantic_scholar for this query."
            )

        return "\n\n---\n\n".join(output)

    except Exception as e:
        return f"Error querying PubMed: {e}"


@tool
def search_semantic_scholar(query: str, max_results: int = 5) -> str:
    """Searches Semantic Scholar for academic papers including preprints, conference
    papers, and journal articles. Unlike PubMed, Semantic Scholar indexes arXiv,
    bioRxiv, CHI, and recent 2024–2026 papers that may not yet appear on PubMed.

    Use this when search_pubmed returns no results, or when you need preprints,
    AI/HCI conference papers, or very recent publications.

    Args:
        query: Search terms (e.g., 'AI chatbot dependence problematic use 2025').
        max_results: Number of results to return (default 5, max 10).
    """
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": min(max_results, 10),
        "fields": "title,abstract,year,authors,externalIds,venue,url",
    }
    headers = {"User-Agent": "ResearchSwarm/1.0 (Academic Research)"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        papers = data.get("data", [])
        if not papers:
            return (
                f"No results found on Semantic Scholar for: '{query}'. "
                "Try search_web or a shorter query."
            )

        output = []
        for paper in papers:
            title = paper.get("title", "No title")
            year = paper.get("year", "n.d.")
            venue = paper.get("venue", "")
            authors = ", ".join(
                a.get("name", "") for a in paper.get("authors", [])[:3]
            )
            if len(paper.get("authors", [])) > 3:
                authors += " et al."

            ext_ids = paper.get("externalIds", {})
            doi = ext_ids.get("DOI", "")
            arxiv = ext_ids.get("ArXiv", "")
            s2_url = paper.get("url", "")

            abstract = paper.get("abstract") or "No abstract available."
            # Cap abstract at 600 chars to keep output manageable
            if len(abstract) > 600:
                abstract = abstract[:600] + "…"

            id_line = " | ".join(filter(None, [
                f"DOI: {doi}" if doi else "",
                f"arXiv: {arxiv}" if arxiv else "",
                s2_url,
            ]))

            output.append(
                f"Title: {title}\n"
                f"Authors: {authors}\n"
                f"Year: {year} | Venue: {venue}\n"
                f"IDs: {id_line}\n"
                f"Abstract: {abstract}"
            )

        return "\n\n---\n\n".join(output)

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            return (
                "Semantic Scholar rate limit hit. Wait a moment and retry, "
                "or use search_web as an alternative."
            )
        return f"Semantic Scholar HTTP error: {e}"
    except Exception as e:
        return f"Error querying Semantic Scholar: {e}"


@tool
def trace_literature_network(
    seed_query: str,
    topic_query: str = "",
    max_references: int = 8,
    max_author_papers: int = 6,
) -> str:
    """Resolve a sentinel paper, inspect its cited references, and expand via lead authors.

    Use this after you identify a major review, meta-analysis, or high-impact article.
    The tool returns two expansion paths: cited references from the seed paper,
    and adjacent papers by the seed paper's first and last authors.
    """
    try:
        search_payload = _semantic_scholar_get(
            "/graph/v1/paper/search",
            {
                "query": seed_query,
                "limit": 5,
                "fields": "paperId,title,year,venue,authors,abstract,externalIds,url,citationCount",
            },
        )
        seed_paper = _select_seed_paper(search_payload.get("data", []), seed_query)
        if not seed_paper:
            return (
                f"No Semantic Scholar seed paper found for '{seed_query}'. "
                "Try a DOI, a more exact title, or a narrower query."
            )

        seed_detail = _semantic_scholar_get(
            f"/graph/v1/paper/{seed_paper['paperId']}",
            {
                "fields": (
                    "paperId,title,year,venue,authors,abstract,externalIds,url,"
                    "citationCount,referenceCount,"
                    "references.paperId,references.title,references.year,references.venue,"
                    "references.authors,references.externalIds,references.url,references.citationCount"
                )
            },
        )

        topic_basis = topic_query or seed_detail.get("title", "") or seed_query
        topic_tokens = _normalise_topic_tokens(topic_basis)
        seed_title = seed_detail.get("title", "No title")
        seed_authors = seed_detail.get("authors", [])

        references = [
            ref for ref in seed_detail.get("references", [])
            if isinstance(ref, dict) and ref.get("title")
        ]
        ranked_references = sorted(
            references,
            key=lambda ref: (_paper_relevance_score(ref, topic_tokens), _safe_int(ref.get("year"))),
            reverse=True,
        )
        top_references = ranked_references[:max(1, max_references)]

        if topic_tokens and top_references:
            any_topic_overlap = any(
                _normalise_topic_tokens(ref.get("title", "")) & topic_tokens
                for ref in top_references
            )
            if not any_topic_overlap:
                top_references = sorted(
                    references,
                    key=lambda ref: (_safe_int(ref.get("citationCount")), _safe_int(ref.get("year"))),
                    reverse=True,
                )[:max(1, max_references)]

        lead_authors = []
        if seed_authors:
            lead_authors.append(seed_authors[0])
            if len(seed_authors) > 1:
                lead_authors.append(seed_authors[-1])

        unique_leads = []
        seen_author_ids = set()
        for author in lead_authors:
            author_id = author.get("authorId")
            if not author_id or author_id in seen_author_ids:
                continue
            seen_author_ids.add(author_id)
            unique_leads.append(author)

        author_candidates = []
        seen_papers = {seed_detail.get("paperId", "")}
        seen_titles = {seed_title.strip().lower()}
        for ref in top_references:
            if ref.get("paperId"):
                seen_papers.add(ref["paperId"])
            seen_titles.add(ref.get("title", "").strip().lower())

        for author in unique_leads:
            author_name = author.get("name", "Unknown author")
            try:
                author_payload = _semantic_scholar_get(
                    f"/graph/v1/author/{author['authorId']}/papers",
                    {
                        "limit": min(max_author_papers * 4, 20),
                        "fields": "paperId,title,year,venue,authors,externalIds,url,citationCount",
                    },
                )
            except requests.exceptions.HTTPError as e:
                author_candidates.append({
                    "author_name": author_name,
                    "error": f"Semantic Scholar HTTP error while expanding author trail: {e}",
                })
                continue

            for paper in _author_paper_items(author_payload):
                paper_id = paper.get("paperId", "")
                title = paper.get("title", "").strip()
                title_key = title.lower()
                if not title or paper_id in seen_papers or title_key in seen_titles:
                    continue

                overlap = len(topic_tokens & _normalise_topic_tokens(title))
                citation_count = _safe_int(paper.get("citationCount"))
                if topic_tokens and overlap == 0 and citation_count < 20:
                    continue

                seen_papers.add(paper_id)
                seen_titles.add(title_key)
                author_candidates.append({
                    "author_name": author_name,
                    "paper": paper,
                    "score": overlap * 1000 + citation_count,
                })

        ranked_author_papers = sorted(
            [item for item in author_candidates if "paper" in item],
            key=lambda item: (item["score"], _safe_int(item["paper"].get("year"))),
            reverse=True,
        )[:max(1, max_author_papers)]

        reference_lines = []
        for index, ref in enumerate(top_references, 1):
            ref_url = _paper_url(ref)
            reference_lines.append(
                f"{index}. {ref.get('title', 'No title')}\n"
                f"   Authors: {_format_authors(ref.get('authors', []))}\n"
                f"   Year/Venue: {ref.get('year', 'n.d.')} | {ref.get('venue', '')}\n"
                f"   Citations: {_safe_int(ref.get('citationCount'))} | {_paper_identifier(ref)}\n"
                + (f"   URL: {ref_url}" if ref_url else "")
            )

        author_lines = []
        for index, item in enumerate(ranked_author_papers, 1):
            paper = item["paper"]
            paper_url = _paper_url(paper)
            author_lines.append(
                f"{index}. {paper.get('title', 'No title')}\n"
                f"   Via lead author: {item['author_name']}\n"
                f"   Authors: {_format_authors(paper.get('authors', []))}\n"
                f"   Year/Venue: {paper.get('year', 'n.d.')} | {paper.get('venue', '')}\n"
                f"   Citations: {_safe_int(paper.get('citationCount'))} | {_paper_identifier(paper)}\n"
                + (f"   URL: {paper_url}" if paper_url else "")
            )

        if not author_lines:
            author_lines.append(
                "No additional lead-author papers met the topic filter. "
                "Retry with a broader topic_query or inspect the seed article directly."
            )

        seed_url = _paper_url(seed_detail)
        return (
            f"Seed article chosen from Semantic Scholar:\n"
            f"Title: {seed_title}\n"
            f"Authors: {_format_authors(seed_authors)}\n"
            f"Year/Venue: {seed_detail.get('year', 'n.d.')} | {seed_detail.get('venue', '')}\n"
            f"Citations: {_safe_int(seed_detail.get('citationCount'))} | {_paper_identifier(seed_detail)}\n"
            + (f"URL: {seed_url}\n" if seed_url else "")
            + "\nReference trail from the seed article:\n"
            + "\n\n".join(reference_lines if reference_lines else ["No references were returned for the seed article."])
            + "\n\nLead-author expansion:\n"
            + "\n\n".join(author_lines)
            + "\n\nUse lookup_doi on the strongest candidates when you need richer citation metadata or DOI confirmation."
        )

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            return (
                "Semantic Scholar rate limit hit during literature-network tracing. "
                "Retry shortly, or fall back to search_pubmed + lookup_doi."
            )
        return f"Semantic Scholar HTTP error while tracing literature network: {e}"
    except Exception as e:
        return f"Error tracing literature network for '{seed_query}': {e}"


@tool
def check_schema_org(entity: str) -> str:
    """Checks if a given term exists natively in the Schema.org vocabulary.
    Pass abstract nouns like 'Dataset' or 'manufacturer'."""
    try:
        resp = requests.get(f"https://schema.org/{entity}")
        if resp.status_code == 200:
            return f"Entity '{entity}' exists in schema.org. It is structurally valid."
        else:
            return f"Entity '{entity}' NOT found natively in schema.org. Custom mapping required."
    except Exception as e:
        return f"Error contacting schema.org: {e}"


# ═══════════════════════════════════════════════════════
# I/O TOOLS (Domain-Agnostic)
# ═══════════════════════════════════════════════════════


@tool
def write_manuscript_section(section_name: str, content: str) -> str:
    """Writes a drafted section to the disk in the configured output directory.
    The file will be saved as a Markdown (.md) file.

    To prevent silent overwrites when the same section is revised multiple times
    in a run, a version counter suffix (_v2, _v3, …) is appended if a file with
    the base name already exists.
    """
    import datetime

    drafts_dir = os.path.join(os.path.dirname(__file__), "..", "Drafts")
    os.makedirs(drafts_dir, exist_ok=True)
    base_slug = section_name.lower().replace(" ", "_")

    # Version the file rather than silently overwriting
    filepath = os.path.join(drafts_dir, f"{base_slug}.md")
    if os.path.exists(filepath):
        version = 2
        while os.path.exists(os.path.join(drafts_dir, f"{base_slug}_v{version}.md")):
            version += 1
        filepath = os.path.join(drafts_dir, f"{base_slug}_v{version}.md")

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return f"SUCCESS: Wrote '{section_name}' to {filepath}"
    except Exception as e:
        return f"FAILED to write file: {e}"


_MATRIX_HEADER_SENTINEL = "| Source |"  # first cell of the expected table header


@tool
def append_traceability_matrix(fact: str, source: str, epistemic_tag: str) -> str:
    """Logs a documented fact and its source into the Knowledge Traceability Matrix.
    Use this IMMEDIATELY after using search tools to secure the epistemic integrity trail.

    Args:
        fact: The specific finding or claim to record.
        source: The URL, PMID, or document reference where this was found.
        epistemic_tag: One of [FACT], [INFERENCE], or [SPECULATION].
    """
    filepath = os.path.join(
        os.path.dirname(__file__), "..", "Knowledge_Traceability_Matrix.md"
    )

    # Guard: the file must exist and contain the table header before we append.
    # Without this check a corrupted or deleted matrix silently receives raw rows,
    # producing an unparseable file on the next review.
    if not os.path.exists(filepath):
        return (
            f"FAILED to append to Traceability Matrix: "
            f"'{filepath}' does not exist. "
            "Create the file with its Markdown table header first."
        )

    try:
        existing = Path(filepath).read_text(encoding="utf-8")
    except Exception as e:
        return f"FAILED to read Traceability Matrix: {e}"

    if _MATRIX_HEADER_SENTINEL not in existing:
        return (
            "FAILED to append to Traceability Matrix: "
            "expected table header not found in the file. "
            "The matrix may be corrupted or in an unexpected format."
        )

    try:
        new_row = (
            f"| *Live Search:* {source} | **Auto-Agent** | "
            f"{fact} | Automatically sourced and summarized via Tool API. | "
            f"**{epistemic_tag}** |\n"
        )
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(new_row)
        return f"SUCCESS: Logged '{epistemic_tag}' finding to Traceability Matrix."
    except Exception as e:
        return f"FAILED to append to Traceability Matrix: {e}"


# ═══════════════════════════════════════════════════════
# WEB SEARCH TOOLS (Domain-Agnostic)
# ═══════════════════════════════════════════════════════


@tool
def search_you_engine(query: str, num_results: int = 5) -> str:
    """Searches the live web using the You.com Search API.
    Returns real-time, LLM-ready web results with titles, URLs, and snippets.
    Use for discovering recent papers, preprint pages, news, and grey literature
    not covered by search_pubmed or search_preprints.

    If results are empty, try narrowing the query or use you_research for a
    synthesised answer instead.

    Args:
        query: Search terms.
        num_results: Number of results to return (default 5).
    """
    api_key = os.getenv("YOU_API_KEY")
    if not api_key:
        return "ERROR: 'YOU_API_KEY' is not set in the environment variables."

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"query": query, "num_web_results": num_results}

    try:
        resp = requests.get(
            "https://api.ydc-index.io/search",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # Current API returns {"hits": [...]}
        # Older API returned {"results": {"web": [...]}} — handle both
        hits = data.get("hits") or data.get("results", {}).get("web", [])

        if not hits:
            return (
                f"No results found on You.com Search for: '{query}'. "
                f"Response keys: {list(data.keys())}. "
                "Try you_research or search_preprints instead."
            )

        output = []
        for i, hit in enumerate(hits[:num_results], 1):
            title = hit.get("title", "No title")
            url = hit.get("url", "")
            # Snippets may be a list or a single string depending on API version
            snippets = hit.get("snippets", [])
            snippet = " ".join(snippets) if isinstance(snippets, list) else snippets
            if not snippet:
                snippet = hit.get("description", "No snippet.")
            output.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")

        return "\n\n".join(output)

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return (
            f"You.com Search HTTP {status} error: {e}. "
            "Check that YOU_API_KEY is valid and has Search access."
        )
    except Exception as e:
        return f"Error contacting You.com Search API: {e}"


@tool
def you_research(query: str, num_web_results: int = 5) -> str:
    """Uses the You.com Research API to produce a synthesised, cited answer
    for a specific sub-question. You.com searches the web, reads multiple
    pages, and returns a structured answer with inline citations and sources.

    Use this when search_pubmed + search_preprints return insufficient evidence
    and you need a synthesised answer rather than raw paper lists. Call at most
    once per sub-question — it is the most expensive tool in the chain.

    Treat the output as a starting synthesis to critically evaluate through
    your domain expertise, not as a citable source in itself.

    Args:
        query: A precise, self-contained research question.
        num_web_results: Number of web sources You.com reads internally (default 5).
    """
    api_key = os.getenv("YOU_API_KEY")
    if not api_key:
        return "ERROR: 'YOU_API_KEY' is not set in the environment variables."

    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"query": query, "num_web_results": num_web_results}

    try:
        resp = requests.get(
            "https://api.ydc-index.io/rag",
            headers=headers,
            params=params,
            timeout=30,  # Research takes longer than Search
        )
        resp.raise_for_status()
        data = resp.json()

        answer = data.get("answer", "").strip()
        sources = data.get("sources", data.get("hits", []))

        if not answer:
            return (
                f"You.com Research returned no answer for: '{query}'. "
                f"Response keys: {list(data.keys())}. "
                "Try search_web or search_preprints instead."
            )

        source_lines = []
        for i, src in enumerate(sources[:8], 1):
            title = src.get("title", "Untitled")
            url = src.get("url", "")
            snippet = src.get("snippet", src.get("description", ""))[:200]
            source_lines.append(f"  [{i}] {title}\n      {url}\n      {snippet}")

        sources_block = "\n".join(source_lines) if source_lines else "  (no sources returned)"

        return (
            f"[You.com Research — synthesised answer]\n\n"
            f"{answer}\n\n"
            f"Sources consulted:\n{sources_block}\n\n"
            "[NOTE: Treat this as a starting synthesis. Verify key claims against "
            "primary sources before citing.]"
        )

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status == 429:
            return (
                "You.com Research rate limit hit. Wait and retry, "
                "or use search_web + scrape_page as an alternative."
            )
        return (
            f"You.com Research HTTP {status} error: {e}. "
            "Check that YOU_API_KEY is valid and has Research (RAG) access."
        )
    except Exception as e:
        return f"Error contacting You.com Research API: {e}"


@tool
def scrape_webpage(url: str, max_chars: int = 5000) -> str:
    """Fetches clean Markdown content from a URL using the You.com Contents API.
    Handles JavaScript-rendered pages, publisher sites, and preprint servers
    that raw HTTP requests cannot parse reliably.

    Use after search_web or search_preprints returns a URL you need to read
    in full — methodology sections, preprint abstracts, supplementary data.

    Falls back to direct HTTP + BeautifulSoup if YOU_API_KEY is not set or
    the Contents API returns an error.

    Args:
        url: The full URL to fetch.
        max_chars: Maximum characters to return (default 5000).
    """
    api_key = os.getenv("YOU_API_KEY")

    # ── Primary path: You.com Contents API ──────────────────────────────
    if api_key:
        try:
            headers = {"X-API-Key": api_key, "Accept": "application/json"}
            params = {"url": url}
            resp = requests.get(
                "https://api.ydc-index.io/news",
                headers=headers,
                params=params,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            # Contents API may return {"content": "...", "markdown": "..."}
            # or a list of results — handle both shapes
            content = ""
            if isinstance(data, dict):
                content = (
                    data.get("markdown")
                    or data.get("content")
                    or data.get("text")
                    or ""
                )
            elif isinstance(data, list) and data:
                first = data[0]
                content = (
                    first.get("markdown")
                    or first.get("content")
                    or first.get("text")
                    or ""
                )

            if content:
                if len(content) > max_chars:
                    content = (
                        content[:max_chars]
                        + f"\n\n[… truncated at {max_chars} chars]"
                    )
                return f"Content from {url} (via You.com Contents API):\n\n{content}"

            # If the API returned something but no recognised content field,
            # fall through to the BeautifulSoup fallback below
        except Exception:
            pass  # Fall through to BeautifulSoup fallback

    # ── Fallback: direct HTTP + BeautifulSoup ───────────────────────────
    try:
        from bs4 import BeautifulSoup
        req_headers = {"User-Agent": "ResearchSwarm/1.0 (Academic Research)"}
        resp = requests.get(url, headers=req_headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)

        if len(text) > max_chars:
            text = (
                text[:max_chars]
                + f"\n\n[… truncated at {max_chars} chars]"
            )
        return f"Content from {url} (via direct fetch):\n\n{text}"

    except ImportError:
        return (
            f"Could not fetch {url}: YOU_API_KEY not set and beautifulsoup4 "
            "is not installed. Run: pip install beautifulsoup4"
        )
    except Exception as e:
        return f"Error fetching {url}: {e}"

@tool
def lookup_doi(query: str) -> str:
    """Look up full publication metadata via Europe PMC and Crossref.
    No API key required — both APIs are open.

    Use this after search_pubmed or search_preprints returns a DOI or partial
    reference, to retrieve full citation details: title, authors, journal, year,
    abstract, and open-access PDF link.

    Args:
        query: A DOI (e.g. '10.1016/j.addbeh.2025.107964') or a title/keyword
               string for Crossref fuzzy search.
    """
    import urllib.parse

    # Detect whether the input looks like a DOI
    clean = query.strip().removeprefix("https://doi.org/").removeprefix("doi.org/")
    is_doi = clean.startswith("10.")

    # ── Europe PMC — preferred for open-access biomedical content ──────────
    if is_doi:
        try:
            epmc_url = (
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                f"?query=DOI:{urllib.parse.quote(clean)}&format=json&resultType=core"
            )
            r = requests.get(epmc_url, timeout=15)
            r.raise_for_status()
            results = r.json().get("resultList", {}).get("result", [])
            if results:
                art = results[0]
                title = art.get("title", "No title")
                authors = art.get("authorString", "Unknown authors")
                journal = art.get("journalTitle", "")
                year = art.get("pubYear", "")
                doi = art.get("doi", clean)
                abstract = (art.get("abstractText") or "No abstract available.")[:400]
                pmcid = art.get("pmcid", "")
                pdf_url = (
                    f"https://europepmc.org/articles/{pmcid}/pdf/render"
                    if pmcid else ""
                )
                return (
                    f"Title: {title}\n"
                    f"Authors: {authors}\n"
                    f"Journal: {journal} ({year})\n"
                    f"DOI: {doi}\n"
                    f"Abstract: {abstract}\n"
                    + (f"OA PDF: {pdf_url}" if pdf_url else "PDF: not available via Europe PMC")
                )
        except Exception:
            pass  # Fall through to Crossref

    # ── Crossref — broader journal/conference coverage ─────────────────────
    try:
        if is_doi:
            crossref_url = f"https://api.crossref.org/works/{urllib.parse.quote(clean)}"
            r = requests.get(
                crossref_url,
                headers={"User-Agent": "ResearchSwarm/1.0 (mailto:research@example.com)"},
                timeout=15,
            )
            r.raise_for_status()
            item = r.json().get("message", {})
            items = [item]
        else:
            crossref_url = "https://api.crossref.org/works"
            r = requests.get(
                crossref_url,
                params={"query": query, "rows": 3},
                headers={"User-Agent": "ResearchSwarm/1.0 (mailto:research@example.com)"},
                timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", [])

        if not items:
            return f"No results found for: '{query}' on Europe PMC or Crossref."

        output = []
        for item in items[:3]:
            title_parts = item.get("title", ["No title"])
            title = title_parts[0] if title_parts else "No title"
            doi = item.get("DOI", "")
            year = str(item.get("published", {}).get("date-parts", [[""]])[0][0])
            container = item.get("container-title", [""])
            journal = container[0] if container else ""
            author_list = item.get("author", [])
            authors = ", ".join(
                f"{a.get('family', '')} {a.get('given', '')[:1]}."
                for a in author_list[:3]
            )
            if len(author_list) > 3:
                authors += " et al."
            url = item.get("URL", f"https://doi.org/{doi}" if doi else "")

            output.append(
                f"Title: {title}\n"
                f"Authors: {authors}\n"
                f"Journal: {journal} ({year})\n"
                f"DOI: {doi}\n"
                f"URL: {url}"
            )

        return "\n\n---\n\n".join(output)

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        return f"Crossref HTTP {status} error for '{query}': {e}"
    except Exception as e:
        return f"Error in lookup_doi for '{query}': {e}"





def _get_active_agent_names() -> list[str]:
    """Return KB folders for personas currently configured in swarm_config.yml."""
    try:
        from automation.config import load_config

        config = load_config()
        agent_names = []
        for persona in config.get("personas", []):
            agent_name = Path(persona.get("persona_file", "")).parent.name
            if agent_name and agent_name not in agent_names:
                agent_names.append(agent_name)
        if agent_names:
            return agent_names
    except Exception:
        pass

    agents_dir = os.path.join(os.path.dirname(__file__), "..", "agents")
    return [
        d
        for d in os.listdir(agents_dir)
        if os.path.isdir(os.path.join(agents_dir, d)) and d != "__pycache__"
    ]


@tool
def search_knowledge_base(query: str, agent_name: str = "all", top_k: int = 5) -> str:
    """Searches the local Knowledge Base (vectorized documents in agents/*/KB/)
    for semantically relevant passages.

    Use this BEFORE external searches to check if the answer already
    exists in your local documents.

    Args:
        query: The search query to find relevant passages.
        agent_name: Search a specific agent's KB (e.g., 'ClinicalPsych') or 'all'.
        top_k: Number of top results to return.
    """
    try:
        try:
            from langchain_huggingface import HuggingFaceEmbeddings as SentenceTransformerEmbeddings
            from langchain_chroma import Chroma
        except ImportError:
            from langchain_community.embeddings import SentenceTransformerEmbeddings
            from langchain_community.vectorstores import Chroma
    except ImportError:
        return (
            "ERROR: Required packages not installed. "
            "Run: pip install langchain-huggingface langchain-chroma sentence-transformers"
        )

    db_base = os.path.join(os.path.dirname(__file__), "db")
    if not os.path.exists(db_base):
        return (
            "No Knowledge Base found. Run 'python -m automation.ingest' first "
            "to vectorize documents from agents/*/KB/ folders."
        )

    try:
        embedding_fn = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")
    except Exception as e:
        return f"Error initializing embeddings model: {e}"

    results = []

    if agent_name == "all":
        agent_names = _get_active_agent_names()
    else:
        agent_names = [agent_name]

    for name in agent_names:
        persist_dir = os.path.join(db_base, name)
        if not os.path.exists(persist_dir):
            continue
        try:
            db = Chroma(
                persist_directory=persist_dir, embedding_function=embedding_fn
            )
            docs = db.similarity_search(query, k=top_k)
            for doc in docs:
                source = doc.metadata.get("source", "Unknown")
                results.append(
                    f"[{name} KB | {os.path.basename(source)}]: "
                    f"{doc.page_content[:500]}"
                )
        except Exception as e:
            results.append(f"[{name} KB]: Error searching — {e}")

    if not results:
        return (
            "No relevant documents found in the local Knowledge Base. "
            "Try an external search tool instead."
        )

    return "\n\n---\n\n".join(results[:top_k])


# ═══════════════════════════════════════════════════════
# VERSION CONTROL TOOL (Audit Trail)
# ═══════════════════════════════════════════════════════


@tool
def git_commit_snapshot(message: str) -> str:
    """Creates a git commit of swarm output files only (Drafts/ directory and the
    Knowledge Traceability Matrix). Stages only known safe paths — never the full
    working tree — to prevent accidentally committing credentials or binaries.

    Use this after write_manuscript_section to lock in a versioned audit snapshot.

    Args:
        message: Descriptive commit message summarizing what was produced.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # Determine safe staging paths from config (fall back to defaults)
    try:
        from automation.config import load_config
        cfg = load_config()
        output_dir = cfg["swarm"].get("output_dir", "./Drafts").lstrip("./")
        matrix = cfg["swarm"].get(
            "traceability_matrix", "./Knowledge_Traceability_Matrix.md"
        ).lstrip("./")
    except Exception:
        output_dir = "Drafts"
        matrix = "Knowledge_Traceability_Matrix.md"

    safe_paths = [output_dir, matrix]

    try:
        # Stage only the swarm's output paths — never git add -A
        subprocess.run(
            ["git", "add", "--", *safe_paths],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

        status = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

        if not status.stdout.strip():
            return "No changes to commit in swarm output paths."

        result = subprocess.run(
            ["git", "commit", "-m", f"[swarm-auto] {message}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )

        return f"SUCCESS: Committed snapshot.\n{result.stdout}"

    except subprocess.CalledProcessError as e:
        return f"Git commit failed: {e.stderr}"
    except FileNotFoundError:
        return "ERROR: git is not installed or not in PATH."
