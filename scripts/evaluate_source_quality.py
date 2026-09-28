#!/usr/bin/env python3
"""Classify the source types in the complete captured AIO citation pool."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from openai import OpenAI
from pydantic import BaseModel, ConfigDict

from extract_medical_claims import load_env_file


PROMPT = """Classify the credibility/type of each medical citation source using only the supplied URL, captured citation title, and cached page metadata. Do not claim to have opened a page or checked credentials unless the metadata demonstrates them. Apply this rubric:
high = government/public-health agency; professional medical society; clinical guideline; systematic review/meta-analysis; or major academic medical institution.
moderate = reliable hospital patient-information page; peer-reviewed primary study; or clearly identified professional organization.
low = commercial health site; private clinic; general media; promotional/drug-company page; individual creator; or a page with no clear professional review.
unclassifiable = supplied information is insufficient to identify the publisher/source type.

Classify the actual page, not merely the hosting domain. A paper on PubMed Central is not a government publication: distinguish reviews from primary studies when the title/metadata establishes this, otherwise use unclassifiable. A YouTube/Facebook page is not automatically low: an identifiable institutional publisher may be high or moderate, but if the creator is unclear use unclassifiable. A reputable hospital's ordinary patient page is moderate; reserve high for major academic medical institutions. Do not infer peer review from an article-like title. Do not classify validity, claim support, or claim-specific appropriateness. Give a short evidence-based reason and set needs_review true when publisher, article type, or label is uncertain. Return exactly one result for every input id."""


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    label: str
    source_type: str
    reason: str
    needs_review: bool


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judgments: list[Judgment]


TRACKING_QUERY_KEYS = {
    "sa", "ved", "usg", "ei", "oq", "sclient", "source", "utm_source",
    "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "gclid",
    "t", "start", "vl", "feature",
}


def identity_url(url: str) -> str:
    parts = urlsplit(url)
    query = sorted((key, value) for key, value in parse_qsl(parts.query) if key.lower() not in TRACKING_QUERY_KEYS)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), parts.path.rstrip("/") or "/", urlencode(query), ""))


def build_inventory(parsed: dict, cache_dir: Path) -> list[dict]:
    cache = {}
    if cache_dir.exists():
        for path in cache_dir.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                cache[identity_url(item.get("requested_url", ""))] = item
            except (ValueError, OSError):
                continue
    grouped = {}
    for record in parsed["records"]:
        if not record.get("aio_present"):
            continue
        for source in record.get("sources", []):
            key = identity_url(source["url"])
            if key not in grouped:
                grouped[key] = {"identity_url": key, "urls": set(), "titles": set(), "domain": source.get("domain", ""), "occurrences": [], "cached_page_title": "", "cached_excerpt": ""}
            row = grouped[key]
            row["urls"].add(source["url"])
            if source.get("title"):
                row["titles"].add(source["title"])
            row["occurrences"].append({"dataset_id": record["dataset_id"], "run": record["run"], "citation_id": source["citation_id"]})
    for key, row in grouped.items():
        page = cache.get(key, {})
        if page.get("retrieval_status") == "success":
            row["cached_page_title"] = page.get("page_title") or ""
            chunks = page.get("chunks") or []
            row["cached_excerpt"] = " ".join(chunk.get("text", "") for chunk in chunks[:2])[:850]
        row["urls"] = sorted(row["urls"])
        row["titles"] = sorted(row["titles"])
    return sorted(grouped.values(), key=lambda item: item["identity_url"])


def classify(client: OpenAI, model: str, rows: list[dict]) -> tuple[list[dict], dict]:
    supplied = [{"id": i, "url": row["identity_url"], "titles": row["titles"][:3], "cached_page_title": row["cached_page_title"], "cached_excerpt": row["cached_excerpt"]} for i, row in enumerate(rows)]
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(supplied, ensure_ascii=False)}],
        response_format={"type": "json_schema", "json_schema": {"name": "source_quality_batch", "strict": True, "schema": Batch.model_json_schema()}},
        extra_body={"provider": {"require_parameters": True}},
    )
    parsed = Batch.model_validate_json(response.choices[0].message.content)
    found = {j.id: j for j in parsed.judgments}
    if set(found) != set(range(len(rows))) or len(parsed.judgments) != len(rows):
        raise ValueError("Incomplete or duplicate source judgments")
    labels = {"high", "moderate", "low", "unclassifiable"}
    if any(j.label not in labels for j in found.values()):
        raise ValueError("Unexpected source-quality label")
    return [found[i].model_dump() for i in range(len(rows))], response.usage.model_dump() if response.usage else {}


