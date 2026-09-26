#!/usr/bin/env python3
"""Download HealthSearchQA if needed and generate a reproducible random subset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


SOURCE_URL = (
    "https://static-content.springer.com/esm/"
    "art%3A10.1038%2Fs41586-023-06291-2/MediaObjects/"
    "41586_2023_6291_MOESM6_ESM.xlsx"
)
ARTICLE_URL = "https://doi.org/10.1038/s41586-023-06291-2"
SOURCE_SHEET = "All HealthSearchQA Questions"
NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg": "http://schemas.openxmlformats.org/package/2006/relationships",
}
MASK32 = 0xFFFFFFFF
EXCLUDED_DATASET_IDS = {"HSQA_1548"}  # Off-domain noise: "What are the 3 wind types?"


class Mulberry32:
    """Python implementation of the 32-bit PRNG used for this pilot sample."""

    def __init__(self, seed: int) -> None:
        self.state = seed & MASK32

    def random(self) -> float:
        self.state = (self.state + 0x6D2B79F5) & MASK32
        old = ((self.state ^ (self.state >> 15)) * (1 | self.state)) & MASK32
        term = ((old ^ (old >> 7)) * (61 | old)) & MASK32
        value = (((old + term) & MASK32) ^ old) & MASK32
        return ((value ^ (value >> 14)) & MASK32) / 4294967296


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_source(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(SOURCE_URL, path)


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.findall(".//main:t", NS))
        for item in root.findall("main:si", NS)
    ]


def sheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("pkg:Relationship", NS)
    }
    for sheet in workbook.findall(".//main:sheet", NS):
        if sheet.attrib["name"] == sheet_name:
            relationship_id = sheet.attrib[f"{{{NS['rel']}}}id"]
            return f"xl/{targets[relationship_id]}"
    raise KeyError(f"Worksheet not found: {sheet_name}")


def load_questions(path: Path) -> list[dict[str, object]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = read_shared_strings(archive)
        root = ET.fromstring(archive.read(sheet_path(archive, SOURCE_SHEET)))
        questions: list[dict[str, object]] = []
        for row in root.findall(".//main:row", NS):
            cells = row.findall("main:c", NS)
            if not cells:
                continue
            cell = cells[0]
            value = cell.find("main:v", NS)
            if value is None:
                continue
            if cell.attrib.get("t") == "s":
                question = shared_strings[int(value.text or "0")].strip()
            else:
                question = (value.text or "").strip()
            if not question:
                continue
            questions.append(
                {
                    "source_index": len(questions) + 1,
                    "source_excel_row": int(row.attrib["r"]),
                    "question": question,
                }
            )
    return questions


def exact_deduplicate(records: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[str] = set()
    unique: list[dict[str, object]] = []
    for record in records:
        question = str(record["question"])
        if question in seen:
            continue
        seen.add(question)
        unique.append(record)
    return unique


def shuffled_copy(records: list[dict[str, object]], seed: int) -> list[dict[str, object]]:
    random = Mulberry32(seed)
    shuffled = records.copy()
    for index in range(len(shuffled) - 1, 0, -1):
        swap_index = math.floor(random.random() * (index + 1))
        shuffled[index], shuffled[swap_index] = shuffled[swap_index], shuffled[index]
    return shuffled


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/healthsearchqa/healthsearchqa_official.xlsx"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/healthsearchqa"))
    parser.add_argument("--sample-size", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args()

    if args.sample_size <= 0:
        raise ValueError("sample-size must be positive")
    if not 0 <= args.seed <= MASK32:
        raise ValueError("seed must be an unsigned 32-bit integer")

    ensure_source(args.source)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    nonempty = load_questions(args.source)
    unique = exact_deduplicate(nonempty)
    if args.sample_size > len(unique):
        raise ValueError(f"sample-size exceeds the {len(unique)} available unique questions")

    shuffled = shuffled_copy(unique, args.seed)
    selected = shuffled[: args.sample_size]
    selected_ids = {
        f"HSQA_{int(record['source_index']):04d}" for record in selected
    }
    replacements = iter(
        record
        for record in shuffled[args.sample_size :]
        if f"HSQA_{int(record['source_index']):04d}" not in EXCLUDED_DATASET_IDS
        and f"HSQA_{int(record['source_index']):04d}" not in selected_ids
    )
    for index, record in enumerate(selected):
        dataset_id = f"HSQA_{int(record['source_index']):04d}"
        if dataset_id in EXCLUDED_DATASET_IDS:
            selected[index] = next(replacements)
    questions = []
    for sample_order, record in enumerate(selected, start=1):
        source_index = int(record["source_index"])
        questions.append(
            {
                "sample_order": sample_order,
                "dataset_id": f"HSQA_{source_index:04d}",
                "source_index": source_index,
                "source_excel_row": int(record["source_excel_row"]),
                "question": str(record["question"]),
            }
        )

    selection_hash = hashlib.sha256(
        "\n".join(item["dataset_id"] for item in questions).encode("utf-8")
    ).hexdigest()
    metadata = {
        "dataset": "HealthSearchQA",
        "source_article": ARTICLE_URL,
        "source_download": SOURCE_URL,
        "source_file": args.source.name,
        "source_sheet": SOURCE_SHEET,
        "source_sha256": sha256_file(args.source),
        "nonempty_question_count": len(nonempty),
        "unique_question_count": len(unique),
        "exact_duplicate_count_removed": len(nonempty) - len(unique),
        "sample_size": args.sample_size,
        "random_seed": args.seed,
        "sampling_method": (
            "Exact-trim deduplication followed by Mulberry32-seeded "
            "Fisher-Yates shuffle; first n records selected; listed "
            "off-domain records replaced in place by the next eligible records."
        ),
        "excluded_dataset_ids": sorted(EXCLUDED_DATASET_IDS),
        "selection_sha256": selection_hash,
    }

    stem = f"healthsearchqa_subset_n{args.sample_size}_seed{args.seed}"
    csv_path = args.output_dir / f"{stem}.csv"
    json_path = args.output_dir / f"{stem}.json"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(questions[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(questions)
    with json_path.open("w", encoding="utf-8") as stream:
        json.dump({"metadata": metadata, "questions": questions}, stream, indent=2)
        stream.write("\n")

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
