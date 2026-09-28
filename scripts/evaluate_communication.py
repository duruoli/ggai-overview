#!/usr/bin/env python3
"""Score AI Overview understandability and actionability item by item with an LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from extract_medical_claims import load_env_file, normalize_text


SCHEMA_VERSION = "0.1.0"
ITEM_IDS = ("u1", "u2", "u3", "u4", "u5", "a1", "a2", "a3")
UNDERSTANDABILITY_IDS = ITEM_IDS[:5]
ACTIONABILITY_IDS = ITEM_IDS[5:]
INSTRUCTIONS = """You are an ordinary adult reading a health answer for yourself. You have everyday reading ability but no medical training or prior knowledge of the condition. Judge only what you can understand and do from the supplied question and answer. Do not use your own medical knowledge to decode terms or fill in missing steps.

Score each item independently as yes, no, or na. Give a short reason and, when possible, a short exact quote from the answer; use an empty quote for omissions and na. Do not judge medical accuracy.

Understandability:
u1 Directly answers the question and has a clear purpose (yes/no).
u2 Primarily uses everyday language (yes/no).
u3 Explains needed medical terms and abbreviations in plain language when first used, including terms from the question (yes/no; na if none need explanation).
u4 Presents information in a logical, manageable order (yes/no).
u5 Avoids distracting repetition and excess detail (yes/no).

Decide actionability_applicable from the question alone: yes if it asks what to do, how to determine whether someone has a condition or injury, or when to seek care. Questions asking only for facts (definition, symptoms, appearance, causes, risks, or prognosis) are no, even if the answer adds advice. If no, set a1-a3 to na and explain why.