def summarize(rows: list[dict], records: list[dict]) -> dict:
    by_key = {r["identity_url"]: r for r in rows if r.get("judgment")}
    def counts_for(sources: list[dict]) -> dict:
        labels = Counter(by_key[identity_url(s["url"])]["final_judgment"]["label"] for s in sources if identity_url(s["url"]) in by_key)
        denominator = sum(labels[k] for k in ("high", "moderate", "low"))
        return {"counts": dict(labels), "classifiable": denominator, "high_quality_source_rate": labels["high"] / denominator if denominator else None}
    by_run = {}
    for run in sorted({r["run"] for r in records}):
        selected = [r for r in records if r["run"] == run and r.get("aio_present")]
        occurrences = [s for r in selected for s in r.get("sources", [])]
        unique = list({identity_url(s["url"]): s for s in occurrences}.values())
        response_rates = []
        for r in selected:
            distinct_sources = list({identity_url(s["url"]): s for s in r.get("sources", [])}.values())
            result = counts_for(distinct_sources)
            if result["high_quality_source_rate"] is not None:
                response_rates.append({"dataset_id": r["dataset_id"], "run": run, **result})
        by_run[str(run)] = {"citation_occurrences": len(occurrences), "unique_source_identities": len(unique), "source_occurrence_distribution": counts_for(occurrences), "unique_source_distribution": counts_for(unique), "response_rates": response_rates}
    return {"source_identity_count": len(rows), "classified_count": len(by_key), "manual_override_count": sum(bool(r.get("manual_override")) for r in rows), "source_identity_distribution": counts_for([{"url": r["identity_url"]} for r in rows]), "by_run": by_run}


def apply_overrides(rows: list[dict], overrides: dict) -> None:
    known = {r["identity_url"] for r in rows}
    if set(overrides) - known:
        raise ValueError("Manual override has no matching source identity")
    for row in rows:
        judgment = row.get("judgment")
        if judgment is None:
            continue
        override = overrides.get(row["identity_url"])
        row["manual_override"] = override
        row["final_judgment"] = {**judgment, **override} if override else judgment


def save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def export_csv(path: Path, rows: list[dict]) -> None:
    fields = ["identity_url", "citation_urls", "captured_titles", "cached_page_title", "domain", "runs", "citation_occurrences", "model_label", "final_label", "source_type", "reason", "needs_review", "manual_override"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            if not row.get("final_judgment"):
                continue
            final = row["final_judgment"]
            writer.writerow({
                "identity_url": row["identity_url"],
                "citation_urls": " | ".join(row["urls"]),
                "captured_titles": " | ".join(row["titles"]),
                "cached_page_title": row["cached_page_title"],
                "domain": row["domain"],
                "runs": ",".join(map(str, sorted({o["run"] for o in row["occurrences"]}))),
                "citation_occurrences": len(row["occurrences"]),
                "model_label": row["judgment"]["label"],
                "final_label": final["label"],
                "source_type": final["source_type"],
                "reason": final["reason"],
                "needs_review": final["needs_review"],
                "manual_override": bool(row.get("manual_override")),
            })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path("outputs/healthsearchqa_pilot/evaluation/citation_cache/clean"))
    parser.add_argument("--env-file", type=Path, default=Path(".openrouter_env"))
    parser.add_argument("--model", default="openai/gpt-5-mini")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit-batches", type=int)
    parser.add_argument("--overrides", type=Path, default=Path("scripts/source_quality_overrides.json"))
    args = parser.parse_args()
    parsed = json.loads(args.input.read_text(encoding="utf-8"))
    overrides = json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides.exists() else {}
    rows = build_inventory(parsed, args.cache_dir)
    previous_batches = []
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("model") != args.model:
            raise SystemExit("Existing output uses a different model")
        previous_batches = previous.get("batches", [])
        old = {r["identity_url"]: r.get("judgment") for r in previous.get("sources", [])}
        for row in rows:
            if old.get(row["identity_url"]):
                row["judgment"] = old[row["identity_url"]]
    output = {"schema_version": "0.1.0", "created_at": datetime.now(timezone.utc).isoformat(), "method": "OpenRouter source-type classification from captured metadata and available local page cache; not claim-specific", "model": args.model, "sources": rows, "batches": previous_batches}
    load_env_file(args.env_file)
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])
    pending = [r for r in rows if not r.get("judgment")]
    batches = [pending[i:i+args.batch_size] for i in range(0, len(pending), args.batch_size)]
    if args.limit_batches is not None:
        batches = batches[:args.limit_batches]
    if batches:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(classify, client, args.model, group): group for group in batches}
            for future in as_completed(futures):
                group = futures[future]
                judgments, usage = future.result()
                for row, judgment in zip(group, judgments):
                    row["judgment"] = {key: value for key, value in judgment.items() if key != "id"}
                apply_overrides(rows, overrides)
                output["batches"].append({"size": len(group), "usage": usage})
                output["summary"] = summarize(rows, parsed["records"])
                save(args.output, output)
                print(f"completed {len(output['batches'])} batches; classified {output['summary']['classified_count']}/{len(rows)}", flush=True)
    if not batches:
        apply_overrides(rows, overrides)
        output["summary"] = summarize(rows, parsed["records"])
        save(args.output, output)
    export_csv(args.output.with_suffix(".csv"), rows)


if __name__ == "__main__":
    main()
