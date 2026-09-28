#!/usr/bin/env python3
"""Export a human-readable claim-citation-chunk-entailment audit trail."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def index_entailment(payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if payload is None:
        return {}
    return {
        pair["pair_id"]: pair
        for record in payload.get("records", [])
        for pair in record.get("claim_citation_pairs", [])
    }


def build_rows(
    retrieval: dict[str, Any], entailment: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    judgments = index_entailment(entailment)
    rows: list[dict[str, Any]] = []
    for record in retrieval.get("records", []):
        pages = {page["citation_id"]: page for page in record.get("pages", [])}
        for pair in record.get("claim_citation_pairs", []):
            page = pages.get(pair["citation_id"], {})
            judgment = judgments.get(pair["pair_id"])
            rows.append(
                {
                    "dataset_id": record["dataset_id"],
                    "run": record.get("run"),
                    "question": record["question"],
                    "pair_id": pair["pair_id"],
                    "claim_id": pair["claim_id"],
                    "claim_text": pair["claim_text"],
                    "citation_id": pair["citation_id"],
                    "citation_title": page.get("citation_title"),
                    "citation_url": pair["citation_url"],
                    "final_url": page.get("final_url"),
                    "access_method": page.get("access_method"),
                    "page_retrieval_status": pair["page_retrieval_status"],
                    "entailment_label": (
                        judgment.get("entailment_label") if judgment else "pending"
                    ),
                    "confidence": judgment.get("confidence") if judgment else None,
                    "reason": judgment.get("reason") if judgment else None,
                    "evidence": judgment.get("evidence", []) if judgment else [],
                    "warnings": judgment.get("warnings", []) if judgment else [],
                    "top_chunks": pair.get("top_chunks", []),
                }
            )
    return rows


def clean_inline(value: Any) -> str:
    return " ".join(str(value or "").split())


def markdown_text(rows: list[dict[str, Any]], retrieval_path: Path, entailment_path: Path | None) -> str:
    labels = Counter(row["entailment_label"] for row in rows)
    lines = [
        "# Citation entailment audit",
        "",
        f"- Retrieval input: `{retrieval_path}`",
        f"- Entailment input: `{entailment_path}`" if entailment_path else "- Entailment input: not supplied",
        f"- Claim-citation pairs: {len(rows)}",
        "- Labels: " + ", ".join(f"{label}={count}" for label, count in sorted(labels.items())),
        "",
        "`pending` means that passages were retrieved but no matching LLM judgment was supplied.",
        "",
    ]
    current_record: tuple[str, Any] | None = None
    for row in rows:
        record_key = (row["dataset_id"], row["run"])
        if record_key != current_record:
            current_record = record_key
            lines.extend(
                [
                    f"## {row['dataset_id']} (run {row['run']})",
                    "",
                    f"**Question:** {clean_inline(row['question'])}",
                    "",
                ]
            )
        lines.extend(
            [
                f"### {row['pair_id']}",
                "",
                f"- Claim: {clean_inline(row['claim_text'])}",
                f"- Citation: [{clean_inline(row['citation_title']) or row['citation_id']}]({row['citation_url']})",
                f"- Retrieval: `{row['page_retrieval_status']}`; access: `{row['access_method'] or 'n/a'}`",
                f"- LLM judgment: `{row['entailment_label']}`; confidence: `{row['confidence'] or 'n/a'}`",
                f"- LLM reason: {clean_inline(row['reason']) or 'Not yet judged.'}",
                "",
            ]
        )
        if row["evidence"]:
            lines.append("**Evidence quotes selected by the LLM**")
            lines.append("")
            for evidence in row["evidence"]:
                lines.append(
                    f"- `{evidence.get('chunk_id', '')}`: {clean_inline(evidence.get('quote'))}"
                )
            lines.append("")
        if row["warnings"]:
            lines.append("**Validation warnings**")
            lines.append("")
            lines.extend(f"- {clean_inline(warning)}" for warning in row["warnings"])
            lines.append("")
        lines.append("**Passages supplied to the LLM**")
        lines.append("")
        if not row["top_chunks"]:
            lines.extend(["No usable passage was retrieved.", ""])
        for chunk in row["top_chunks"]:
            heading = clean_inline(chunk.get("heading")) or "(no heading)"
            lines.extend(
                [
                    f"#### Rank {chunk.get('rank')} — `{chunk.get('chunk_id')}` — {heading}",
                    "",
                    f"Retrieval score: `{chunk.get('retrieval_score')}`",
                    "",
                    clean_inline(chunk.get("text")),
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "dataset_id",
        "run",
        "question",
        "pair_id",
        "claim_id",
        "claim_text",
        "citation_id",
        "citation_title",
        "citation_url",
        "final_url",
        "access_method",
        "page_retrieval_status",
        "entailment_label",
        "confidence",
        "reason",
        "evidence",
        "warnings",
        "top_chunks",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            output = dict(row)
            for field in ("evidence", "warnings", "top_chunks"):
                output[field] = json.dumps(output[field], ensure_ascii=False)
            writer.writerow(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retrieval_json", type=Path)
    parser.add_argument("output_markdown", type=Path)
    parser.add_argument("--entailment-json", type=Path)
    parser.add_argument("--output-csv", type=Path)
    args = parser.parse_args()

    retrieval = load_json(args.retrieval_json)
    entailment = load_json(args.entailment_json) if args.entailment_json else None
    rows = build_rows(retrieval, entailment)

    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.write_text(
        markdown_text(rows, args.retrieval_json, args.entailment_json), encoding="utf-8"
    )
    if args.output_csv:
        write_csv(args.output_csv, rows)
    print(f"Exported {len(rows)} claim-citation pairs to {args.output_markdown}")
    if args.output_csv:
        print(f"CSV: {args.output_csv}")


if __name__ == "__main__":
    main()