When actionability applies:
a1 States at least one action the reader can take (yes/no).
a2 Gives concrete, feasible guidance the reader can follow without guessing key steps; no action or only vague advice is no (yes/no).
a3 Explains when, why, or where to seek care when the question calls for it (yes/no; na only if care-seeking guidance is irrelevant).
"""


class Label(str, Enum):
    yes = "yes"
    no = "no"
    na = "na"


class ItemJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label
    reason: str
    quote: str = Field(description="Exact contiguous excerpt from answer, or empty string")


class CommunicationJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actionability_applicable: bool
    applicability_reason: str
    u1: ItemJudgment
    u2: ItemJudgment
    u3: ItemJudgment
    u4: ItemJudgment
    u5: ItemJudgment
    a1: ItemJudgment
    a2: ItemJudgment
    a3: ItemJudgment


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def record_key(record: dict[str, Any]) -> str:
    return f"{record['dataset_id']}::run_{record['run']}"


def prompt_payload(record: dict[str, Any]) -> str:
    return json.dumps(
        {"question": record["question"], "main_answer_text": record["main_text"]},
        ensure_ascii=False,
        indent=2,
    )


def call_judge(
    client: OpenAI, model: str, record: dict[str, Any]
) -> tuple[CommunicationJudgment, str | None, dict[str, Any] | None]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": prompt_payload(record)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "communication_judgment",
                "strict": True,
                "schema": CommunicationJudgment.model_json_schema(),
            },
        },
        extra_body={"provider": {"require_parameters": True}},
    )
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenRouter returned no textual structured output.")
    judgment = CommunicationJudgment.model_validate_json(content)
    usage = response.usage.model_dump() if response.usage is not None else None
    return judgment, response.id, usage


def validate_judgment(judgment: CommunicationJudgment, answer: str) -> list[str]:
    warnings: list[str] = []
    if not judgment.applicability_reason.strip():
        warnings.append("Actionability applicability reason is empty.")
    for item_id in ITEM_IDS:
        item: ItemJudgment = getattr(judgment, item_id)
        if not item.reason.strip():
            warnings.append(f"{item_id} reason is empty.")
        if item.quote.strip() and normalize_text(item.quote) not in normalize_text(answer):
            warnings.append(f"{item_id} quote is not an exact normalized answer substring.")
        if item.label == Label.na and item.quote.strip():
            warnings.append(f"{item_id} is na but has a quote.")
    for item_id in ("u1", "u2", "u4", "u5"):
        if getattr(judgment, item_id).label == Label.na:
            warnings.append(f"{item_id} cannot be na.")
    for item_id in ACTIONABILITY_IDS:
        label = getattr(judgment, item_id).label
        if judgment.actionability_applicable and item_id in ("a1", "a2") and label == Label.na:
            warnings.append(f"{item_id} cannot be na when actionability applies.")
        if not judgment.actionability_applicable and label != Label.na:
            warnings.append(f"{item_id} must be na when actionability does not apply.")
    return warnings


def score_items(items: dict[str, dict[str, str]], ids: tuple[str, ...]) -> float | None:
    applicable = [items[item_id]["label"] for item_id in ids if items[item_id]["label"] != "na"]
    if not applicable:
        return None
    return round(100 * applicable.count("yes") / len(applicable), 2)


def scored_communication(judgment: CommunicationJudgment) -> dict[str, Any]:
    items = {
        item_id: getattr(judgment, item_id).model_dump(mode="json") for item_id in ITEM_IDS
    }
    return {
        "understandability_score": score_items(items, UNDERSTANDABILITY_IDS),
        "actionability_applicable": judgment.actionability_applicable,
        "actionability_applicability_reason": judgment.applicability_reason,
        "actionability_score": score_items(items, ACTIONABILITY_IDS),
        "items": items,
    }


def score_summary(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        record["communication"][field]
        for record in records
        if record.get("communication") and record["communication"][field] is not None
    ]
    quartiles = (
        statistics.quantiles(values, n=4, method="inclusive")
        if len(values) >= 2
        else [values[0]] * 3 if values else None
    )
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2) if values else None,
        "standard_deviation": round(statistics.stdev(values), 2) if len(values) >= 2 else None,
        "median": round(statistics.median(values), 2) if values else None,
        "q1": round(quartiles[0], 2) if quartiles else None,
        "q3": round(quartiles[2], 2) if quartiles else None,
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def update_summary(output: dict[str, Any]) -> None:
    records = output["records"]
    output["summary"] = {
        "record_count": len(records),
        "aio_present_count": sum(record["aio_present"] for record in records),
        "scored_count": sum(record["status"] == "scored" for record in records),
        "error_count": sum(record["status"] == "error" for record in records),
        "actionability_na_count": sum(
            record.get("communication") is not None
            and record["communication"]["actionability_score"] is None
            for record in records
        ),
        "understandability": score_summary(records, "understandability_score"),
        "actionability": score_summary(records, "actionability_score"),
        "warning_count": sum(len(record.get("warnings", [])) for record in records),
        "prompt_tokens": sum((record.get("usage") or {}).get("prompt_tokens", 0) or 0 for record in records),
        "completion_tokens": sum(
            (record.get("usage") or {}).get("completion_tokens", 0) or 0 for record in records
        ),
        "cost_usd": round(
            sum((record.get("usage") or {}).get("cost", 0) or 0 for record in records), 6
        ),
    }


def evaluate_record(
    client: OpenAI,
    model: str,
    source_record: dict[str, Any],
    retries: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "dataset_id": source_record["dataset_id"],
        "sample_order": source_record.get("sample_order"),
        "run": source_record["run"],
        "question": source_record["question"],
        "aio_present": True,
    }
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            judgment, response_id, usage = call_judge(client, model, source_record)
            warnings = validate_judgment(judgment, source_record["main_text"])
            if warnings:
                raise ValueError("; ".join(warnings))
            result.update(
                {
                    "status": "scored",
                    "communication": scored_communication(judgment),
                    "model_response_id": response_id,
                    "usage": usage,
                    "warnings": [],
                }
            )
            return result
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2**attempt)
    result.update(
        {
            "status": "error",
            "communication": None,
            "error": f"{type(last_error).__name__}: {last_error}",
        }
    )
    return result


def run(
    source: dict[str, Any],
    source_bytes: bytes,
    output_path: Path,
    model: str,
    env_file: Path,
    run_filter: int | None,
    limit: int | None,
    retries: int,
    resume: bool,
    workers: int,
) -> dict[str, Any]:
    selected = [
        record for record in source["records"]
        if run_filter is None or record["run"] == run_filter
    ]
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if resume and output_path.exists():
        output = json.loads(output_path.read_text(encoding="utf-8"))
        if (
            output.get("source_sha256") != source_hash
            or output.get("model") != model
            or output.get("run_filter") != run_filter
            or output.get("instructions_sha256") != hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()
        ):
            raise SystemExit("Resume metadata does not match source, model, run, or rubric.")
    else:
        output = {
            "schema_version": SCHEMA_VERSION,
            "created_at": utc_now(),
            "completed_at": None,
            "source_sha256": source_hash,
            "source_capture": source.get("source_capture"),
            "model": model,
            "provider": "openrouter",
            "run_filter": run_filter,
            "instructions_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
            "method": "LLM item-level simplified PEMAT-informed text rubric; deterministic score calculation",
            "records": [],
            "summary": {},
        }

    existing = {record_key(record): record for record in output["records"]}
    pending = [
        record for record in selected
        if record["aio_present"] and (
            record_key(record) not in existing or existing[record_key(record)]["status"] == "error"
        )
    ]
    if limit is not None:
        pending = pending[:limit]
    if pending:
        load_env_file(env_file)
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY is not set.")
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    else:
        client = None

    order = {record_key(record): index for index, record in enumerate(selected)}
    for source_record in selected:
        key = record_key(source_record)
        if source_record["aio_present"] or key in existing:
            continue
        result = {
            "dataset_id": source_record["dataset_id"],
            "sample_order": source_record.get("sample_order"),
            "run": source_record["run"],
            "question": source_record["question"],
            "aio_present": False,
            "status": "not_present",
            "communication": None,
        }
        output["records"].append(result)
        existing[key] = result
    output["records"].sort(key=lambda record: order[record_key(record)])
    update_summary(output)
    write_json(output_path, output)

    if pending:
        assert client is not None
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(evaluate_record, client, model, record, retries): record
                for record in pending
            }
            for future in as_completed(futures):
                source_record = futures[future]
                result = future.result()
                key = record_key(source_record)
                if key in existing:
                    existing[key].clear()
                    existing[key].update(result)
                else:
                    output["records"].append(result)
                    existing[key] = result
                output["records"].sort(key=lambda record: order[record_key(record)])
                update_summary(output)
                write_json(output_path, output)
                print(
                    f"[{order[key] + 1}/{len(selected)}] {key}: {result['status']}",
                    file=sys.stderr,
                    flush=True,
                )

    output["completed_at"] = utc_now()
    update_summary(output)
    write_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Parsed AIO JSON from parse_aio.py")
    parser.add_argument("output", type=Path, help="Communication evaluation JSON")
    parser.add_argument("--model", required=True, help="OpenRouter model name")
    parser.add_argument("--env-file", type=Path, default=Path(".openrouter_env"))
    parser.add_argument("--run", type=int, help="Only score one repeated run")
    parser.add_argument("--limit", type=int, help="Maximum number of present AIOs to score")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4, help="Concurrent API requests")
    parser.add_argument("--resume", action="store_true", help="Keep scored records and retry errors")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.retries < 0:
        parser.error("--retries must be nonnegative")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.output.exists() and not args.resume:
        parser.error("Output already exists; use --resume or a new output path")
    source_bytes = args.input.read_bytes()
    source = json.loads(source_bytes)
    result = run(
        source,
        source_bytes,
        args.output,
        args.model,
        args.env_file,
        args.run,
        args.limit,
        args.retries,
        args.resume,
        args.workers,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
