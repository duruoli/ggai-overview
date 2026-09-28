#!/usr/bin/env python3
"""Judge whether retrieved citation passages support medical claims."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from extract_medical_claims import load_env_file, normalize_text


SCHEMA_VERSION = "0.1.0"
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
INSTRUCTIONS = """You evaluate whether passages retrieved from one cited webpage support one medical claim.

Use only the supplied passages. Do not use outside medical knowledge and do not fact-check the claim against your own knowledge.

Labels:
- supported: the passages directly support every material part of the claim, including its modality, frequency, comparison, causal direction, and numerical details.
- partially_supported: the passages support some but not all material parts, or support a weaker/qualified version of the claim.
- not_supported: the passages are sufficiently relevant to assess the claim but do not substantiate it.
- contradicted: the passages directly conflict with at least one material part of the claim. Mere absence is not contradiction.
- insufficient_evidence: the supplied passages are off-topic, incomplete, or otherwise insufficient to decide.

For supported, partially_supported, or contradicted judgments, provide one or more short exact contiguous quotes from the supplied passages. Do not invent, paraphrase, splice, or shorten a quote with ellipses. If two non-contiguous passages are needed, return them as two separate evidence items. For not_supported or insufficient_evidence, the evidence list may be empty.

The reason must be concise and must describe only the relationship between the claim and supplied passages.
"""


class EntailmentLabel(str, Enum):
    supported = "supported"
    partially_supported = "partially_supported"
    not_supported = "not_supported"
    contradicted = "contradicted"
    insufficient_evidence = "insufficient_evidence"


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(description="Exact supplied chunk identifier")
    quote: str = Field(description="Short exact contiguous quotation from that chunk")


class EntailmentJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: EntailmentLabel
    evidence: list[EvidenceQuote]
    reason: str
    confidence: Confidence


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def pair_prompt(question: str, pair: dict[str, Any]) -> str:
    return json.dumps(
        {
            "question": question,
            "claim": pair["claim_text"],
            "cited_url": pair["citation_url"],
            "retrieved_passages": [
                {
                    "chunk_id": chunk["chunk_id"],
                    "heading": chunk["heading"],
                    "text": chunk["text"],
                }
                for chunk in pair.get("top_chunks", [])
            ],
        },
        indent=2,
        ensure_ascii=False,
    )


def normalize_rendered_text(value: str) -> str:
    """Normalize Markdown-backed passages to the text visible to a reader."""
    value = MARKDOWN_LINK_RE.sub(r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("_", "")
    return normalize_text(value)


def call_judge(client: OpenAI, model: str, question: str, pair: dict[str, Any]) -> tuple[Any, str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": pair_prompt(question, pair)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "citation_entailment_judgment",
                "strict": True,
                "schema": EntailmentJudgment.model_json_schema(),
            },
        },
        extra_body={"provider": {"require_parameters": True}},
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenRouter returned no textual structured output.")
    judgment = EntailmentJudgment.model_validate_json(content)
    usage = response.usage.model_dump() if response.usage is not None else None
    return judgment, response.id, usage


def validate_evidence(pair: dict[str, Any], judgment: EntailmentJudgment) -> list[str]:
    chunk_map = {chunk["chunk_id"]: chunk["text"] for chunk in pair.get("top_chunks", [])}
    warnings: list[str] = []
    for index, evidence in enumerate(judgment.evidence, start=1):
        chunk_text = chunk_map.get(evidence.chunk_id)
        if chunk_text is None:
            warnings.append(f"Evidence {index} refers to unknown chunk {evidence.chunk_id!r}.")
        elif normalize_rendered_text(evidence.quote) not in normalize_rendered_text(chunk_text):
            warnings.append(f"Evidence {index} quote is not an exact normalized substring of its chunk.")
    if judgment.label in {
        EntailmentLabel.supported,
        EntailmentLabel.partially_supported,
        EntailmentLabel.contradicted,
    } and not judgment.evidence:
        warnings.append(f"The {judgment.label.value} judgment contains no evidence quote.")
    return warnings


def build_output(source: dict[str, Any], model: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "completed_at": None,
        "source_retrieval_file_created_at": source.get("created_at"),
        "model": model,
        "method": "LLM entailment over retrieved top-k passages; no outside knowledge",
        "records": [],
        "summary": {},
    }


def update_summary(output: dict[str, Any]) -> None:
    pairs = [pair for record in output["records"] for pair in record["claim_citation_pairs"]]
    label_counts: dict[str, int] = {}
    for pair in pairs:
        label = pair["entailment_label"]
        label_counts[label] = label_counts.get(label, 0) + 1
    output["summary"] = {
        "record_count": len(output["records"]),
        "pair_count": len(pairs),
        "label_counts": label_counts,
        "warning_count": sum(len(pair.get("warnings", [])) for pair in pairs),
        "prompt_tokens": sum((pair.get("usage") or {}).get("prompt_tokens", 0) or 0 for pair in pairs),
        "completion_tokens": sum(
            (pair.get("usage") or {}).get("completion_tokens", 0) or 0 for pair in pairs
        ),
        "total_tokens": sum((pair.get("usage") or {}).get("total_tokens", 0) or 0 for pair in pairs),
        "cost_usd": sum((pair.get("usage") or {}).get("cost", 0) or 0 for pair in pairs),
    }


def run(
    source: dict[str, Any],
    output_path: Path,
    model: str,
    env_file: Path,
    retries: int,
) -> dict[str, Any]:
    load_env_file(env_file)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    output = build_output(source, model)

    for record in source.get("records", []):
        output_record = {
            "dataset_id": record["dataset_id"],
            "run": record["run"],
            "question": record["question"],
            "claim_count": record["claim_count"],
            "claims_with_local_citation": record["claims_with_local_citation"],
            "claim_citation_pairs": [],
        }
        output["records"].append(output_record)
        for pair in record.get("claim_citation_pairs", []):
            result = {
                "pair_id": pair["pair_id"],
                "claim_id": pair["claim_id"],
                "claim_text": pair["claim_text"],
                "citation_id": pair["citation_id"],
                "citation_url": pair["citation_url"],
                "page_retrieval_status": pair["page_retrieval_status"],
                "entailment_label": None,
                "evidence": [],
                "reason": None,
                "confidence": None,
                "model_response_id": None,
                "usage": None,
                "warnings": [],
            }
            output_record["claim_citation_pairs"].append(result)
            if pair["page_retrieval_status"] != "success" or not pair.get("top_chunks"):
                result.update(
                    {
                        "entailment_label": "unavailable",
                        "reason": "The cited page content was unavailable for entailment evaluation.",
                        "confidence": "high",
                    }
                )
                update_summary(output)
                write_json(output_path, output)
                print(f"{pair['pair_id']}: unavailable", flush=True)
                continue

            last_error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    judgment, response_id, usage = call_judge(client, model, record["question"], pair)
                    warnings = validate_evidence(pair, judgment)
                    if warnings and attempt < retries:
                        last_error = ValueError("; ".join(warnings))
                        print(
                            f"{pair['pair_id']}: invalid evidence quote; retrying",
                            flush=True,
                        )
                        time.sleep(2**attempt)
                        continue
                    result.update(
                        {
                            "entailment_label": judgment.label.value,
                            "evidence": [item.model_dump() for item in judgment.evidence],
                            "reason": judgment.reason,
                            "confidence": judgment.confidence.value,
                            "model_response_id": response_id,
                            "usage": usage,
                            "warnings": warnings,
                        }
                    )
                    print(f"{pair['pair_id']}: {judgment.label.value}", flush=True)
                    break
                except Exception as exc:  # API and schema errors are checkpointed after retries.
                    last_error = exc
                    if attempt < retries:
                        time.sleep(2**attempt)
            if result["entailment_label"] is None:
                result.update(
                    {
                        "entailment_label": "error",
                        "reason": f"{type(last_error).__name__}: {last_error}",
                        "confidence": "low",
                    }
                )
                print(f"{pair['pair_id']}: error", flush=True)
            update_summary(output)
            write_json(output_path, output)

    output["completed_at"] = utc_now()
    update_summary(output)
    write_json(output_path, output)
    return output


def revalidate_existing(
    source: dict[str, Any], output_path: Path, output: dict[str, Any]
) -> dict[str, Any]:
    source_pairs = {
        pair["pair_id"]: pair
        for record in source.get("records", [])
        for pair in record.get("claim_citation_pairs", [])
    }
    for record in output.get("records", []):
        for result in record.get("claim_citation_pairs", []):
            source_pair = source_pairs.get(result["pair_id"])
            if source_pair is None or result["entailment_label"] in {"unavailable", "error"}:
                continue
            judgment = EntailmentJudgment.model_validate(
                {
                    "label": result["entailment_label"],
                    "evidence": result["evidence"],
                    "reason": result["reason"],
                    "confidence": result["confidence"],
                }
            )
            result["warnings"] = validate_evidence(source_pair, judgment)
    update_summary(output)
    write_json(output_path, output)
    return output


def resume_unavailable(
    source: dict[str, Any],
    output_path: Path,
    output: dict[str, Any],
    model: str,
    env_file: Path,
    retries: int,
) -> dict[str, Any]:
    source_records = {record["dataset_id"]: record for record in source.get("records", [])}
    pending: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for output_record in output.get("records", []):
        source_record = source_records.get(output_record["dataset_id"])
        if source_record is None:
            continue
        source_pairs = {
            pair["pair_id"]: pair for pair in source_record.get("claim_citation_pairs", [])
        }
        for result in output_record.get("claim_citation_pairs", []):
            source_pair = source_pairs.get(result["pair_id"])
            if source_pair is None:
                continue
            result["page_retrieval_status"] = source_pair["page_retrieval_status"]
            if source_pair["page_retrieval_status"] != "success":
                if result["entailment_label"] not in {"unavailable", "error"}:
                    result["superseded_judgment"] = {
                        "entailment_label": result["entailment_label"],
                        "evidence": result["evidence"],
                        "reason": result["reason"],
                        "confidence": result["confidence"],
                        "model_response_id": result["model_response_id"],
                        "usage": result["usage"],
                        "warnings": result["warnings"],
                    }
                result.update(
                    {
                        "entailment_label": "unavailable",
                        "evidence": [],
                        "reason": "The cited page did not yield sufficient article content for entailment evaluation.",
                        "confidence": "high",
                        "model_response_id": None,
                        "usage": None,
                        "warnings": [],
                    }
                )
                continue
            if (
                result["entailment_label"] in {"unavailable", "error"}
                and source_pair["page_retrieval_status"] == "success"
                and source_pair.get("top_chunks")
            ):
                pending.append((source_record["question"], source_pair, result))

    if not pending:
        update_summary(output)
        write_json(output_path, output)
        return output

    load_env_file(env_file)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    for question, source_pair, result in pending:
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                judgment, response_id, usage = call_judge(client, model, question, source_pair)
                warnings = validate_evidence(source_pair, judgment)
                if warnings and attempt < retries:
                    last_error = ValueError("; ".join(warnings))
                    print(
                        f"{source_pair['pair_id']}: invalid evidence quote; retrying",
                        flush=True,
                    )
                    time.sleep(2**attempt)
                    continue
                result.update(
                    {
                        "entailment_label": judgment.label.value,
                        "evidence": [item.model_dump() for item in judgment.evidence],
                        "reason": judgment.reason,
                        "confidence": judgment.confidence.value,
                        "model_response_id": response_id,
                        "usage": usage,
                        "warnings": warnings,
                    }
                )
                print(f"{source_pair['pair_id']}: {judgment.label.value}", flush=True)
                break
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(2**attempt)
        if result["entailment_label"] in {"unavailable", "error"}:
            result.update(
                {
                    "entailment_label": "error",
                    "reason": f"{type(last_error).__name__}: {last_error}",
                    "confidence": "low",
                }
            )
        update_summary(output)
        write_json(output_path, output)

    output["completed_at"] = utc_now()
    update_summary(output)
    write_json(output_path, output)
    return output


def retry_warning_pairs(
    source: dict[str, Any],
    output_path: Path,
    output: dict[str, Any],
    model: str,
    env_file: Path,
    retries: int,
) -> dict[str, Any]:
    source_items = {
        pair["pair_id"]: (record["question"], pair)
        for record in source.get("records", [])
        for pair in record.get("claim_citation_pairs", [])
    }
    pending = [
        result
        for record in output.get("records", [])
        for result in record.get("claim_citation_pairs", [])
        if result.get("warnings")
    ]
    if not pending:
        update_summary(output)
        write_json(output_path, output)
        return output

    load_env_file(env_file)
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY is not set.")
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    for result in pending:
        source_item = source_items.get(result["pair_id"])
        if source_item is None:
            continue
        question, source_pair = source_item
        previous = {
            "entailment_label": result["entailment_label"],
            "evidence": result["evidence"],
            "reason": result["reason"],
            "confidence": result["confidence"],
            "model_response_id": result["model_response_id"],
            "usage": result["usage"],
            "warnings": result["warnings"],
        }
        for attempt in range(retries + 1):
            judgment, response_id, usage = call_judge(client, model, question, source_pair)
            warnings = validate_evidence(source_pair, judgment)
            if warnings and attempt < retries:
                print(
                    f"{source_pair['pair_id']}: invalid evidence quote; retrying",
                    flush=True,
                )
                time.sleep(2**attempt)
                continue
            result.setdefault("superseded_judgments", []).append(previous)
            result.update(
                {
                    "entailment_label": judgment.label.value,
                    "evidence": [item.model_dump() for item in judgment.evidence],
                    "reason": judgment.reason,
                    "confidence": judgment.confidence.value,
                    "model_response_id": response_id,
                    "usage": usage,
                    "warnings": warnings,
                }
            )
            print(
                f"{source_pair['pair_id']}: {judgment.label.value} "
                f"({len(warnings)} warnings)",
                flush=True,
            )
            break
        update_summary(output)
        write_json(output_path, output)

    output["completed_at"] = utc_now()
    update_summary(output)
    write_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retrieval_json", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".openrouter_env"))
    parser.add_argument("--model", default="openai/gpt-5-mini")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--dataset-id",
        action="append",
        help="Evaluate only the named dataset ID; repeat to select multiple records",
    )
    parser.add_argument(
        "--revalidate-only",
        action="store_true",
        help="Recheck evidence quotes in an existing output without calling the API",
    )
    parser.add_argument(
        "--resume-unavailable",
        action="store_true",
        help="Only judge prior unavailable/error pairs whose page content is now available",
    )
    parser.add_argument(
        "--retry-warnings",
        action="store_true",
        help="Rejudge only existing pairs whose evidence validation has warnings",
    )
    args = parser.parse_args()

    source = json.loads(args.retrieval_json.read_text(encoding="utf-8"))
    if args.dataset_id:
        selected = set(args.dataset_id)
        source = dict(source)
        source["records"] = [
            record for record in source.get("records", []) if record["dataset_id"] in selected
        ]
        found = {record["dataset_id"] for record in source["records"]}
        missing = selected - found
        if missing:
            raise SystemExit(f"Dataset IDs not found: {', '.join(sorted(missing))}")
    if args.revalidate_only:
        if not args.output_json.exists():
            raise SystemExit(f"Existing output does not exist: {args.output_json}")
        existing = json.loads(args.output_json.read_text(encoding="utf-8"))
        output = revalidate_existing(source, args.output_json, existing)
        print(json.dumps(output["summary"], indent=2), flush=True)
        print(f"Revalidated: {args.output_json}", flush=True)
        return
    if args.resume_unavailable:
        if not args.output_json.exists():
            raise SystemExit(f"Existing output does not exist: {args.output_json}")
        existing = json.loads(args.output_json.read_text(encoding="utf-8"))
        output = resume_unavailable(
            source,
            args.output_json,
            existing,
            args.model,
            args.env_file,
            args.retries,
        )
        print(json.dumps(output["summary"], indent=2), flush=True)
        print(f"Resumed: {args.output_json}", flush=True)
        return
    if args.retry_warnings:
        if not args.output_json.exists():
            raise SystemExit(f"Existing output does not exist: {args.output_json}")
        existing = json.loads(args.output_json.read_text(encoding="utf-8"))
        output = retry_warning_pairs(
            source,
            args.output_json,
            existing,
            args.model,
            args.env_file,
            args.retries,
        )
        print(json.dumps(output["summary"], indent=2), flush=True)
        print(f"Retried warnings: {args.output_json}", flush=True)
        return
    output = run(source, args.output_json, args.model, args.env_file, args.retries)
    print(json.dumps(output["summary"], indent=2), flush=True)
    print(f"Output: {args.output_json}", flush=True)


if __name__ == "__main__":
    main()
