#!/usr/bin/env python3
"""Fetch locally cited pages and retrieve claim-relevant evidence passages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer


SCHEMA_VERSION = "0.1.0"
SPACE_RE = re.compile(r"\s+")
BLOCK_SELECTOR = "h1, h2, h3, h4, p, li, [role='paragraph']"
DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "aside",
    "form",
    "button",
    "iframe",
)
BOILERPLATE_PATTERNS = (
    "accept cookies",
    "cookie policy",
    "sign up for",
    "subscribe to",
    "advertisement",
    "skip to content",
)
NON_CONTENT_HEADINGS = {
    "references",
    "citations",
    "information",
    "share",
    "related content",
    "recommended",
}
CHALLENGE_PATTERNS = (
    "performing security verification",
    "verify you are not a bot",
    "not a bot",
    "verifying you are human",
    "checking your browser",
    "enable javascript and cookies to continue",
    "access denied",
)
USER_AGENT = (
    "Mozilla/5.0 (compatible; GG-AIO-Evidence-Pilot/0.1; "
    "+https://example.invalid/research-pilot)"
)
READER_BASE_URL = "https://r.jina.ai/http://"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    return SPACE_RE.sub(" ", value).strip()


def url_cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def valid_web_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def select_content_root(soup: BeautifulSoup) -> Any:
    body = soup.body or soup

    def substantive_word_count(node: Any) -> int:
        return sum(
            len(normalize_text(child.get_text(" ", strip=True)).split())
            for child in node.select("p, li, [role='paragraph']")
        )

    body_words = substantive_word_count(body)
    semantic_candidates = soup.select(
        "[property='articleBody'], [itemprop='articleBody'], #bodymatter, "
        ".article-body, .article-content, .entry-content, .post-content"
    )
    if semantic_candidates:
        best_semantic = max(semantic_candidates, key=substantive_word_count)
        semantic_words = substantive_word_count(best_semantic)
        if semantic_words >= 30 and (not body_words or semantic_words >= body_words * 0.25):
            return best_semantic

    candidates = soup.select("article, main, [role='main']")
    if not candidates:
        return body

    best = max(candidates, key=substantive_word_count)
    best_words = substantive_word_count(best)
    if best_words < 30 or (body_words and best_words < body_words * 0.25):
        return body
    return best


def extract_blocks(html: str) -> tuple[str, list[dict[str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    root = deepcopy(select_content_root(soup))
    for tag in root.find_all(DROP_TAGS):
        tag.decompose()

    blocks: list[dict[str, str]] = []
    current_heading = ""
    previous_text = ""
    for node in root.select(BLOCK_SELECTOR):
        text = normalize_text(node.get_text(" ", strip=True))
        if not text or text == previous_text:
            continue
        previous_text = text
        if node.name in {"h1", "h2", "h3", "h4"}:
            current_heading = text
            continue
        if len(text) < 20:
            continue
        lowered = text.lower()
        if any(pattern in lowered for pattern in BOILERPLATE_PATTERNS):
            continue
        blocks.append({"heading": current_heading, "text": text})
    return title, blocks


def sanitize_captured_html(html: str) -> str:
    """Remove executable and presentation assets from a browser-captured DOM."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(("script", "style", "noscript", "svg", "template")):
        tag.decompose()
    for tag in soup.select("link[rel='stylesheet'], link[rel='preload'][as='style']"):
        tag.decompose()
    return str(soup)


def extract_markdown_blocks(markdown: str) -> tuple[str, list[dict[str, str]]]:
    """Extract heading-associated paragraphs from a Jina Reader markdown response."""
    title = ""
    content = markdown
    marker = "Markdown Content:"
    if marker in markdown:
        header, content = markdown.split(marker, 1)
        for line in header.splitlines():
            if line.startswith("Title:"):
                title = normalize_text(line.removeprefix("Title:"))
                break

    blocks: list[dict[str, str]] = []
    current_heading = ""
    paragraph: list[str] = []

    def flush() -> None:
        nonlocal paragraph
        text = normalize_text(" ".join(paragraph))
        paragraph = []
        if len(text) < 20:
            return
        lowered = text.lower()
        if any(pattern in lowered for pattern in BOILERPLATE_PATTERNS):
            return
        blocks.append({"heading": current_heading, "text": text})

    for raw_line in content.splitlines():
        line = raw_line.strip()
        heading_match = re.match(r"^#{1,3}\s+(.+)$", line)
        if heading_match:
            flush()
            current_heading = normalize_text(heading_match.group(1))
        elif not line:
            flush()
        else:
            paragraph.append(line)
    flush()
    return title, blocks


