"""Deterministic HCM pilot mode built from a fixed PMID seed corpus."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from pydantic import BaseModel, Field

from automation.exporters.identity import digest
from automation.exporters.identity import package_id as content_package_id
from automation.exporters.mapppp import (
    CONTRACT_VERSION,
    DOMAIN_TYPE,
    EvidenceAssertion,
    GraphAssertion,
    MappingAssertion,
    MAPPPPBundle,
    MetadataAssertion,
    PackageMetadata,
    Provenance,
    ReviewNote,
    StudyContext,
)
from automation.hcmo_suggestions import HCMO_SOURCE, suggest_hcmo_mappings

HCM_PILOT_MATRIX_HEADER = """# HCMSAS Knowledge Traceability Matrix

## Traceability Log

| claim_id | claim | epistemic_tag | source | agent | mnms_category | notes | status |
|---|---|---|---|---|---|---|---|
"""


class PilotSpec(BaseModel):
    pilot_name: str
    research_question: str
    pmids: list[str]
    expected_assertions: list[str] = Field(default_factory=list)


class CachedPaper(BaseModel):
    pmid: str
    title: str
    abstract: str
    journal: str | None = None
    year: str | None = None
    doi: str | None = None


class PilotManifest(BaseModel):
    pilot_name: str
    created_at: str
    package_id: str
    mapppp_version: str = CONTRACT_VERSION
    domain: str = DOMAIN_TYPE
    pmids: list[str]
    corpus_digest: str | None = None


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _load_pilot_spec(spec_path: str | Path) -> PilotSpec:
    with open(spec_path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    payload["pmids"] = [str(pmid) for pmid in payload.get("pmids", [])]
    return PilotSpec.model_validate(payload)


def _load_manifest(path: Path, pilot_name: str, pmids: list[str]) -> PilotManifest:
    if path.exists():
        manifest = PilotManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if manifest.pilot_name != pilot_name or manifest.pmids != pmids:
            raise ValueError("Pilot manifest does not match the requested name and PMID corpus")
        return manifest

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    package_id = "pending"
    manifest = PilotManifest(
        pilot_name=pilot_name,
        created_at=created_at,
        package_id=package_id,
        pmids=pmids,
    )
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return manifest


def _fetch_pubmed_paper(pmid: str) -> CachedPaper:
    response = requests.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "xml"},
        timeout=30,
    )
    response.raise_for_status()
    root = ET.fromstring(response.text)
    article = root.find(".//PubmedArticle")
    if article is None:
        raise ValueError(f"No PubMed article returned for PMID {pmid}")

    title_el = article.find(".//ArticleTitle")
    journal_el = article.find(".//Journal/Title")
    year_el = article.find(".//PubDate/Year")
    doi = None
    for article_id in article.findall(".//ArticleId"):
        if article_id.get("IdType") == "doi":
            doi = article_id.text
            break

    abstract_parts: list[str] = []
    for ab in article.findall(".//AbstractText"):
        text = "".join(ab.itertext()).strip()
        if text:
            label = ab.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)

    return CachedPaper(
        pmid=pmid,
        title="".join(title_el.itertext()).strip() if title_el is not None else f"PMID {pmid}",
        abstract=" ".join(abstract_parts).strip(),
        journal=journal_el.text.strip() if journal_el is not None and journal_el.text else None,
        year=year_el.text.strip() if year_el is not None and year_el.text else None,
        doi=doi,
    )


def _load_or_fetch_paper(cache_dir: Path, pmid: str, refresh: bool) -> CachedPaper:
    cache_path = cache_dir / f"{pmid}.json"
    if cache_path.exists() and not refresh:
        return CachedPaper.model_validate_json(cache_path.read_text(encoding="utf-8"))
    paper = _fetch_pubmed_paper(pmid)
    cache_path.write_text(paper.model_dump_json(indent=2), encoding="utf-8")
    return paper


def _cache_seed_corpus(spec: PilotSpec, cache_dir: Path, refresh: bool) -> list[CachedPaper]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    papers = [_load_or_fetch_paper(cache_dir, pmid, refresh) for pmid in spec.pmids]
    corpus_path = cache_dir / "seed_corpus.json"
    corpus_path.write_text(
        json.dumps([paper.model_dump(mode="json") for paper in papers], indent=2),
        encoding="utf-8",
    )
    return papers


def _contains(text: str, *terms: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def _normalise_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).strip(" .")


def _sentence_split(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+", text.strip())
    return [_normalise_value(chunk) for chunk in chunks if _normalise_value(chunk)]


def _maybe_add_metadata(
    items: list[dict[str, Any]],
    *,
    assertion_id: str,
    field: str,
    value: str | None,
    provenance: Provenance,
    epistemic_label: str = "reported",
) -> None:
    if not value:
        return
    items.append(
        MetadataAssertion(
            assertion_id=assertion_id,
            epistemic_label=epistemic_label,
            provenance=provenance,
            field=field,
            value=_normalise_value(value),
        ).model_dump(mode="json")
    )


def _extract_strain(text: str) -> str | None:
    patterns = [
        r"\b(C57BL/6J)\b",
        r"\b(C57/BL6)\b",
        r"\b(C57BL/6)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).replace("C57/BL6", "C57BL/6")
    return None


def _extract_sex(text: str) -> str | None:
    lowered = text.lower()
    if re.search(r"\b(male and female|female and male|both sexes)\b", lowered):
        return "male_and_female"
    if re.search(r"\bfemales?\b", lowered) and re.search(r"\bmales?\b", lowered):
        return "male_and_female"
    if re.search(r"\bfemales?\b", lowered):
        return "female"
    if re.search(r"\bmales?\b", lowered):
        return "male"
    return None


def _extract_age(text: str) -> str | None:
    patterns = [
        r"\b(\d+(?:-\d+)?-week-old)\b",
        r"\b(\d+(?:-\d+)?\s*weeks? old)\b",
        r"\b(up to \d+\s*months? of age)\b",
        r"\b(from \d+\s*months? until \d+\s*months? of age)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_genotype_model(text: str) -> str | None:
    patterns = [
        r"\b(Cry1-/-,\s*Cry2-/- double knockout)\b",
        r"\b(APP/?PS1)\b",
        r"\b(Alzheimer's disease model)\b",
        r"\b(KRAS-LKB1)\b",
        r"\b(KRASG12C)\b",
        r"\b(lung cancer model)\b",
        r"\b(aging colony)\b",
        r"\b(type-1-like diabetes model)\b",
    ]
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            matches.append(_normalise_value(match.group(1)))
    if not matches:
        return None
    return "; ".join(dict.fromkeys(matches))


def _extract_monitoring_duration(text: str) -> str | None:
    patterns = [
        r"\b(continuously for 24/7)\b",
        r"\b(continuously 24 hours a day, 7 days a week)\b",
        r"\b(up to \d+\s*months? of age)\b",
        r"\b(over time)\b",
        r"\b(longitudinal experiments?)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1)
    return None


def _extract_light_dark_schedule(text: str) -> str | None:
    timing = re.search(r"\b\d{1,2}\s*[:/]\s*\d{1,2}\s*(?:h(?:ours?)?\s*)?(?:light[- /]dark|L\s*[:/]\s*D)\b", text, re.I)
    if timing:
        return _normalise_value(timing.group(0))
    lowered = text.lower()
    terms = []
    if "light/dark" in lowered:
        terms.append("light_dark")
    if "constant dark" in lowered:
        terms.append("constant_dark")
    if "constant light" in lowered:
        terms.append("constant_light")
    if "dark phase" in lowered:
        terms.append("dark_phase_reported")
    if "light phase" in lowered:
        terms.append("light_phase_reported")
    return "; ".join(dict.fromkeys(terms)) if terms else None


def _extract_sensor_modality(text: str) -> str | None:
    match = re.search(r"\b(infrared|capacitive|video|camera)\s+(?:sensor|sensing|tracking)\b", text, re.I)
    return _normalise_value(match.group(0).lower()) if match else None


def _extract_software_version(text: str) -> str | None:
    match = re.search(r"\b(?:classifier|software)\s+version\s+[A-Za-z0-9._-]+\b", text, re.I)
    return _normalise_value(match.group(0)) if match else None


def _extract_housing_specifics(text: str) -> str | None:
    lowered = text.lower()
    terms = []
    if "group-housed" in lowered or "group housed" in lowered:
        terms.append("group_housed")
    if "individually ventilated cages" in lowered:
        terms.append("individually_ventilated_cages")
    if "single animal" in lowered:
        terms.append("single_animal_tracking")
    if "aging colony" in lowered:
        terms.append("colony_monitoring")
    return "; ".join(dict.fromkeys(terms)) if terms else None


def _extract_activity_metric_definition(text: str) -> str | None:
    lowered = text.lower()
    terms = []
    if "locomotor activity" in lowered:
        terms.append("locomotor_activity")
    if "home cage activity" in lowered:
        terms.append("home_cage_activity")
    if "circadian activity profiles" in lowered:
        terms.append("circadian_activity_profile")
    if "rest disturbance" in lowered or "rdi" in lowered:
        terms.append("rest_disturbance_index")
    if "dark phase" in lowered:
        terms.append("dark_phase_activity")
    return "; ".join(dict.fromkeys(terms)) if terms else None


def _claim_sentences(paper: CachedPaper) -> list[str]:
    text = f"{paper.title}. {paper.abstract}".strip()
    selected: list[str] = []
    keywords = (
        "circadian",
        "locomotor",
        "activity",
        "monitor",
        "monitoring",
        "dvc",
        "digital ventilated cage",
        "group-housed",
        "dark phase",
        "light phase",
        "welfare",
        "reproducible",
        "comparable",
        "hyperactivity",
        "arrhythmic",
    )
    for sentence in _sentence_split(text):
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            selected.append(sentence)
    if not selected:
        selected = _sentence_split(text)[:2]
    return list(dict.fromkeys(selected))


def _paper_source_id(paper: CachedPaper) -> str:
    return f"PMID:{paper.pmid}"


def _paper_provenance(paper: CachedPaper, proposed_by: str, created_at: str) -> Provenance:
    return Provenance(
        source_id=_paper_source_id(paper),
        source_anchor=f"title/abstract PMID {paper.pmid}",
        proposed_by=proposed_by,
        created_at=created_at,
    )


def _load_or_build_json(path: Path, refresh: bool, factory) -> dict[str, Any]:
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    value = factory()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    return value


def _build_metadata_and_evidence(papers: list[CachedPaper], created_at: str) -> dict[str, Any]:
    metadata_assertions: list[dict[str, Any]] = []
    evidence_assertions: list[dict[str, Any]] = []

    for paper in papers:
        title_blob = f"{paper.title} {paper.abstract}"
        provenance = _paper_provenance(paper, "Metadata Architect", created_at)

        if _contains(title_blob, "mouse", "mice"):
            _maybe_add_metadata(
                metadata_assertions,
                assertion_id=f"metadata-{paper.pmid}-species",
                field="species",
                value="mouse",
                provenance=provenance,
            )

        if _contains(title_blob, "locomotor"):
            _maybe_add_metadata(
                metadata_assertions,
                assertion_id=f"metadata-{paper.pmid}-endpoint",
                field="endpoint",
                value="locomotor_activity",
                provenance=provenance,
            )

        if _contains(title_blob, "circadian"):
            _maybe_add_metadata(
                metadata_assertions,
                assertion_id=f"metadata-{paper.pmid}-temporal",
                field="temporal_structure",
                value="circadian",
                provenance=provenance,
            )

        if _contains(title_blob, "group-housed", "group housed"):
            _maybe_add_metadata(
                metadata_assertions,
                assertion_id=f"metadata-{paper.pmid}-housing",
                field="housing_context",
                value="group_housed",
                provenance=provenance,
            )

        if _contains(title_blob, "digital ventilated cage"):
            _maybe_add_metadata(
                metadata_assertions,
                assertion_id=f"metadata-{paper.pmid}-platform",
                field="monitoring_platform",
                value="digital_ventilated_cage",
                provenance=provenance,
            )

        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-strain", field="strain", value=_extract_strain(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-sex", field="sex", value=_extract_sex(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-age", field="age", value=_extract_age(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-genotype", field="genotype_or_model", value=_extract_genotype_model(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-duration", field="monitoring_duration", value=_extract_monitoring_duration(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-lightdark", field="light_dark_schedule", value=_extract_light_dark_schedule(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-housing-specifics", field="housing_specifics", value=_extract_housing_specifics(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-activity-metric", field="activity_metric_definition", value=_extract_activity_metric_definition(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-sensor-modality", field="sensor_modality", value=_extract_sensor_modality(title_blob), provenance=provenance)
        _maybe_add_metadata(metadata_assertions, assertion_id=f"metadata-{paper.pmid}-software-version", field="software_version", value=_extract_software_version(title_blob), provenance=provenance)

        for index, sentence in enumerate(_claim_sentences(paper), start=1):
            evidence_assertions.append(
                EvidenceAssertion(
                    assertion_id=f"evidence-{paper.pmid}-{index}",
                    epistemic_label="reported",
                    provenance=Provenance.model_validate(provenance.model_dump(mode="json")),
                    subject=f"Study_{paper.pmid}",
                    observation=sentence,
                    value=sentence,
                ).model_dump(mode="json")
            )

    return {
        "metadata_assertions": metadata_assertions,
        "evidence_assertions": evidence_assertions,
    }


def _build_review_notes(
    papers: list[CachedPaper],
    metadata_assertions: list[dict[str, Any]],
    evidence_assertions: list[dict[str, Any]],
    created_at: str,
) -> dict[str, Any]:
    provenance = Provenance(
        source_id="seed_corpus",
        source_anchor="fixed PMID corpus",
        proposed_by="Reproducibility and Bias Auditor",
        created_at=created_at,
    )
    review_notes = [
        ReviewNote(
            note_id="review-note-light-cycle",
            disposition="flag",
            note="Explicit light-cycle timing was not consistently extracted from the seed abstracts. Inspect full methods before any ontology or reuse decision; this is not a conformance finding.",
            provenance=provenance,
            related_assertion_ids=[item["assertion_id"] for item in metadata_assertions if item["field"] == "light_dark_schedule"],
        ).model_dump(mode="json"),
        ReviewNote(
            note_id="review-note-processing-details",
            disposition="request_changes",
            note="Software and processing details were not consistently extracted at abstract level. Inspect full methods before comparing studies; no compliance status is inferred.",
            provenance=provenance,
            related_assertion_ids=[item["assertion_id"] for item in metadata_assertions if item["field"] == "software_version"],
        ).model_dump(mode="json"),
        ReviewNote(
            note_id="review-note-sex-and-age",
            disposition="flag",
            note="Sex and age are not uniformly extracted from the seed abstracts; inspect source methods before reuse decisions.",
            provenance=provenance,
            related_assertion_ids=[
                item["assertion_id"]
                for item in metadata_assertions
                if item["field"] in {"sex", "age"}
            ],
        ).model_dump(mode="json"),
        ReviewNote(
            note_id="review-note-housing-and-metric-definition",
            disposition="comment",
            note="Housing specifics and activity metric definitions vary across the corpus and should be normalized before asserting strong cross-study comparability.",
            provenance=provenance,
            related_assertion_ids=[
                item["assertion_id"]
                for item in metadata_assertions
                if item["field"] in {"housing_specifics", "activity_metric_definition", "housing_context"}
            ],
        ).model_dump(mode="json"),
    ]
    for paper in papers:
        field_set = {item["field"] for item in metadata_assertions
                     if item["provenance"]["source_id"] == _paper_source_id(paper)}
        linked = [item["assertion_id"] for item in evidence_assertions
                  if item["provenance"]["source_id"] == _paper_source_id(paper)]
        if "light_dark_schedule" not in field_set or not any(
            re.search(r"\b\d+\s*[:/]\s*\d+\b", str(item["value"]))
            for item in metadata_assertions
            if item["field"] == "light_dark_schedule" and item["provenance"]["source_id"] == _paper_source_id(paper)
        ):
            review_notes.append(ReviewNote(
                note_id=f"review-{paper.pmid}-light-timing", disposition="flag",
                note="No explicit light/dark phase timing was extracted from this abstract; inspect full methods. No HCMO conformance judgment is made.",
                provenance=_paper_provenance(paper, "Reproducibility and Bias Auditor", created_at),
                related_assertion_ids=linked,
            ).model_dump(mode="json"))
        if "software_version" not in field_set:
            review_notes.append(ReviewNote(
                note_id=f"review-{paper.pmid}-software-version", disposition="comment",
                note="No software or classifier version was extracted from this abstract; inspect full methods before comparison.",
                provenance=_paper_provenance(paper, "Reproducibility and Bias Auditor", created_at),
                related_assertion_ids=linked,
            ).model_dump(mode="json"))
    return {"review_notes": review_notes}


def _paper_reference(index: int, paper: CachedPaper) -> str:
    venue = paper.journal or "Unknown journal"
    year = paper.year or "n.d."
    doi = f" doi:{paper.doi}." if paper.doi else ""
    return f"[{index}] {paper.title} {venue}. {year}. PMID: {paper.pmid}.{doi}"


def _build_final_draft(
    spec: PilotSpec,
    papers: list[CachedPaper],
    metadata_assertions: list[dict[str, Any]],
    evidence_assertions: list[dict[str, Any]],
    mapping_assertions: list[dict[str, Any]],
    graph_assertions: list[dict[str, Any]],
    review_notes: list[dict[str, Any]],
) -> str:
    metadata_lines = "\n".join(
        f"- [MISSING] {item['note']} Source: {item['provenance']['source_id']}."
        for item in review_notes if item["note_id"].startswith("review-")
    ) or "No source-specific abstract extraction gaps were flagged."
    evidence_table = "\n".join(
        f"| {item['subject']} | {item['observation']} | {item['provenance']['source_id']} |"
        for index, item in enumerate(evidence_assertions, start=1)
    )
    mapping_table = "\n".join(
        f"| {item['local_field']} | {item['target_schema']} | {item['target_field']} | {item['provenance']['source_id']} |"
        for item in mapping_assertions
    )
    graph_table = "No graph relations are minted from abstract-only text. Curators may propose them after source and ontology review."
    risk_table = "\n".join(
        f"| {item['note_id']} | {item['disposition']} | {item['note']} |"
        for item in review_notes
    )
    references = "\n".join(_paper_reference(index, paper) for index, paper in enumerate(papers, start=1))
    return f"""## A. Research scope
