"""Capture paired web-grounded answers for the citation-pattern pilot.

Only the final response is saved. The API key is read locally and never written.
"""

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


MODELS = ["openai/gpt-6.1-sol", "anthropic/claude-sonnet-5.5"]
QUESTION_IDS = [
    "HSQA_2432",  # numerical scope
    "HSQA_3069",  # combined risk factors
    "HSQA_0552",  # unsupported detail
    "HSQA_2089",  # compound descriptors
    "HSQA_2423",  # combined causal examples
    "HSQA_1648",  # multi-item list
    "HSQA_0508",  # temporal scope
    "HSQA_2598",  # numerical qualifier
    "HSQA_1519",  # numerical threshold
    "HSQA_2424",  # population scope
]
PROMPT = (
    "Search the web before answering this consumer health question. Answer in English "
    "for a lay reader. Give a direct, useful answer, cite the sources immediately after "
    "the statements they support, and preserve source-specific uncertainty and scope. "
    "Do not claim a source supports a statement unless it does.\n\nQuestion: {question}"
)


def load_key(path: Path) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    for line in path.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"\'“”‘’')
    raise RuntimeError("OPENROUTER_API_KEY is unavailable")


def request_answer(key: str, model: str, question: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT.format(question=question)}],
        "tools": [{"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 5}}],
    }
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:
            return json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode()[:1000]}") from error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".openrouter_env"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("outputs/healthsearchqa_pilot/evaluation/results/cross_model_web_pilot.jsonl"))
    args = parser.parse_args()
    key = load_key(args.env_file)
    with Path("data/healthsearchqa/healthsearchqa_subset_n40_seed20260926.csv").open() as stream:
        questions = {row["dataset_id"]: row["question"] for row in csv.DictReader(stream)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if args.output.exists():
        for line in args.output.read_text().splitlines():
            item = json.loads(line)
            completed.add((item["dataset_id"], item["requested_model"]))
    for dataset_id in QUESTION_IDS[: args.limit]:
        for model in MODELS:
            if (dataset_id, model) in completed:
                continue
            question = questions[dataset_id]
            print(f"Requesting {dataset_id} {model}", flush=True)
            response = request_answer(key, model, question)
            record = {
                "dataset_id": dataset_id,
                "question": question,
                "requested_model": model,
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "search_engine_requested": "exa",
                "prompt": PROMPT.format(question=question),
                "response": response,
            }
            with args.output.open("a") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