def is_challenge_page(title: str, blocks: list[dict[str, str]]) -> bool:
    combined = normalize_text(" ".join([title, *(block["text"] for block in blocks)])).lower()
    return any(pattern in combined for pattern in CHALLENGE_PATTERNS)


def split_long_text(text: str, max_words: int) -> list[str]:
    words = text.split()
    if len(words) <= max_words:
        return [text]
    return [
        " ".join(words[start : start + max_words])
        for start in range(0, len(words), max_words)
    ]


def make_chunks(
    blocks: list[dict[str, str]], target_words: int = 220, max_words: int = 320
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_heading = ""
    current_parts: list[str] = []
    current_count = 0

    def flush() -> None:
        nonlocal current_parts, current_count
        if not current_parts:
            return
        text = normalize_text(" ".join(current_parts))
        chunks.append(
            {
                "chunk_id": f"chunk_{len(chunks) + 1:04d}",
                "heading": current_heading,
                "text": text,
                "word_count": len(text.split()),
            }
        )
        current_parts = []
        current_count = 0

    for block in blocks:
        heading = block["heading"]
        for part in split_long_text(block["text"], max_words):
            part_count = len(part.split())
            heading_changed = bool(current_parts and heading != current_heading)
            would_overflow = bool(current_parts and current_count + part_count > target_words)
            if heading_changed or would_overflow:
                flush()
            current_heading = heading
            current_parts.append(part)
            current_count += part_count
            if current_count >= max_words:
                flush()
    flush()
    return chunks


def quality_checked_chunks(
    blocks: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Remove citation UI/duplicates and reject fragmentary pseudo-content."""
    content_blocks = [
        block
        for block in blocks
        if normalize_text(block.get("heading", "")).lower() not in NON_CONTENT_HEADINGS
    ]
    chunks = make_chunks(content_blocks)
    unique_chunks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in chunks:
        normalized = normalize_text(chunk["text"])
        if normalized in seen:
            continue
        seen.add(normalized)
        item = dict(chunk)
        item["chunk_id"] = f"chunk_{len(unique_chunks) + 1:04d}"
        unique_chunks.append(item)

    combined = " ".join(chunk["text"] for chunk in unique_chunks)
    if combined.count("[...]") >= 3:
        return [], "The extracted text consists primarily of truncated or redacted fragments."
    if len(combined.split()) < 30:
        return [], "Less than 30 words of substantive article content were extracted."
    return unique_chunks, None


def retrieve_top_chunks(
    claim_text: str,
    question: str,
    chunks: list[dict[str, Any]],
    top_k: int,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    documents = [f"{chunk['heading']} {chunk['text']}".strip() for chunk in chunks]
    query = f"{question} {claim_text}".strip()
    try:
        vectorizer = TfidfVectorizer(
            lowercase=True,
            ngram_range=(1, 2),
            sublinear_tf=True,
            strip_accents="unicode",
        )
        matrix = vectorizer.fit_transform(documents + [query])
        scores = (matrix[:-1] @ matrix[-1].T).toarray().ravel()
    except ValueError:
        scores = [0.0] * len(chunks)
    ranked = sorted(range(len(chunks)), key=lambda index: (-float(scores[index]), index))
    results: list[dict[str, Any]] = []
    for index in ranked[: min(top_k, len(ranked))]:
        chunk = chunks[index]
        results.append(
            {
                "rank": len(results) + 1,
                "chunk_id": chunk["chunk_id"],
                "heading": chunk["heading"],
                "text": chunk["text"],
                "word_count": chunk["word_count"],
                "retrieval_score": round(float(scores[index]), 6),
            }
        )
    return results


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def fetch_page(
    session: requests.Session,
    url: str,
    cache_dir: Path,
    timeout: float,
    refresh: bool,
    recheck_cache: bool,
) -> dict[str, Any]:
    key = url_cache_key(url)
    metadata_path = cache_dir / "clean" / f"{key}.json"
    raw_path = cache_dir / "raw" / f"{key}.html"
    if metadata_path.exists() and not refresh:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw_file = cached.get("raw_file")
        if recheck_cache and raw_file and Path(raw_file).exists():
            raw_text = Path(raw_file).read_text(encoding="utf-8", errors="replace")
            if Path(raw_file).suffix.lower() == ".md":
                title, blocks = extract_markdown_blocks(raw_text)
            else:
                title, blocks = extract_blocks(raw_text)
            cached["page_title"] = title or cached.get("page_title", "")
            if is_challenge_page(cached["page_title"], blocks):
                cached["chunks"] = []
                cached["retrieval_status"] = "blocked_content"
                cached["error"] = "Cached page is a bot/security challenge, not source content."
            else:
                chunks, quality_error = quality_checked_chunks(blocks)
                cached["chunks"] = chunks
                cached["retrieval_status"] = "success" if chunks else "insufficient_content"
                cached["error"] = quality_error
            cached["quality_rechecked_at"] = utc_now()
            write_json(metadata_path, cached)
        cached["cache_hit"] = True
        return cached

    result: dict[str, Any] = {
        "cache_key": key,
        "requested_url": url,
        "final_url": None,
        "retrieval_status": "error",
        "http_status": None,
        "content_type": None,
        "page_title": "",
        "access_method": None,
        "direct_error": None,
        "fetched_at": utc_now(),
        "cache_hit": False,
        "raw_file": None,
        "chunks": [],
        "error": None,
    }
    if not valid_web_url(url):
        result["retrieval_status"] = "invalid_url"
        result["error"] = "Only HTTP(S) URLs are supported."
        write_json(metadata_path, result)
        return result

    try:
        response = session.get(url, timeout=(10, timeout), allow_redirects=True)
        result["http_status"] = response.status_code
        result["final_url"] = response.url
        result["content_type"] = response.headers.get("content-type", "").split(";", 1)[0].lower()
        response.raise_for_status()
        if result["content_type"] not in {"text/html", "application/xhtml+xml", ""}:
            result["retrieval_status"] = "unsupported_content_type"
            result["error"] = f"Unsupported content type: {result['content_type']}"
        else:
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(response.content)
            title, blocks = extract_blocks(response.text)
            result["page_title"] = title
            result["raw_file"] = str(raw_path)
            result["access_method"] = "direct_html"
            if is_challenge_page(title, blocks):
                result["chunks"] = []
                result["retrieval_status"] = "blocked_content"
                result["error"] = "The retrieved page is a bot/security challenge, not source content."
            else:
                chunks, quality_error = quality_checked_chunks(blocks)
                result["chunks"] = chunks
                result["retrieval_status"] = "success" if chunks else "insufficient_content"
            if not result["chunks"] and result["error"] is None:
                result["error"] = quality_error or "No substantive content was extracted."
    except requests.RequestException as exc:
        result["direct_error"] = f"{type(exc).__name__}: {exc}"
        reader_url = READER_BASE_URL + url.split("://", 1)[-1]
        try:
            reader_response = session.get(reader_url, timeout=(10, max(timeout, 45)), allow_redirects=True)
            reader_response.raise_for_status()
            reader_path = cache_dir / "raw" / f"{key}.md"
            reader_path.parent.mkdir(parents=True, exist_ok=True)
            reader_path.write_bytes(reader_response.content)
            title, blocks = extract_markdown_blocks(reader_response.text)
            result["page_title"] = title
            result["raw_file"] = str(reader_path)
            result["access_method"] = "jina_reader_fallback"
            if is_challenge_page(title, blocks):
                result["chunks"] = []
                result["retrieval_status"] = "blocked_content"
                result["error"] = "Reader fallback returned a bot/security challenge, not source content."
            else:
                chunks, quality_error = quality_checked_chunks(blocks)
                result["chunks"] = chunks
                result["retrieval_status"] = "success" if chunks else "insufficient_content"
                result["error"] = None if chunks else (
                    quality_error or "Reader fallback returned no substantive content."
                )
        except requests.RequestException as reader_exc:
            result["error"] = f"{type(reader_exc).__name__}: {reader_exc}"

    write_json(metadata_path, result)
    return result


def build_preview(
    claims_payload: dict[str, Any],
    cache_dir: Path,
    limit_questions: int,
    top_k: int,
    timeout: float,
    delay: float,
    refresh: bool,
    recheck_cache: bool,
) -> dict[str, Any]:
    records = [
        record
        for record in claims_payload.get("records", [])
        if record.get("extraction_status") == "success"
    ][:limit_questions]
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})

    planned_urls = {
        source["url"]
        for record in records
        for source in record.get("source_pool", [])
        if source["citation_id"]
        in {
            citation_id
            for claim in record.get("claims", [])
            for citation_id in claim.get("local_citation_ids", [])
        }
    }

    page_cache: dict[str, dict[str, Any]] = {}
    output_records: list[dict[str, Any]] = []
    pair_count = 0
    for record in records:
        source_by_id = {source["citation_id"]: source for source in record.get("source_pool", [])}
        local_ids = sorted(
            {
                citation_id
                for claim in record.get("claims", [])
                for citation_id in claim.get("local_citation_ids", [])
            }
        )
        pages: list[dict[str, Any]] = []
        for citation_id in local_ids:
            source = source_by_id.get(citation_id)
            if source is None:
                continue
            url = source["url"]
            if url not in page_cache:
                page_cache[url] = fetch_page(
                    session, url, cache_dir, timeout, refresh, recheck_cache
                )
                page = page_cache[url]
                print(
                    f"[{len(page_cache)}/{len(planned_urls)}] "
                    f"{page['retrieval_status']} ({page.get('access_method') or 'none'}): {url}",
                    flush=True,
                )
                if delay and not page.get("cache_hit"):
                    time.sleep(delay)
            page = page_cache[url]
            pages.append(
                {
                    "citation_id": citation_id,
                    "citation_title": source.get("title"),
                    "url": url,
                    "final_url": page.get("final_url"),
                    "retrieval_status": page["retrieval_status"],
                    "http_status": page.get("http_status"),
                    "content_type": page.get("content_type"),
                    "page_title": page.get("page_title"),
                    "access_method": page.get("access_method"),
                    "direct_error": page.get("direct_error"),
                    "cache_key": page["cache_key"],
                    "chunk_count": len(page.get("chunks", [])),
                    "error": page.get("error"),
                }
            )

        pairs: list[dict[str, Any]] = []
        for claim in record.get("claims", []):
            for citation_id in claim.get("local_citation_ids", []):
                source = source_by_id.get(citation_id)
                if source is None:
                    continue
                page = page_cache[source["url"]]
                pairs.append(
                    {
                        "pair_id": f"{record['dataset_id']}::{claim['claim_id']}::{citation_id}",
                        "claim_id": claim["claim_id"],
                        "claim_text": claim["claim_text"],
                        "citation_id": citation_id,
                        "citation_url": source["url"],
                        "page_retrieval_status": page["retrieval_status"],
                        "top_chunks": retrieve_top_chunks(
                            claim["claim_text"], record["question"], page.get("chunks", []), top_k
                        ),
                    }
                )
                pair_count += 1
        output_records.append(
            {
                "dataset_id": record["dataset_id"],
                "run": record["run"],
                "question": record["question"],
                "claim_count": len(record.get("claims", [])),
                "claims_with_local_citation": sum(
                    bool(claim.get("local_citation_ids")) for claim in record.get("claims", [])
                ),
                "pages": pages,
                "claim_citation_pairs": pairs,
            }
        )

    unique_pages = list(page_cache.values())
    status_counts: dict[str, int] = {}
    for page in unique_pages:
        status = page["retrieval_status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "retrieval_method": {
            "scope": "explicit local citations only",
            "chunking": "HTML main/article paragraphs grouped by heading",
            "ranking": "TF-IDF cosine similarity over word unigrams and bigrams",
            "top_k": top_k,
        },
        "summary": {
            "record_count": len(output_records),
            "unique_page_count": len(unique_pages),
            "page_status_counts": status_counts,
            "claim_citation_pair_count": pair_count,
        },
        "records": output_records,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims_json", type=Path)
    parser.add_argument("output_json", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--limit-questions", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--recheck-cache",
        action="store_true",
        help="Re-extract and quality-check cached raw pages without downloading them again",
    )
    args = parser.parse_args()

    payload = json.loads(args.claims_json.read_text(encoding="utf-8"))
    result = build_preview(
        payload,
        args.cache_dir,
        args.limit_questions,
        args.top_k,
        args.timeout,
        args.delay,
        args.refresh,
        args.recheck_cache,
    )
    write_json(args.output_json, result)
    summary = result["summary"]
    print(
        f"Processed {summary['record_count']} questions, "
        f"{summary['unique_page_count']} unique pages, and "
        f"{summary['claim_citation_pair_count']} local claim-citation pairs."
    )
    print(f"Page statuses: {summary['page_status_counts']}")
    print(f"Output: {args.output_json}")


if __name__ == "__main__":
    main()
