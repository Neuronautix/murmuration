"""Producer-side source identity and retry bookkeeping; OntoHistoCurator validates MAPPPP."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def package_id(payload: dict, prefix: str = "hcm") -> str:
    canonical = json.loads(json.dumps(payload))
    canonical.pop("package_metadata", None)
    for family in ("metadata_assertions", "evidence_assertions", "mapping_assertions", "graph_assertions", "review_notes"):
        for item in canonical.get(family, []):
            item.get("provenance", {}).pop("created_at", None)
    return f"{prefix}-mapppp-sha256-{digest(canonical)}"


class SourceRegistry:
    """Persist stable source records; numbered citations are local to each draft."""

    def __init__(self, path: Path, references: dict[str, str], draft_text: str):
        self.path = path
        self.references = references
        self.bindings: dict[str, str] = {}
        self.sources: dict[str, dict] = {}
        self.packages: dict[str, str] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.sources = data.get("sources", {})
            self.packages = data.get("packages", {})
        self.report_id = f"report:sha256:{digest(draft_text)}"
        self._register(self.report_id, "note", "HCM draft")

    def _register(self, source_id: str, source_type: str, title: str | None = None) -> str:
        record = {"source_id": source_id, "source_type": source_type}
        if title:
            record["title"] = title
        prior = self.sources.get(source_id)
        if prior and (prior["source_type"] != source_type or
                      (prior.get("title") and title and prior["title"] != title)):
            raise ValueError(f"Source ID conflict: {source_id}")
        if prior and prior.get("title") and not title:
            record["title"] = prior["title"]
        self.sources[source_id] = record
        return source_id

    def bind(self, marker: str, source_id: str) -> None:
        if marker not in self.references:
            raise ValueError(f"Unresolved citation reference: {marker}")
        prior = self.bindings.get(marker)
        if prior and prior != source_id:
            raise ValueError(f"Citation {marker} has conflicting source IDs")
        self.bindings[marker] = source_id

    def resolve(self, explicit: str | None, citations: list[str]) -> tuple[str, str]:
        explicit = (explicit or "").strip()
        if re.fullmatch(r"\[\d+\]", explicit):
            citations = [explicit, *citations]
            explicit = ""
        resolved = []
        for marker in citations:
            reference = self.references.get(marker)
            if not reference:
                raise ValueError(f"Unresolved citation reference: {marker}")
            doi = re.search(r"\b10\.\d{4,9}/[^\s;,]+", reference, re.I)
            pmid = re.search(r"\bPMID[:\s]+(\d+)\b", reference, re.I)
            sid = self.bindings.get(marker) or (f"DOI:{doi.group(0).rstrip('.')}" if doi else
                   f"PMID:{pmid.group(1)}" if pmid else
                   f"reference:sha256:{digest(reference.strip())}")
            resolved.append((sid, reference))
        if explicit:
            sid = explicit
            if resolved and any(other != sid for other, _ in resolved if other.startswith(("DOI:", "PMID:"))):
                raise ValueError(f"Citation and source ID disagree: {explicit}")
            title = resolved[0][1] if resolved else None
            self._register(sid, "paper" if sid.startswith(("DOI:", "PMID:")) else "other", title)
            return sid, sid
        if resolved:
            if len({sid for sid, _ in resolved}) != 1:
                raise ValueError(f"Multiple sources in one provenance: {citations}")
            sid, reference = resolved[0]
            self._register(sid, "paper", reference)
            return sid, f"reference:sha256:{digest(reference.strip())}"
        return self.report_id, "draft"

    def created_at(self, identity: str, proposed: str) -> str:
        self.packages.setdefault(identity, proposed)
        return self.packages[identity]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"sources": self.sources, "packages": self.packages},
                                        indent=2, sort_keys=True), encoding="utf-8")
