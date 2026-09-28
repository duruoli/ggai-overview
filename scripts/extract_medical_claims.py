#!/usr/bin/env python3
"""Extract atomic, externally verifiable medical claims from parsed AIO blocks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "0.1.0"
SPACE_RE = re.compile(r"\s+")

INSTRUCTIONS = """You extract atomic, externally verifiable medical claims from patient-facing answers.

Rules:
1. Extract claims; do not fact-check them and do not correct them.
2. Use clinically meaningful atomicity, not the smallest possible semantic fragments. Default to one claim per sentence or bullet.
3. Keep coordinated symptoms, descriptors, examples, consequences, or triggers together when one source would normally support them as a group. Do not split synonyms or a parenthetical definition into separate claims.
4. Split a sentence only when its components make substantively different medical assertions that could reasonably require different evidence or be true or false independently. A causal chain may be split when it contains distinct causal steps.
5. Include claims about definitions, symptoms, causes, risk factors, diagnosis, prognosis, prevalence, numerical estimates, treatments, prevention, self-care, care-seeking, timing, contraindications, and consequences.
6. Exclude headings, rhetorical questions, invitations for follow-up, generic disclaimers, purely connective text, and statements about what the answer will discuss.
7. Rephrase only enough to make the claim standalone and resolve pronouns. Preserve the original modality, frequency, uncertainty, comparison, and causal direction. For example, do not change "feels like" into "causes," "can" into "does," or "sometimes" into "always."
8. Do not add information that is absent from the source block.
9. source_quote must be a short, exact, contiguous quotation from the specified block that provides the claim.
10. Preserve the supplied block_id exactly. Return an empty claims list if there are no externally verifiable medical claims.
"""


class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(description="Exact block identifier supplied in the input")
    source_quote: str = Field(description="Exact contiguous quote from the source block")
    claim_text: str = Field(description="One standalone, atomic medical claim")


class ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim]


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value).strip()


def record_key(record: dict[str, Any]) -> str:
    return f"{record.get('dataset_id')}::run_{record.get('run')}"


def prompt_payload(record: dict[str, Any]) -> str:
    blocks = [
        {
            "block_id": block["block_id"],
            "block_type": block["block_type"],
            "text": block["text"],
        }
        for block in record.get("blocks", [])
    ]
    return json.dumps(
        {"question": record.get("question", ""), "answer_blocks": blocks},
        indent=2,
        ensure_ascii=False,
    )


def prepared_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": record.get("dataset_id"),
        "sample_order": record.get("sample_order"),
        "run": record.get("run"),
        "question": record.get("question", ""),
        "aio_present": record.get("aio_present", False),
        "extraction_status": "prepared" if record.get("aio_present") else "not_present",
        "prompt_input": prompt_payload(record) if record.get("aio_present") else None,
        "source_pool_ids": [source["citation_id"] for source in record.get("sources", [])],
        "source_pool": record.get("sources", []),
        "claims": [],
    }


def validate_and_enrich_claims(
    record: dict[str, Any], extraction: ClaimExtraction
) -> tuple[list[dict[str, Any]], list[str]]:
    block_map = {block["block_id"]: block for block in record.get("blocks", [])}
    claims: list[dict[str, Any]] = []
    warnings: list[str] = []

    for position, item in enumerate(extraction.claims, start=1):
        block = block_map.get(item.block_id)
        if block is None:
            warnings.append(f"Claim {position} refers to unknown block {item.block_id!r} and was dropped.")
            continue
        quote_matches = normalize_text(item.source_quote) in normalize_text(block["text"])
        if not quote_matches:
            warnings.append(f"Claim {position} source_quote is not an exact normalized substring of {item.block_id}.")
        claims.append(
            {
                "claim_id": f"c{len(claims) + 1:03d}",
                "block_id": item.block_id,
                "source_quote": item.source_quote,
                "source_quote_validated": quote_matches,
                "claim_text": item.claim_text,
                "local_citation_ids": list(block.get("local_citation_ids", [])),
                "candidate_citation_ids": list(block.get("candidate_citation_ids", [])),
                "citation_scope": block.get("citation_scope", "none"),
                "has_local_citation": bool(block.get("local_citation_ids")),
                "mapping_ambiguous": bool(
                    set(block.get("candidate_citation_ids", []))
                    != set(block.get("local_citation_ids", []))
                    or
                    block.get("unresolved_citation_marker_count", 0)
                    or block.get("additional_sources_indicated", 0)
                ),
            }
        )
    return claims, warnings


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE pairs without printing or persisting secret values."""
    if not path.exists():
        raise SystemExit(f"Environment file does not exist: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        quote_chars = {'"', "'", "\u2018", "\u2019", "\u201c", "\u201d"}
        if len(value) >= 2 and value[0] in quote_chars and value[-1] in quote_chars:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def call_extractor(
    client: Any,
    provider: str,
    model: str,
    record: dict[str, Any],
) -> tuple[ClaimExtraction, str | None, dict[str, Any] | None]:
    if provider == "openrouter":
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": prompt_payload(record)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "medical_claim_extraction",
                    "strict": True,
                    "schema": ClaimExtraction.model_json_schema(),
                },
            },
            extra_body={"provider": {"require_parameters": True}},
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("OpenRouter returned no textual structured output.")
        parsed_output = ClaimExtraction.model_validate_json(content)
        usage = response.usage.model_dump() if response.usage is not None else None
        return parsed_output, response.id, usage

    response = client.responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=prompt_payload(record),
        text_format=ClaimExtraction,
        temperature=0,
    )
    parsed_output = response.output_parsed
    if parsed_output is None:
        raise RuntimeError("The model returned no parsed structured output.")
    usage = response.usage.model_dump() if response.usage is not None else None
    return parsed_output, response.id, usage


