"""Offline, deterministic ORAtlas export of claims present in a traced final report."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

CONTRACT_COMMIT = "8fba7b16f4ca2c09d21d2be5ca1bfb94dd03b0fc"
SCHEMA = Path(__file__).with_name("schemas") / "review-manifest.schema.json"
CLAIM_TYPES = ["empirical", "mechanistic", "methodological", "theoretical", "normative", "summary",
               "synthesis", "model-derived", "translational", "study-design", "resource-capability", "other"]
RELATION_TYPES = ["supports", "partially-supports", "contradicts", "contextualizes", "method-source", "background", "unclear"]
TAGS = re.compile(r"\[(?:FACT|INFERENCE|SPECULATION|MISSING(?: SOURCE)?|PLATFORM-SPECIFIC|CONTESTED)\]")
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"|]+", re.I)
PMID = re.compile(r"(?:\bPMID\s*:?\s*|pubmed\.ncbi\.nlm\.nih\.gov/)(\d{1,9})\b", re.I)


class CitationIdentityError(ValueError):
    """Conflicting source identities must abort rather than produce partial evidence."""


def schema_with_ai_synthesis_compatibility_override() -> dict:
    """Return the unmodified upstream schema plus the one agreed runtime compatibility fix."""
    schema = copy.deepcopy(json.loads(SCHEMA.read_text(encoding="utf-8")))
    # Upstream fix: https://github.com/dhuzard/oratlas/pull/244
    # reviewTypeSchema in packages/contracts/src/enums.ts already accepts ai-synthesis.
    # TODO: remove this override once the corrected JSON Schema is released.
    enum = schema["properties"]["review"]["properties"]["reviewType"]["enum"]
    if "ai-synthesis" not in enum:
        enum.append("ai-synthesis")
    return schema


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def stable_id(prefix: str, value: object) -> str:
    return prefix + "-" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:24]


def normalize_doi(value: str) -> str:
    value = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", value.strip(), flags=re.I).lower()
    if not re.fullmatch(r"10\.\d{4,9}/\S+", value):
        raise ValueError(f"Invalid DOI: {value}")
    if value.startswith("10.5555/"):
        raise ValueError(f"Example DOI must not be exported: {value}")
    return value


def safe_path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or len(name) > 512 or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*", name
    ):
        raise ValueError(f"Unsafe repository-relative path: {name!r}")
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Artifact escapes output root: {name}")
    return target


def _string(maximum: int, minimum: int = 0) -> dict:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def _record_schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


RECORD_SCHEMAS = {
    "claims": _record_schema({"id": _string(120, 1), "text": _string(5000, 1), "section": _string(300),
                              "anchor": _string(300), "claimType": {"enum": CLAIM_TYPES},
                              "qualification": _string(2000)}, ["id", "text"]),
    "citations": _record_schema({"id": _string(120, 1), "doi": {"type": "string", "pattern": r"^10\.\d{4,9}/\S+$"},
                                 "pmid": {"type": "string", "pattern": r"^\d{1,9}$"}, "openAlexId": _string(60),
                                 "title": _string(1000), "authors": {"type": "array", "maxItems": 200, "items": _string(300)},
                                 "year": {"type": "integer", "minimum": 1500, "maximum": 2100},
                                 "source": _string(500), "url": {**_string(1000), "format": "uri", "pattern": "^https?://"}}, ["id"]),
    "relations": _record_schema({"claimId": _string(120, 1), "citationId": _string(120, 1),
                                 "relationType": {"enum": RELATION_TYPES},
                                 "supportDirection": {"enum": ["positive", "negative", "mixed", "neutral"]}},
                                ["claimId", "citationId", "relationType"]),
}


def validate_records(manifest: dict, records: dict[str, list[dict]]) -> None:
    Draft202012Validator(schema_with_ai_synthesis_compatibility_override(), format_checker=FormatChecker()).validate(manifest)
    url = urlsplit(manifest["repository"]["url"])
    if url.scheme != "https" or not url.hostname:
        raise ValueError("repository.url must be an absolute HTTPS URL")
    if "trustAssessments" in manifest.get("artifacts", {}):
        raise ValueError("TRUST export is not supported")
    for key in ("versionDoi", "conceptDoi"):
        if key in manifest.get("publication", {}):
            doi = manifest["publication"][key]
            if normalize_doi(doi) != doi:
                raise ValueError(f"publication.{key} is not normalized")
    for name in manifest.get("artifacts", {}).values():
        safe_path(Path.cwd(), name)
    for family, schema in RECORD_SCHEMAS.items():
        if len(records[family]) > 5000:
            raise ValueError(f"{family} exceeds ORAtlas's 5000-record ingestion cap")
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        seen = set()
        for record in records[family]:
            validator.validate(record)
            identity = record.get("id") or (record["claimId"], record["citationId"])
            if identity in seen:
                raise ValueError(f"Duplicate {family} identity: {identity}")
            seen.add(identity)
            if "doi" in record and normalize_doi(record["doi"]) != record["doi"]:
                raise ValueError("Citation DOI is not normalized")
    claims = {r["id"] for r in records["claims"]}
    citations = {r["id"] for r in records["citations"]}
    for relation in records["relations"]:
        if relation["claimId"] not in claims or relation["citationId"] not in citations:
            raise ValueError(f"Dangling relation: {relation}")


def check_export(root: Path) -> dict[str, int]:
    def read(name: str) -> str:
        data = safe_path(root, name).read_bytes()
        if not data.endswith(b"\n") or b"\r" in data or data.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"{name}: expected UTF-8, LF and a trailing newline")
        return data.decode("utf-8")

    manifest = json.loads(read("review-manifest.json"))
    # Validate before opening any manifest-selected paths.
    Draft202012Validator(schema_with_ai_synthesis_compatibility_override(), format_checker=FormatChecker()).validate(manifest)
    records = {family: [json.loads(line) for line in read(manifest["artifacts"][family]).splitlines() if line.strip()]
               for family in RECORD_SCHEMAS}
    validate_records(manifest, records)
    if "provenance" in manifest["artifacts"]:
        json.loads(read(manifest["artifacts"]["provenance"]))
    return {family: len(values) for family, values in records.items()}


def _cells(line: str) -> list[str]:
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _tables(text: str) -> list[dict]:
    """Read only explicit table rows, including legacy live-search and HCM matrices."""
    rows, headers = [], []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        names = [re.sub(r"[^a-z0-9]+", "_", c.lower()).strip("_") for c in cells]
        if "claim" in names and ("source" in names or "stable_source_id" in names):
            headers = names
        elif headers and len(cells) == len(headers) and not all(re.fullmatch(r"[-: ]+", c) for c in cells):
            rows.append(dict(zip(headers, cells)))
    return rows


def _plain(text: str) -> str:
    text = TAGS.sub("", text)
    text = re.sub(r"\[[^\]]+\]\(([^)]+)\)", lambda m: m.group(0).split("](")[0][1:], text)
    text = re.sub(r"\[(?:\d+|[A-Za-z][\w-]*-\d+)\]", "", text)
    return normalized(text.replace("**", "").replace("`", "")).strip(" .")


def _report_parts(text: str) -> tuple[list[dict], dict[str, str]]:
    parts, references, section, in_refs = [], {}, "", False
    for line in text.splitlines():
        heading = re.match(r"^#{1,6}\s+(.+)", line)
        if heading:
            section = heading[1]
            in_refs = bool(re.search(r"\b(references|bibliography)\b", section, re.I))
            continue
        if in_refs:
            ref = re.match(r"^\s*(\[\d+\]|\d+\.)\s+(.+)", line)
            if ref:
                references["[" + re.search(r"\d+", ref[1])[0] + "]"] = ref[2]
            continue
        if line.strip():
            parts.append({"raw": line, "section": section, "texts": [_plain(c) for c in _cells(line)]
                          if line.lstrip().startswith("|") else [_plain(line.lstrip("- "))]})
    return parts, references


def _citation_text(source: str) -> str:
    """Replace inline Markdown links with destinations, respecting nested parentheses."""
    link = re.compile(r"!?\[[^\]\n]*\]\(")
    result, start = [], 0
    while match := link.search(source, start):
        depth, end = 1, match.end()
        while end < len(source) and depth:
            if source[end] == "\\":
                end += 2
                continue
            if source[end] == "(":
                depth += 1
            elif source[end] == ")":
                depth -= 1
            end += 1
        if depth:
            break  # An incomplete link is not a reliably delimited destination.
        destination = source[match.end():end - 1].strip()
        if destination.startswith("<") and ">" in destination:
            destination = destination[1:destination.index(">")]
        else:
            destination = destination.split(maxsplit=1)[0] if destination else ""
        destination = re.sub(r"\\([()])", r"\1", destination)
        result.extend((source[start:match.start()], destination))
        start = end
    result.append(source[start:])
    return "".join(result)


def _citation_urls(source: str) -> list[str]:
    """Trim prose punctuation consistently, retaining balanced destination parentheses."""
    urls = []
    for url in re.findall(r"https?://[^\s<>|]+", source):
        url = url.rstrip(".,;")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(".,;")
        urls.append(url)
    return urls


def _report_citations(source: str) -> list[dict]:
    """Read each directly cited identifier independently; proximity is not an alias binding."""
    source = _citation_text(source)
    records = [{"doi": normalize_doi(doi.rstrip(".,;"))} for doi in DOI.findall(source)]
    records.extend({"pmid": str(int(pmid))} for pmid in PMID.findall(source))
    for url in _citation_urls(source):
        if not DOI.search(url) and not PMID.search(url):
            records.append({"url": url})
    return list({canonical(record): record for record in records}.values())


def _citation(source: str) -> dict:
    source = _citation_text(source)
    if len(set(m.lower().rstrip(".,;") for m in DOI.findall(source))) > 1 or len(set(PMID.findall(source))) > 1:
        raise ValueError("ambiguous multi-work source; use separate traceability rows or numbered references")
    doi, pmid = DOI.search(source), PMID.search(source)
    record = {}
    if doi:
        record["doi"] = normalize_doi(doi[0].rstrip(".,;"))
    if pmid:
        record["pmid"] = str(int(pmid[1]))
    urls = _citation_urls(source)
    if urls and not record:
        record["url"] = urls[0]
    if not record:
        raise ValueError("no explicit DOI, PMID or source URL")
    return record


def _deduplicate(candidates: list[dict]) -> tuple[list[dict], list[str]]:
    """Merge DOI/PMID aliases transitively; refuse conflicting identifier associations."""
    groups: list[tuple[dict, set[int]]] = []
    for index, candidate in enumerate(candidates):
        matches = [g for g in groups if any(candidate.get(k) and candidate.get(k) == g[0].get(k)
                                           for k in ("doi", "pmid", "url"))]
        merged, members = dict(candidate), {index}
        for group in matches:
            for key, value in group[0].items():
                if key in ("doi", "pmid") and key in merged and merged[key] != value:
                    raise CitationIdentityError(f"Conflicting citation identifiers: {merged}, {group[0]}")
                merged[key] = min(value, merged[key]) if key in merged else value
            members.update(group[1])
            groups.remove(group)
        groups.append((merged, members))
    records, ids = [], [""] * len(candidates)
    for record, members in groups:
        key = next((k, record[k]) for k in ("doi", "pmid", "url") if k in record)
        record["id"] = stable_id("citation", key)
        records.append(record)
        for index in members:
            ids[index] = record["id"]
    return sorted(records, key=lambda r: r["id"]), ids


def _merge_citation(target: dict, source: dict) -> None:
    for key in ("doi", "pmid"):
        if key in target and key in source and target[key] != source[key]:
            raise CitationIdentityError(f"Conflicting citation identifiers: {target}, {source}")
    target.update(source)


def _one(root: Path, patterns: list[str], label: str) -> Path:
    matches = sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()})
    if len(matches) != 1:
        raise ValueError(f"Expected one {label}, found {len(matches)}; specify its path explicitly")
    return matches[0]


def export_oratlas(run: Path, out: Path, *, repository_url: str, title: str, abstract: str,
                   license: str, language: str = "en", keywords: list[str] | None = None,
                   review_type: str = "ai-synthesis", report: Path | None = None,
                   matrix: Path | None = None, version_doi: str | None = None,
                   concept_doi: str | None = None) -> dict:
    run, out = Path(run), Path(out)
    report = report or _one(run, ["final_report.md", "Drafts/**/final_report.md"], "final report")
    matrix = matrix or _one(run, ["Knowledge_Traceability_Matrix*.md", "traceability.md"], "traceability matrix")
    report_text = report.read_text(encoding="utf-8")
    parts, references = _report_parts(report_text)
    rows = _tables(matrix.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError("No supported traceability rows found")
    claims, pending, candidates, skipped = {}, [], [], []
    for row in rows:
        label = row.get("claim_id") or stable_id("row", row)
        try:
            if row.get("status", "active").lower() not in ("active", "proposed", "accepted"):
                raise ValueError("inactive traceability row")
            text = _plain(row["claim"])
            matches = [part for part in parts if text and text in part["texts"]]
            if not matches:
                raise ValueError("claim text not present verbatim in final report")
            # Bind to a report occurrence that explicitly cites this row's source.
            source = row.get("source", "")
            if re.fullmatch(r"\[\d+\]", source):
                source = references.get(source, source)
            chosen = None
            for part in matches:
                raw = part["raw"]
                cited = [references[m] for m in re.findall(r"\[\d+\]", raw) if m in references]
                report_sources = _report_citations(raw)
                report_sources.extend(_citation(s) for s in cited if DOI.search(s) or PMID.search(s)
                                      or re.search(r"https?://", s))
                row_source = _citation(source)
                if any(any(row_source.get(k) == c.get(k) for k in row_source) for c in report_sources):
                    chosen = (part, row_source, report_sources)
                    break
            if chosen is None:
                raise ValueError("no matching explicit citation at claim occurrence in final report")
            part, citation, report_sources = chosen
            for other in report_sources:
                if any(citation.get(k) and citation.get(k) == other.get(k) for k in ("doi", "pmid", "url")):
                    _merge_citation(citation, other)
            # Enrich only from the report bibliography, never from the network.
            for reference in references.values():
                try:
                    other = _citation(reference)
                except ValueError:
                    continue
                if any(citation.get(k) and citation.get(k) == other.get(k) for k in ("doi", "pmid", "url")):
                    _merge_citation(citation, other)
            section = part["section"]
            cid = stable_id("claim", [normalized(text), normalized(section)])
            anchor = re.sub(r"[^\w\s-]", "", section.lower()).replace(" ", "-")
            claim = {"id": cid, "text": text, "section": section,
                     "anchor": "knowledge/oratlas/report.md#" + anchor}
            qualification = normalized(" ".join(TAGS.findall(row.get("epistemic_tag", "") + " " + part["raw"])))
            if qualification:
                claim["qualification"] = qualification
            if row.get("claim_type"):
                claim["claimType"] = row["claim_type"]
            if cid in claims and claims[cid] != claim:
                raise ValueError("conflicting qualifications for the same report claim")
            claims[cid] = claim
            pending.append({"claimId": cid, "relationType": row.get("relation_type") or "unclear"})
            candidates.append(citation)
        except CitationIdentityError:
            raise
        except ValueError as exc:
            skipped.append({"record": label, "reason": str(exc)})
    citations, ids = _deduplicate(candidates)
    relations = {canonical({**r, "citationId": ids[i]}): {**r, "citationId": ids[i]} for i, r in enumerate(pending)}
    records = {"claims": sorted(claims.values(), key=lambda r: r["id"]), "citations": citations,
               "relations": [relations[k] for k in sorted(relations)]}
    manifest = {"schemaVersion": "1.0.0", "review": {"title": title, "abstract": abstract,
                "reviewType": review_type, "language": language, "keywords": sorted(set(keywords or [])), "license": license},
                "repository": {"url": repository_url},
                "artifacts": {family: f"knowledge/oratlas/{family}.jsonl" for family in records}}
    publication = {key: normalize_doi(value) for key, value in (("versionDoi", version_doi), ("conceptDoi", concept_doi)) if value}
    if publication:
        manifest["publication"] = publication
    provenance = {"contractCommit": CONTRACT_COMMIT, "sources": {}, "skipped": sorted(skipped, key=canonical)}
    # Copy the traceable report and matrix so the published export is auditable.
    files = {"review-manifest.json": canonical(manifest) + "\n"}
    for name, source in (("report.md", report), ("traceability.md", matrix)):
        text = source.read_text(encoding="utf-8").rstrip("\n") + "\n"
        files[f"knowledge/oratlas/{name}"] = text
        provenance["sources"][name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    configs = sorted(run.glob("swarm_config*.y*ml"))
    if len(configs) == 1:
        config = yaml.safe_load(configs[0].read_text(encoding="utf-8"))
        provenance["swarmConfigHash"] = hashlib.sha256(canonical(config).encode("utf-8")).hexdigest()
        provenance["agents"] = sorted(p["name"] for p in config.get("personas", []))
        provenance["model"] = {k: v for k, v in config.get("model", {}).items() if k in ("name", "provider")}
    bundle_path = run / "mapppp_bundle.json"
    if bundle_path.exists():
        metadata = json.loads(bundle_path.read_text(encoding="utf-8")).get("package_metadata", {})
        if metadata.get("created_at"):
            provenance["runTimestamp"] = metadata["created_at"]
    manifest["artifacts"]["provenance"] = "knowledge/oratlas/provenance.json"
    files["review-manifest.json"] = canonical(manifest) + "\n"
    files[manifest["artifacts"]["provenance"]] = canonical(provenance) + "\n"
    for family, values in records.items():
        files[manifest["artifacts"][family]] = "\n".join(canonical(v) for v in values) + "\n"
    validate_records(manifest, records)
    targets = {name: safe_path(out, name) for name in files}
    for name, target in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[name].encode("utf-8"))
    return {"counts": check_export(out), "skipped": provenance["skipped"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Validate existing output without rewriting it")
    for name in ("repository-url", "title", "abstract", "license", "version-doi", "concept-doi"):
        parser.add_argument("--" + name)
    parser.add_argument("--language", default="en")
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--review-type", default="ai-synthesis")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--matrix", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.check:
            result = {"counts": check_export(args.out)}
        else:
            for name in ("run", "repository_url", "title", "abstract", "license"):
                if getattr(args, name) is None:
                    parser.error("--" + name.replace("_", "-") + " is required for export")
            result = export_oratlas(args.run, args.out, repository_url=args.repository_url, title=args.title,
                                    abstract=args.abstract, license=args.license, language=args.language,
                                    keywords=args.keyword, review_type=args.review_type, report=args.report,
                                    matrix=args.matrix, version_doi=args.version_doi, concept_doi=args.concept_doi)
        print(canonical(result))
        return 0
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        print(f"ORAtlas export failed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"ORAtlas validation failed: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
