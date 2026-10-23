"""Canonical MAPPPP v0.1.0 export support for downstream packaging."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from automation.exporters.identity import SourceRegistry
from automation.exporters.identity import package_id as content_package_id
from automation.hcmo_suggestions import HCMO_SOURCE, suggest_hcmo_mappings

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SECTION_RE = re.compile(r"^\s{0,3}(?:##|#)\s+([A-I])\.\s+(.+?)\s*$", re.MULTILINE)
_KNOWN_EPISTEMIC_TAGS = [
    "[FACT]",
    "[INFERENCE]",
    "[SPECULATION]",
    "[MISSING]",
    "[MISSING SOURCE]",
    "[PLATFORM-SPECIFIC]",
    "[CONTESTED]",
]
_TRACEABILITY_HEADERS = [
    "claim_id",
    "claim",
    "epistemic_tag",
    "source",
    "agent",
    "mnms_category",
    "notes",
    "status",
]
_EPISTEMIC_LABEL_MAP = {
    "[FACT]": "reported",
    "[INFERENCE]": "inferred",
    "[SPECULATION]": "uncertain",
    "[MISSING]": "uncertain",
    "[MISSING SOURCE]": "uncertain",
    "[PLATFORM-SPECIFIC]": "extracted",
    "[CONTESTED]": "uncertain",
}

HCM_METADATA_PERSONA = "Metadata Architect"
HCM_RISK_PERSONA = "Reproducibility and Bias Auditor"
CANONICAL_PROPOSED_STATUS = "proposed"
DEFAULT_EPISTEMIC_LABEL = "normalized"
CONTRACT_NAME = "MAPPPP"
CONTRACT_VERSION = "0.1.0"
DOMAIN_TYPE = "hcm"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _extract_citations(text: str) -> list[str]:
    return _unique([f"[{match}]" for match in _CITATION_RE.findall(text or "")])


def _extract_epistemic_tags(text: str) -> list[str]:
    return [tag for tag in _KNOWN_EPISTEMIC_TAGS if tag in (text or "")]


def _normalise_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", header.strip().lower()).strip("_")


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower()).strip("_")


def _strip_epistemic_and_citations(text: str) -> str:
    cleaned = text or ""
    for tag in _KNOWN_EPISTEMIC_TAGS:
        cleaned = cleaned.replace(tag, "")
    cleaned = _CITATION_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" .")


def _first_epistemic_label(*values: str) -> str:
    for value in values:
        for tag in _extract_epistemic_tags(value):
            return _EPISTEMIC_LABEL_MAP.get(tag, DEFAULT_EPISTEMIC_LABEL)
    return DEFAULT_EPISTEMIC_LABEL


def _normalise_review_disposition(label: str | None, note: str | None) -> str:
    text = " ".join(filter(None, [label, note])).lower()
    if any(
        token in text
        for token in ("request_changes", "request changes", "change", "fix", "must", "do not", "require")
    ):
        return "request_changes"
    if any(token in text for token in ("flag", "risk", "warning", "concern", "limiting", "omitted")):
        return "flag"
    return "comment"


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    if len(lines) < 2:
        return []

    headers = [cell.strip() for cell in lines[0].strip().strip("|").split("|")]
    if not headers or all(not header for header in headers):
        return []

    rows: list[dict[str, str]] = []
    for raw_line in lines[2:]:
        if not raw_line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in raw_line.strip().strip("|").split("|")]
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        row = {
            _normalise_header(header): cells[index]
            for index, header in enumerate(headers)
        }
        if any(value for value in row.values()):
            rows.append(row)
    return rows


def _extract_markdown_tables(text: str) -> list[list[dict[str, str]]]:
    tables: list[list[dict[str, str]]] = []
    block: list[str] = []

    for line in (text or "").splitlines():
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if block:
            parsed = _parse_markdown_table(block)
            if parsed:
                tables.append(parsed)
            block = []

    if block:
        parsed = _parse_markdown_table(block)
        if parsed:
            tables.append(parsed)

    return tables


def _extract_bullets(text: str) -> list[str]:
    bullets: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            bullets.append(stripped[2:].strip())
    return bullets


def _split_sections(markdown_text: str) -> dict[str, str]:
    matches = list(_SECTION_RE.finditer(markdown_text or ""))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown_text)
        sections[match.group(1)] = markdown_text[start:end].strip()
    return sections


def _parse_references(section_text: str) -> dict[str, str]:
    references: dict[str, str] = {}
    for line in (section_text or "").splitlines():
        stripped = line.strip()
        bracket_match = re.match(r"^(\[\d+\])\s+(.*)$", stripped)
        if bracket_match:
            references[bracket_match.group(1)] = bracket_match.group(2).strip()
            continue
        numbered_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if numbered_match:
            references[f"[{numbered_match.group(1)}]"] = numbered_match.group(2).strip()
    return references


class Provenance(BaseModel):
    source_id: str
    source_anchor: str
    proposed_by: str
    created_at: str


class PackageMetadata(BaseModel):
    package_id: str
    created_at: str
    created_by: str
    contract_name: str = CONTRACT_NAME
    contract_version: str = CONTRACT_VERSION
    domain_type: str = DOMAIN_TYPE


class StudyContext(BaseModel):
    species: str | None = None


class MetadataAssertion(BaseModel):
    assertion_id: str
    status: str = CANONICAL_PROPOSED_STATUS
    epistemic_label: str = DEFAULT_EPISTEMIC_LABEL
    provenance: Provenance = Field(default_factory=Provenance)
    field: str
    value: str


class EvidenceAssertion(BaseModel):
    assertion_id: str
    status: str = CANONICAL_PROPOSED_STATUS
    epistemic_label: str = DEFAULT_EPISTEMIC_LABEL
    provenance: Provenance = Field(default_factory=Provenance)
    subject: str
    observation: str
    value: str


class MappingAssertion(BaseModel):
    assertion_id: str
    status: str = CANONICAL_PROPOSED_STATUS
    epistemic_label: str = DEFAULT_EPISTEMIC_LABEL
    provenance: Provenance = Field(default_factory=Provenance)
    local_field: str
    target_schema: str
    target_field: str
    mapping_rationale: str
    confidence: float


class GraphAssertion(BaseModel):
    assertion_id: str
    status: str = CANONICAL_PROPOSED_STATUS
    epistemic_label: str = DEFAULT_EPISTEMIC_LABEL
    provenance: Provenance = Field(default_factory=Provenance)
    subject: str
    predicate: str
    object: str
    object_type: str | None = None


class ReviewNote(BaseModel):
    note_id: str
    disposition: str
    note: str
    provenance: Provenance = Field(default_factory=Provenance)
    related_assertion_ids: list[str] = Field(default_factory=list)


class MAPPPPBundle(BaseModel):
    package_metadata: PackageMetadata
    sources: list[dict[str, str]] = Field(default_factory=list)
    study_context: StudyContext | None = None
    metadata_assertions: list[MetadataAssertion] = Field(default_factory=list)
    evidence_assertions: list[EvidenceAssertion] = Field(default_factory=list)
    mapping_assertions: list[MappingAssertion] = Field(default_factory=list)
    graph_assertions: list[GraphAssertion] = Field(default_factory=list)
    review_notes: list[ReviewNote] = Field(default_factory=list)


def _provenance(
    source_id: str | None,
    citations: list[str],
    references: SourceRegistry,
    proposed_by: str,
    created_at: str,
) -> Provenance:
    stable_id, source_anchor = references.resolve(source_id, citations)
    return Provenance(
        source_id=stable_id,
        source_anchor=source_anchor,
        proposed_by=proposed_by,
        created_at=created_at,
    )


def _load_traceability_rows(traceability_matrix_path: Path) -> list[dict[str, str]]:
    text = traceability_matrix_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    expected_headers = _TRACEABILITY_HEADERS
    for index, line in enumerate(lines):
        normalised_headers = [
            _normalise_header(cell)
            for cell in line.strip().strip("|").split("|")
        ]
        if not set(expected_headers).issubset(normalised_headers):
            continue
        headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows: list[dict[str, str]] = []
        for raw_line in lines[index + 2:]:
            stripped = raw_line.strip()
            if not stripped.startswith("|"):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) != len(headers):
                continue
            if all(set(cell) <= {"-"} for cell in cells):
                continue
            rows.append(
                {
                    _normalise_header(header): cells[cell_index]
                    for cell_index, header in enumerate(headers)
                }
            )
        if rows:
            return rows

    tables = _extract_markdown_tables(text)
    for table in tables:
        headers = set(table[0].keys()) if table else set()
        if {"claim_id", "claim", "epistemic_tag", "source", "agent"}.issubset(headers):
            return table
    return []


def _load_review_note_payload(review_notes_path: Path | None) -> list[dict[str, Any]]:
    if review_notes_path is None:
        return []
    if not review_notes_path.exists():
        raise FileNotFoundError(f"Review notes file not found: {review_notes_path}")
    with open(review_notes_path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else []


def _mapping_confidence(classification: str | None, value_or_status: str | None) -> float:
    # A named category or a missing value is not evidence of semantic fit.
    return 0.5


def _metadata_gap_field_and_value(text: str) -> tuple[str, str]:
    cleaned = _strip_epistemic_and_citations(text)
    lowered = cleaned.lower()
    if " was not reported" in lowered:
        prefix = cleaned[: lowered.index(" was not reported")].strip(" .")
        return _slugify(prefix), "not reported"
    if " remains inconsistent" in lowered:
        prefix = cleaned[: lowered.index(" remains inconsistent")].strip(" .")
        return _slugify(prefix), "inconsistent"
    return _slugify(cleaned[:80]), cleaned


def _object_type_from_value(value: str) -> str | None:
    if not value:
        return None
    if value.startswith("HCM_System_"):
        return "HCM_System"
    if value.endswith("_Gap") or value.endswith("Gap"):
        return "Gap"
    return None


def _collect_anchor_tokens(provenance: Provenance) -> set[str]:
    tokens: set[str] = set()
    tokens.add(provenance.source_id.lower())
    tokens.add(provenance.source_anchor.lower())
    return tokens


def _related_assertion_ids(
    note: str,
    provenance: Provenance,
    metadata_assertions: list[MetadataAssertion],
    evidence_assertions: list[EvidenceAssertion],
    mapping_assertions: list[MappingAssertion],
    graph_assertions: list[GraphAssertion],
) -> list[str]:
    note_text = (note or "").lower()
    note_anchor_tokens = _collect_anchor_tokens(provenance)
    related: list[str] = []

    for assertion in metadata_assertions:
        if assertion.field in note_text or note_anchor_tokens & _collect_anchor_tokens(assertion.provenance):
            related.append(assertion.assertion_id)
    for assertion in evidence_assertions:
        if note_anchor_tokens & _collect_anchor_tokens(assertion.provenance):
            related.append(assertion.assertion_id)
    for assertion in mapping_assertions:
        if assertion.local_field in note_text or assertion.target_field in note_text:
            related.append(assertion.assertion_id)
            continue
        if note_anchor_tokens & _collect_anchor_tokens(assertion.provenance):
            related.append(assertion.assertion_id)
    for assertion in graph_assertions:
        graph_blob = " ".join([assertion.subject, assertion.predicate, assertion.object]).lower()
        if any(token in note_text for token in graph_blob.split()):
            related.append(assertion.assertion_id)
            continue
        if note_anchor_tokens & _collect_anchor_tokens(assertion.provenance):
            related.append(assertion.assertion_id)

    return _unique(related)


def export_hcm_mapppp_bundle(
    config_path: str | Path,
    draft_path: str | Path,
    traceability_matrix_path: str | Path,
    review_notes_path: str | Path | None = None,
    exported_at: datetime | None = None,
    registry_path: str | Path | None = None,
    suggest_hcmo: bool = False,
    coverage_path: str | Path | None = None,
) -> MAPPPPBundle:
    """Build a canonical MAPPPP v0.1.0 bundle from existing HCM swarm artifacts."""
    config_path = Path(config_path)
    draft_path = Path(draft_path)
    traceability_matrix_path = Path(traceability_matrix_path)
    review_notes_path = Path(review_notes_path) if review_notes_path else None

    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    draft_text = draft_path.read_text(encoding="utf-8")
    sections = _split_sections(draft_text)
    references = SourceRegistry(
        Path(registry_path) if registry_path else draft_path.parent / "source_registry.json",
        _parse_references(sections.get("I", "")), draft_text,
    )
    traceability_rows = _load_traceability_rows(traceability_matrix_path)
    for row in traceability_rows:
        stable_source = (row.get("source") or "").strip()
        if stable_source and not stable_source.startswith("["):
            for marker in _extract_citations(row.get("claim", "") + " " + row.get("notes", "")):
                references.bind(marker, stable_source)

    exported_timestamp = exported_at or datetime.now(timezone.utc)
    exported_at_iso = exported_timestamp.isoformat(timespec="seconds")
    swarm = config.get("swarm", {})
    swarm_slug = _slugify(swarm.get("name", "science_agent_squad")) or "science_agent_squad"
    package_id = "pending"

    metadata_assertions: list[MetadataAssertion] = []
    evidence_assertions: list[EvidenceAssertion] = []
    mapping_assertions: list[MappingAssertion] = []
    graph_assertions: list[GraphAssertion] = []
    review_notes: list[ReviewNote] = []

    for index, row in enumerate(traceability_rows, start=1):
        if row.get("status", "").lower() == "superseded":
            continue
        statement = row.get("claim", "").strip()
        if not statement or statement.lower().startswith("matrix initialized"):
            continue
        citations = _extract_citations(statement + " " + row.get("notes", ""))
        provenance = _provenance(
            row.get("source"),
            citations,
            references,
            proposed_by=row.get("agent") or "traceability_matrix",
            created_at=exported_at_iso,
        )
        cleaned_statement = _strip_epistemic_and_citations(statement)
        epistemic_label = _first_epistemic_label(row.get("epistemic_tag", ""))
        evidence_assertions.append(
            EvidenceAssertion(
                assertion_id=f"traceability-evidence-{index}",
                subject=row.get("source") or "reported_study",
                observation=cleaned_statement,
                value=row.get("notes") or cleaned_statement,
                epistemic_label=epistemic_label,
                provenance=provenance,
            )
        )
        if row.get("mnms_category"):
            metadata_assertions.append(
                MetadataAssertion(
                    assertion_id=f"traceability-metadata-{index}",
                    field=_slugify(row.get("mnms_category", "")),
                    value=cleaned_statement,
                    epistemic_label=epistemic_label,
                    provenance=provenance,
                )
            )

    for index, table in enumerate(_extract_markdown_tables(sections.get("C", "")), start=1):
        for row_index, row in enumerate(table, start=1):
            statement = row.get("claim") or row.get("assertion") or row.get("evidence") or ""
            if not statement:
                continue
            notes = row.get("notes") or row.get("method") or ""
            citations = _extract_citations(" ".join([statement, notes]))
            source_id = (row.get("source_anchor") or row.get("source") or
                         row.get("source_id") or row.get("stable_source_id") or row.get("doi"))
            cleaned_statement = _strip_epistemic_and_citations(statement)
            evidence_assertions.append(
                EvidenceAssertion(
                    assertion_id=f"evidence-table-{index}-{row_index}",
                    subject=row.get("evidence_type") or "evidence_observation",
                    observation=cleaned_statement,
                    value=_strip_epistemic_and_citations(notes) or cleaned_statement,
                    epistemic_label=_first_epistemic_label(statement, notes),
                    provenance=_provenance(
                        source_id,
                        citations,
                        references,
                        proposed_by="journalist_evidence_table",
                        created_at=exported_at_iso,
                    ),
                )
            )

    for index, table in enumerate(_extract_markdown_tables(sections.get("D", "")), start=1):
        for row_index, row in enumerate(table, start=1):
            notes = row.get("notes") or ""
            citations = _extract_citations(" ".join(filter(None, [notes, row.get("source", "")])))
            epistemic_label = (
                "uncertain"
                if (row.get("value_or_status") or "").strip().lower() == "missing"
                else _first_epistemic_label(notes)
            )
            target_schema = row.get("target_schema") or "MNMS"
            target_field = row.get("target_field") or _slugify(row.get("mnms_category", ""))
            if target_field:
                mapping_assertions.append(
                    MappingAssertion(
                        assertion_id=f"mapping-assertion-{index}-{row_index}",
                        local_field=_slugify(row.get("metadata_field", "")),
                        target_schema=target_schema,
                        target_field=target_field,
                        mapping_rationale=" ".join(part for part in [
                            row.get("classification"), row.get("rationale"),
                            _strip_epistemic_and_citations(notes),
                        ] if part) or "Candidate mapping; source and term review required",
                        confidence=_mapping_confidence(row.get("classification"), row.get("value_or_status")),
                        epistemic_label=epistemic_label,
                        provenance=_provenance(
                            row.get("source") or row.get("source_id"),
                            citations,
                            references,
                            proposed_by=HCM_METADATA_PERSONA,
                            created_at=exported_at_iso,
                        ),
                    )
                )
            if row.get("value_or_status"):
                metadata_assertions.append(
                    MetadataAssertion(
                        assertion_id=f"mapping-metadata-{index}-{row_index}",
                        field=_slugify(row.get("metadata_field", "")),
                        value=str(row["value_or_status"]),
                        epistemic_label=epistemic_label,
                        provenance=_provenance(
                            row.get("source") or row.get("source_id"),
                            citations,
                            references,
                            proposed_by=HCM_METADATA_PERSONA,
                            created_at=exported_at_iso,
                        ),
                    )
                )

    for index, bullet in enumerate(_extract_bullets(sections.get("E", "")), start=1):
        if not any(tag in bullet for tag in ("[MISSING]", "[CONTESTED]", "[MISSING SOURCE]")):
            continue
        citations = _extract_citations(bullet)
        pmid_anchor = re.search(r"\bPMID:\d+\b", bullet)
        field, value = _metadata_gap_field_and_value(bullet)
        metadata_assertions.append(
            MetadataAssertion(
                assertion_id=f"metadata-gap-{index}",
                field=field,
                value=value,
                epistemic_label=_first_epistemic_label(bullet),
                provenance=_provenance(
                    pmid_anchor.group(0) if pmid_anchor else None,
                    citations,
                    references,
                    proposed_by=HCM_METADATA_PERSONA,
                    created_at=exported_at_iso,
                ),
            )
        )

    for index, table in enumerate(_extract_markdown_tables(sections.get("F", "")), start=1):
        for row_index, row in enumerate(table, start=1):
            if not {"subject", "predicate", "object"}.issubset(row.keys()):
                continue
            notes = row.get("notes") or ""
            citations = _extract_citations(" ".join(filter(None, [notes, row.get("source_id", "")])))
            graph_assertions.append(
                GraphAssertion(
                    assertion_id=f"graph-assertion-{index}-{row_index}",
                    subject=row["subject"],
                    predicate=row["predicate"],
                    object=row["object"],
                    object_type=_object_type_from_value(row["object"]),
                    epistemic_label=_first_epistemic_label(row.get("evidence_level", ""), notes),
                    provenance=_provenance(
                        row.get("source_id"),
                        citations,
                        references,
                        proposed_by="journalist_graph_schema",
                        created_at=exported_at_iso,
                    ),
                )
            )

    for index, table in enumerate(_extract_markdown_tables(sections.get("G", "")), start=1):
        for row_index, row in enumerate(table, start=1):
            note = row.get("description") or row.get("note") or ""
            if not note:
                continue
            citations = _extract_citations(note)
            provenance = _provenance(
                None,
                citations,
                references,
                proposed_by=HCM_RISK_PERSONA,
                created_at=exported_at_iso,
            )
            review_notes.append(
                ReviewNote(
                    note_id=f"risk-note-{index}-{row_index}",
                    disposition=_normalise_review_disposition("risk_audit", note),
                    note=_strip_epistemic_and_citations(note),
                    provenance=provenance,
                    related_assertion_ids=_related_assertion_ids(
                        note,
                        provenance,
                        metadata_assertions,
                        evidence_assertions,
                        mapping_assertions,
                        graph_assertions,
                    ),
                )
            )

    for index, payload in enumerate(_load_review_note_payload(review_notes_path), start=1):
        note = str(payload.get("note", "")).strip()
        if not note:
            continue
        citations = _unique(
            [str(citation) for citation in payload.get("citations", []) if citation]
            + _extract_citations(note)
        )
        provenance = _provenance(
            payload.get("source_anchor"),
            citations,
            references,
            proposed_by=str(payload.get("persona", "Reviewer-2")),
            created_at=exported_at_iso,
        )
        review_notes.append(
            ReviewNote(
                note_id=f"external-review-note-{index}",
                disposition=_normalise_review_disposition(str(payload.get("note_type", "persona_review")), note),
                note=_strip_epistemic_and_citations(note),
                provenance=provenance,
                related_assertion_ids=_related_assertion_ids(
                    note,
                    provenance,
                    metadata_assertions,
                    evidence_assertions,
                    mapping_assertions,
                    graph_assertions,
                ),
            )
        )

    coverage = None
    if suggest_hcmo:
        coverage = suggest_hcmo_mappings([item.model_dump(mode="json") for item in metadata_assertions])
        mapping_assertions.extend(MappingAssertion.model_validate(item) for item in coverage["mappings"])
    reported_species = set()
    for item in metadata_assertions:
        if item.field != "species" or item.provenance.source_id == references.report_id:
            continue
        match = re.fullmatch(r"(?:species:\s*)?(mouse|mice|rat|rats)", str(item.value).strip(), re.I)
        if match:
            reported_species.add({"mice": "mouse", "rats": "rat"}.get(match.group(1).lower(), match.group(1).lower()))
    study_context = StudyContext(species=next(iter(reported_species))) if len(reported_species) == 1 else None

    bundle = MAPPPPBundle(
        package_metadata=PackageMetadata(
            package_id=package_id,
            created_at=exported_at_iso,
            created_by="automation.exporters.mapppp.export_hcm_mapppp_bundle",
        ),
        study_context=study_context,
        sources=sorted([*references.sources.values(), *([HCMO_SOURCE] if suggest_hcmo else [])],
                       key=lambda source: source["source_id"]),
        metadata_assertions=metadata_assertions,
        evidence_assertions=evidence_assertions,
        mapping_assertions=mapping_assertions,
        graph_assertions=graph_assertions,
        review_notes=review_notes,
    )
    identity = content_package_id(bundle.model_dump(mode="json", exclude_none=True), swarm_slug)
    stable_created_at = references.created_at(identity, exported_at_iso)
    bundle.package_metadata.package_id = identity
    bundle.package_metadata.created_at = stable_created_at
    for family in (bundle.metadata_assertions, bundle.evidence_assertions, bundle.mapping_assertions,
                   bundle.graph_assertions, bundle.review_notes):
        for item in family:
            item.provenance.created_at = stable_created_at
    references.save()
    if coverage_path and coverage:
        target = Path(coverage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(coverage, indent=2), encoding="utf-8")
    return bundle