Pilot `{spec.pilot_name}` addresses the question: {spec.research_question}

## B. Key evidence summary
This deterministic HCM pilot uses only the fixed seed corpus PMIDs {', '.join(spec.pmids)} and does not expand beyond those papers.

## C. Evidence table with in-text citations
| study | claim | stable source ID |
|---|---|---|
{evidence_table}

## D. Optional HCMO 0.4.1 term suggestions
| metadata_field | target_schema | target_field | source_id |
|---|---|---|---|
{mapping_table or 'No ontology suggestions requested or supported by the extracted evidence.'}

## E. Missing metadata and ambiguity report
{metadata_lines}

## F. Knowledge graph schema proposal
{graph_table}

## G. Reproducibility and comparability risks
| risk_id | disposition | description |
|---|---|---|
{risk_table}

## H. Conservative conclusions
[INFERENCE] The fixed PMID corpus supports source-linked text and metadata proposals. Abstract-level gaps require full-method review; neither HCMO nor MNMS conformance is assessed here.

## I. References
{references}
"""


def _build_traceability_matrix(
    path: Path,
    metadata_assertions: list[dict[str, Any]],
    evidence_assertions: list[dict[str, Any]],
    review_notes: list[dict[str, Any]],
) -> None:
    rows = [HCM_PILOT_MATRIX_HEADER]
    claim_index = 1

    def tag_for(label: str) -> str:
        return {
            "reported": "[FACT]",
            "inferred": "[INFERENCE]",
            "normalized": "[INFERENCE]",
            "uncertain": "[CONTESTED]",
            "extracted": "[FACT]",
        }.get(label, "[INFERENCE]")

    for item in metadata_assertions:
        rows.append(
            f"| HCM-{claim_index:03d} | {item['field']}: {item['value']} | {tag_for(item['epistemic_label'])} | {item['provenance']['source_id']} | {item['provenance']['proposed_by']} | {item['field']} | Deterministic pilot metadata extraction | active |\n"
        )
        claim_index += 1
    for item in evidence_assertions:
        rows.append(
            f"| HCM-{claim_index:03d} | {item['observation']} | {tag_for(item['epistemic_label'])} | {item['provenance']['source_id']} | {item['provenance']['proposed_by']} | evidence | Deterministic pilot evidence extraction | active |\n"
        )
        claim_index += 1
    for item in review_notes:
        rows.append(
            f"| HCM-{claim_index:03d} | {item['note']} | [INFERENCE] | {item['provenance']['source_id']} | {item['provenance']['proposed_by']} | review | Pilot review note | active |\n"
        )
        claim_index += 1

    path.write_text("".join(rows), encoding="utf-8")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_hcm_pilot(
    spec_path: str | Path,
    output_root: str | Path | None = None,
    *,
    refresh_cache: bool = False,
    refresh_intermediates: bool = False,
    suggest_hcmo: bool = False,
) -> dict[str, Path]:
    spec = _load_pilot_spec(spec_path)
    root = Path(output_root) if output_root else Path.cwd() / "Drafts" / "hcm_pilot" / _slugify(spec.pilot_name)
    root.mkdir(parents=True, exist_ok=True)
    cache_dir = root / "cache" / "papers"
    intermediates_dir = root / "intermediates"
    drafts_dir = root / "Drafts" / "HCMSAS"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(root / "pilot_manifest.json", spec.pilot_name, spec.pmids)
    papers = _cache_seed_corpus(spec, cache_dir, refresh_cache)
    fingerprint = digest([paper.model_dump(mode="json") for paper in papers])
    if manifest.corpus_digest and manifest.corpus_digest != fingerprint:
        refresh_intermediates = True
    manifest.corpus_digest = fingerprint

    metadata_evidence = _load_or_build_json(
        intermediates_dir / "metadata_evidence_v3.json",
        refresh_intermediates,
        lambda: _build_metadata_and_evidence(papers, manifest.created_at),
    )
    coverage = suggest_hcmo_mappings(metadata_evidence["metadata_assertions"])
    coverage["suggestions_enabled"] = suggest_hcmo
    coverage_path = root / "hcmo_coverage.json"
    _write_json(coverage_path, coverage)
    mapping_assertions = coverage["mappings"] if suggest_hcmo else []
    graph_assertions: list[dict[str, Any]] = []
    review_payload = _load_or_build_json(
        intermediates_dir / "review_notes_v3.json",
        refresh_intermediates,
        lambda: _build_review_notes(
            papers,
            metadata_evidence["metadata_assertions"],
            metadata_evidence["evidence_assertions"],
            manifest.created_at,
        ),
    )

    draft_text = _build_final_draft(
        spec,
        papers,
        metadata_evidence["metadata_assertions"],
        metadata_evidence["evidence_assertions"],
        mapping_assertions,
        graph_assertions,
        review_payload["review_notes"],
    )
    draft_path = drafts_dir / "final_report.md"
    draft_path.write_text(draft_text, encoding="utf-8")

    traceability_path = root / "Knowledge_Traceability_Matrix_HCMSAS.md"
    _build_traceability_matrix(
        traceability_path,
        metadata_evidence["metadata_assertions"],
        metadata_evidence["evidence_assertions"],
        review_payload["review_notes"],
    )

    bundle = MAPPPPBundle(
        package_metadata=PackageMetadata(
            package_id=manifest.package_id,
            created_at=manifest.created_at,
            created_by="automation.hcm_pilot.run_hcm_pilot",
        ),
        study_context=StudyContext(species="mouse") if any(
            item["field"] == "species" and item["value"] == "mouse"
            for item in metadata_evidence["metadata_assertions"]
        ) else None,
        sources=[{"source_id": _paper_source_id(paper), "source_type": "paper", "title": paper.title}
                 for paper in papers] + [{"source_id": "seed_corpus", "source_type": "note",
                                          "title": "Fixed PMID seed corpus"}] + ([HCMO_SOURCE] if suggest_hcmo else []),
        metadata_assertions=[MetadataAssertion.model_validate(item) for item in metadata_evidence["metadata_assertions"]],
        evidence_assertions=[EvidenceAssertion.model_validate(item) for item in metadata_evidence["evidence_assertions"]],
        mapping_assertions=[MappingAssertion.model_validate(item) for item in mapping_assertions],
        graph_assertions=[GraphAssertion.model_validate(item) for item in graph_assertions],
        review_notes=[ReviewNote.model_validate(item) for item in review_payload["review_notes"]],
    )
    identity = content_package_id(bundle.model_dump(mode="json", exclude_none=True), _slugify(spec.pilot_name))
    bundle.package_metadata.package_id = identity
    manifest.package_id = identity
    (root / "pilot_manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    bundle_path = root / "mapppp_bundle.json"
    bundle_path.write_text(bundle.model_dump_json(indent=2, exclude_none=True), encoding="utf-8")

    return {
        "root": root,
        "manifest": root / "pilot_manifest.json",
        "draft": draft_path,
        "traceability_matrix": traceability_path,
        "bundle": bundle_path,
        "cache_dir": cache_dir,
        "intermediates_dir": intermediates_dir,
        "coverage": coverage_path,
    }
