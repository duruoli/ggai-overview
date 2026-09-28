#!/usr/bin/env python3
"""Parse captured Google AI Overviews into text blocks and local citations."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from bs4 import BeautifulSoup, Tag


SCHEMA_VERSION = "0.1.0"
BLOCK_SELECTOR = ".n6owBd, .otQkpb, li"
CITATION_UI_SELECTOR = ".WBgIic, .DHPVt, button.vDOt8c"
SPACE_RE = re.compile(r"\s+")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?])")
ADDITIONAL_SOURCES_RE = re.compile(r"\(\+(\d+)\)")


def normalize_text(value: str) -> str:
    """Collapse DOM whitespace without changing wording."""
    value = SPACE_RE.sub(" ", value).strip()
    return SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", value)


def canonical_url(value: str) -> str:
    """Normalize a URL enough to match local anchors to the flat source list."""
    value = value.strip()
    if not value:
        return ""
    value, _fragment = urldefrag(value)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return value


def citation_label(anchor: Tag) -> str:
    label = normalize_text(anchor.get("aria-label", ""))
    if label:
        return label
    visible = normalize_text(anchor.get_text(" ", strip=True))
    if visible:
        return visible
    parent = anchor.parent
    if isinstance(parent, Tag):
        button = parent.find("button", attrs={"aria-label": True})
        if isinstance(button, Tag):
            return normalize_text(button.get("aria-label", ""))
    return ""


def block_kind(node: Tag) -> str:
    classes = set(node.get("class", []))
    if "otQkpb" in classes or node.get("role") == "heading":
        return "heading"
    if node.name == "li":
        return "list_item"
    return "paragraph"


def block_text(node: Tag) -> str:
    """Extract answer text while excluding citation widgets and nested list items."""
    clone_soup = BeautifulSoup(str(node), "html.parser")
    clone = clone_soup.find()
    if not isinstance(clone, Tag):
        return ""

    if clone.name == "li":
        for nested_list in clone.find_all(["ul", "ol"]):
            nested_list.decompose()

    for citation_ui in clone.select(CITATION_UI_SELECTOR):
        citation_ui.decompose()

    return normalize_text(clone.get_text(" ", strip=True))


def local_anchor_data(node: Tag) -> tuple[list[dict[str, Any]], int, int]:
    """Return visible local links and unresolved/hidden citation marker counts."""
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    additional_sources = 0

    for anchor in node.find_all("a", href=True):
        url = canonical_url(anchor.get("href", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        label = citation_label(anchor)
        button = anchor.find_next("button", attrs={"aria-label": True})
        button_label = normalize_text(button.get("aria-label", "")) if isinstance(button, Tag) else ""
        marker_text = " ".join(filter(None, [label, button_label]))
        match = ADDITIONAL_SOURCES_RE.search(marker_text)
        indicated = int(match.group(1)) if match else 0
        additional_sources += indicated
        classes = set(anchor.get("class", []))
        anchors.append(
            {
                "url": url,
                "label": label,
                "anchor_kind": "citation_marker" if "PMDqCb" in classes else "inline_link",
                "additional_sources_indicated": indicated,
            }
        )

    marker_buttons = node.select("button.vDOt8c")
    unresolved_markers = 0
    for button in marker_buttons:
        label = normalize_text(button.get("aria-label", ""))
        if "Related results" not in label:
            continue
        wrapper = button.find_parent()
        has_anchor = isinstance(wrapper, Tag) and wrapper.find("a", href=True) is not None
        if not has_anchor:
            unresolved_markers += 1
        match = ADDITIONAL_SOURCES_RE.search(label)
        if match and not has_anchor:
            additional_sources += int(match.group(1))

    return anchors, unresolved_markers, additional_sources


def source_catalog(record: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    catalog: list[dict[str, Any]] = []
    url_to_id: dict[str, str] = {}
    for citation in record.get("citations", []):
        url = canonical_url(citation.get("url", ""))
        if not url or url in url_to_id:
            continue
        citation_id = f"src_{len(catalog) + 1:03d}"
        url_to_id[url] = citation_id
        catalog.append(
            {
                "citation_id": citation_id,
                "position": citation.get("position"),
                "title": citation.get("title", ""),
                "url": url,
                "domain": citation.get("domain", urlparse(url).netloc),
                "mapped_block_ids": [],
                "candidate_block_ids": [],
                "catalog_origin": "captured_citation_list",
            }
        )
    return catalog, url_to_id


def add_local_source(
    anchor: dict[str, Any],
    catalog: list[dict[str, Any]],
    url_to_id: dict[str, str],
) -> str:
    url = anchor["url"]
    if url in url_to_id:
        return url_to_id[url]
    citation_id = f"src_{len(catalog) + 1:03d}"
    url_to_id[url] = citation_id
    catalog.append(
        {
            "citation_id": citation_id,
            "position": None,
            "title": anchor.get("label", ""),
            "url": url,
            "domain": urlparse(url).netloc,
            "mapped_block_ids": [],
            "candidate_block_ids": [],
            "catalog_origin": "local_html_anchor",
        }
    )
    return citation_id


def parse_record(record: dict[str, Any]) -> dict[str, Any]:
    base = {
        "dataset_id": record.get("dataset_id"),
        "sample_order": record.get("sample_order"),
        "run": record.get("run"),
        "question": record.get("question", ""),
        "aio_present": bool(record.get("ai_overview_present")),
    }
    if not base["aio_present"]:
        return {
            **base,
            "parse_status": "not_present",
            "main_text": "",
            "blocks": [],
            "sources": [],
            "diagnostics": {
                "block_count": 0,
                "locally_mapped_source_count": 0,
                "unmapped_source_count": 0,
                "unresolved_citation_marker_count": 0,
                "additional_sources_indicated": 0,
            },
        }

    html = record.get("aio_html_sanitized", "")
    if not html:
        return {**base, "parse_status": "missing_html", "main_text": "", "blocks": [], "sources": []}

    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one('[data-container-id="main-col"]')
    used_fallback = False
    if main is None:
        main = soup
        used_fallback = True

    catalog, url_to_id = source_catalog(record)
    blocks: list[dict[str, Any]] = []
    list_group_ids: dict[int, str] = {}
    unresolved_total = 0
    additional_total = 0

    for node in main.select(BLOCK_SELECTOR):
        if not isinstance(node, Tag):
            continue
        # A paragraph or heading inside a list item would otherwise be duplicated.
        if node.name != "li" and node.find_parent("li") is not None:
            continue
        text = block_text(node)
        if not text or text in {"AI Overview", "AI Mode replied:"} or text.startswith("AI Mode reply for "):
            continue

        block_id = f"b{len(blocks) + 1:03d}"
        list_ancestor = node.find_parent(["ul", "ol"])
        list_group_id = None
        if isinstance(list_ancestor, Tag):
            list_key = id(list_ancestor)
            if list_key not in list_group_ids:
                list_group_ids[list_key] = f"list_{len(list_group_ids) + 1:03d}"
            list_group_id = list_group_ids[list_key]
        anchors, unresolved, additional = local_anchor_data(node)
        citation_ids: list[str] = []
        for anchor in anchors:
            citation_id = add_local_source(anchor, catalog, url_to_id)
            if citation_id not in citation_ids:
                citation_ids.append(citation_id)
            source = next(item for item in catalog if item["citation_id"] == citation_id)
            if block_id not in source["mapped_block_ids"]:
                source["mapped_block_ids"].append(block_id)

        unresolved_total += unresolved
        additional_total += additional
        blocks.append(
            {
                "block_id": block_id,
                "block_type": block_kind(node),
                "list_group_id": list_group_id,
                "text": text,
                "local_citation_ids": citation_ids,
                "candidate_citation_ids": [],
                "local_citation_marker_count": len(anchors),
                "unresolved_citation_marker_count": unresolved,
                "additional_sources_indicated": additional,
            }
        )

    if not blocks:
        fallback_text = normalize_text(main.get_text(" ", strip=True))
        if fallback_text:
            blocks.append(
                {
                    "block_id": "b001",
                    "block_type": "paragraph",
                    "list_group_id": None,
                    "text": fallback_text,
                    "local_citation_ids": [],
                    "candidate_citation_ids": [],
                    "local_citation_marker_count": 0,
                    "unresolved_citation_marker_count": 0,
                    "additional_sources_indicated": 0,
                }
            )
            used_fallback = True

    group_citations: dict[str, list[str]] = {}
    for block in blocks:
        group_id = block.get("list_group_id")
        if not group_id:
            continue
        group_citations.setdefault(group_id, [])
        for citation_id in block["local_citation_ids"]:
            if citation_id not in group_citations[group_id]:
                group_citations[group_id].append(citation_id)

    for block in blocks:
        local_ids = list(block["local_citation_ids"])
        group_ids = group_citations.get(block.get("list_group_id"), [])
        candidate_ids = local_ids + [item for item in group_ids if item not in local_ids]
        block["candidate_citation_ids"] = candidate_ids
        block["citation_scope"] = (
            "local_and_same_list"
            if local_ids and len(candidate_ids) > len(local_ids)
            else "local"
            if local_ids
            else "same_list_inferred"
            if candidate_ids
            else "none"
        )
        for citation_id in candidate_ids:
            source = next(item for item in catalog if item["citation_id"] == citation_id)
            if block["block_id"] not in source["candidate_block_ids"]:
                source["candidate_block_ids"].append(block["block_id"])

    content_blocks = [block for block in blocks if block["block_type"] != "heading"]
    mapped = sum(1 for source in catalog if source["mapped_block_ids"])
    return {
        **base,
        "parse_status": "fallback" if used_fallback else "success",
        "main_text": "\n".join(block["text"] for block in content_blocks),
        "blocks": blocks,
        "sources": catalog,
        "diagnostics": {
            "block_count": len(blocks),
            "content_block_count": len(content_blocks),
            "locally_mapped_source_count": mapped,
            "unmapped_source_count": len(catalog) - mapped,
            "unresolved_citation_marker_count": unresolved_total,
            "additional_sources_indicated": additional_total,
            "used_fallback": used_fallback,
        },
    }


def parse_capture(input_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    records = [parse_record(record) for record in payload.get("records", [])]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_capture": str(input_path),
        "record_count": len(records),
        "aio_present_count": sum(record["aio_present"] for record in records),
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Captured AIO JSON export")
    parser.add_argument("output", type=Path, help="Parsed JSON output")
    args = parser.parse_args()

    result = parse_capture(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Parsed {result['record_count']} records; "
        f"{result['aio_present_count']} contained an AI Overview. Output: {args.output}"
    )


if __name__ == "__main__":
    main()