def run_extraction(
    parsed: dict[str, Any],
    output_path: Path,
    provider: str,
    model: str,
    limit: int | None,
    env_file: Path | None,
    run_filter: int | None,
) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("The openai Python package is required for claim extraction.") from exc

    if env_file is not None:
        load_env_file(env_file)

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise SystemExit("OPENROUTER_API_KEY is not set.")
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    else:
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set.")
        client = OpenAI()
    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_parsed_file": str(parsed.get("source_capture", "")),
        "provider": provider,
        "model": model,
        "records": [],
    }
    completed = 0

    selected_records = [
        record
        for record in parsed.get("records", [])
        if run_filter is None or record.get("run") == run_filter
    ]

    for record in selected_records:
        base = prepared_record(record)
        base.pop("prompt_input", None)
        if not record.get("aio_present"):
            output["records"].append(base)
            continue
        if limit is not None and completed >= limit:
            base["extraction_status"] = "not_run_due_to_limit"
            output["records"].append(base)
            continue

        try:
            parsed_output, response_id, usage = call_extractor(client, provider, model, record)
            claims, warnings = validate_and_enrich_claims(record, parsed_output)
            base.update(
                {
                    "extraction_status": "success_with_warnings" if warnings else "success",
                    "model_response_id": response_id,
                    "usage": usage,
                    "claims": claims,
                    "warnings": warnings,
                }
            )
        except Exception as exc:  # preserve partial progress for later review/resume
            base.update({"extraction_status": "error", "error": f"{type(exc).__name__}: {exc}"})
        output["records"].append(base)
        completed += 1
        write_json(output_path, output)
        print(f"[{completed}] {record_key(record)}: {base['extraction_status']}", file=sys.stderr)

    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    output["summary"] = {
        "record_count": len(output["records"]),
        "successful_record_count": sum(
            item["extraction_status"] in {"success", "success_with_warnings"}
            for item in output["records"]
        ),
        "claim_count": sum(len(item.get("claims", [])) for item in output["records"]),
    }
    write_json(output_path, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Parsed AIO JSON from parse_aio.py")
    parser.add_argument("output", type=Path, help="Claim extraction JSON output")
    parser.add_argument("--provider", choices=["openai", "openrouter"], default="openai")
    parser.add_argument("--model", help="Provider-specific model name used for extraction")
    parser.add_argument("--env-file", type=Path, help="Optional KEY=VALUE environment file")
    parser.add_argument("--limit", type=int, help="Maximum number of present-AIO records to call")
    parser.add_argument("--run", type=int, help="Only include records from this repeated-run number")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Write model-ready inputs without making API calls",
    )
    args = parser.parse_args()

    parsed = json.loads(args.input.read_text(encoding="utf-8"))
    if args.prepare_only:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_parsed_file": str(args.input),
            "instructions": INSTRUCTIONS,
            "records": [prepared_record(record) for record in parsed.get("records", [])],
        }
        write_json(args.output, payload)
        prepared_count = sum(item["extraction_status"] == "prepared" for item in payload["records"])
        print(f"Prepared {prepared_count} model inputs. Output: {args.output}")
        return

    if not args.model:
        parser.error("--model is required unless --prepare-only is used")
    result = run_extraction(
        parsed,
        args.output,
        args.provider,
        args.model,
        args.limit,
        args.env_file,
        args.run,
    )
    print(
        f"Extracted {result['summary']['claim_count']} claims from "
        f"{result['summary']['successful_record_count']} responses. Output: {args.output}"
    )


if __name__ == "__main__":
    main()
