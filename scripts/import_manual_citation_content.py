#!/usr/bin/env python3
"""Import manually downloaded citation pages into the evidence-retrieval pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from capture_blocked_citations import manual_page_record, update_retrieval_payload
from retrieve_citation_evidence import (
    extract_markdown_blocks,
    is_challenge_page,
    normalize_text,
    quality_checked_chunks,
    sanitize_captured_html,
    url_cache_key,
    utc_now,
    write_json,
)


SUPPORTED_SUFFIXES = (".html", ".htm", ".mhtml", ".mht", ".md", ".txt")
WEAK_SUCCESS_METHODS = {"pmc_efetch_abstract"}


def unique_pages(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pages: dict[str, dict[str, Any]] = {}
    for record in payload.get("records", []):
        for page in record.get("pages", []):
            pages.setdefault(page["url"], page)
    return pages


def used_by(payload: dict[str, Any], url: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        pair_ids = [
            pair["pair_id"]
            for pair in record.get("claim_citation_pairs", [])
            if pair["citation_url"] == url
        ]
        if pair_ids:
            result.append(
                {
                    "dataset_id": record["dataset_id"],
                    "question": record["question"],
                    "pair_ids": pair_ids,
                }
            )
    return result


def import_queue(payload: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for url, page in unique_pages(payload).items():
        failed = page.get("retrieval_status") != "success"
        weak = page.get("access_method") in WEAK_SUCCESS_METHODS
        if not failed and not weak:
            continue
        key = url_cache_key(url)
        queue.append(
            {
                "url": url,
                "citation_title": page.get("citation_title"),
                "current_status": page.get("retrieval_status"),
                "current_access_method": page.get("access_method"),
                "current_error": page.get("error"),
                "cache_key": key,
                "save_as": f"{key}.html",
                "reason": "retrieval_failed" if failed else "abstract_only_upgrade",
                "used_by": used_by(payload, url),
            }
        )
    return queue


def find_import_file(import_dir: Path, key: str) -> Path | None:
    matches = [import_dir / f"{key}{suffix}" for suffix in SUPPORTED_SUFFIXES]
    existing = [path for path in matches if path.is_file()]
    if len(existing) > 1:
        names = ", ".join(str(path) for path in existing)
        raise ValueError(f"Multiple import files found for {key}: {names}")
    return existing[0] if existing else None


def extract_mhtml_html(path: Path) -> str:
    message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    candidates = []
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        content = part.get_content()
        if isinstance(content, bytes):
            content = content.decode(part.get_content_charset() or "utf-8", errors="replace")
        candidates.append(str(content))
    if not candidates:
        raise ValueError("The MHTML file contains no text/html part.")
    return max(candidates, key=len)


def declared_page_url(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one("link[rel='canonical'][href]")
    if canonical:
        return str(canonical.get("href"))
    open_graph = soup.select_one("meta[property='og:url'][content]")
    if open_graph:
        return str(open_graph.get("content"))
    return None


def text_page_record(url: str, path: Path, cache_dir: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    title, blocks = extract_markdown_blocks(text)
    chunks, quality_error = quality_checked_chunks(blocks)
    key = url_cache_key(url)
    raw_path = cache_dir / "manual" / "raw" / f"{key}{path.suffix.lower()}"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")
    return {
        "cache_key": key,
        "requested_url": url,
        "final_url": url,
        "retrieval_status": "success" if chunks else "insufficient_content",
        "http_status": None,
        "content_type": "text/plain" if path.suffix.lower() == ".txt" else "text/markdown",
        "page_title": title,
        "fetched_at": utc_now(),
        "cache_hit": False,
        "raw_file": str(raw_path),
        "chunks": chunks,
        "error": None if chunks else quality_error,
        "access_method": "manual_text_import",
        "direct_error": None,
    }


def is_reddit_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host == "reddit.com" or host.endswith(".reddit.com")


def extract_reddit_html_blocks(html: str) -> tuple[str, list[dict[str, str]]]:
    """Extract a Reddit post and its rendered comments from a saved Shreddit page."""
    soup = BeautifulSoup(html, "html.parser")

    def rendered_text(node: Any) -> str:
        text = normalize_text(node.get_text(" ", strip=True))
        if text:
            return text
        # BeautifulSoup deliberately hides descendants of HTML <template> from
        # get_text(); reparsing the selected fragment exposes Reddit's SSR text.
        fragment = BeautifulSoup(node.decode(), "html.parser")
        return normalize_text(fragment.get_text(" ", strip=True))

    post = soup.select_one("shreddit-post")
    title = ""
    blocks: list[dict[str, str]] = []
    if post is not None:
        title = normalize_text(post.get("post-title", ""))
        post_body = post.select_one("[property='schema:articleBody']")
        if post_body is None:
            post_body = post.select_one("[slot='text-body']")
        if post_body is not None:
            text = rendered_text(post_body)
            if text and text not in {"[deleted]", "[removed]"}:
                blocks.append({"heading": title or "Post", "text": text})

    for comment in soup.select("shreddit-comment"):
        body = comment.select_one("[slot='comment']")
        if body is None:
            continue
        text = rendered_text(body)
        if not text or text in {"[deleted]", "[removed]"}:
            continue
        author = normalize_text(comment.get("author", ""))
        blocks.append(
            {
                "heading": f"Comment by {author}" if author else "Comment",
                "text": text,
            }
        )
    return title, blocks


def reddit_html_page_record(url: str, html: str, cache_dir: Path) -> dict[str, Any]:
    # Reddit's server-rendered comments may live inside <template> elements. Extract
    # them before the generic sanitizer removes executable/template containers.
    title, blocks = extract_reddit_html_blocks(html)
    sanitized_html = sanitize_captured_html(html)
    challenge = is_challenge_page(title, blocks)
    chunks, quality_error = ([], None) if challenge else quality_checked_chunks(blocks)
    key = url_cache_key(url)
    raw_path = cache_dir / "manual" / "raw" / f"{key}.html"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(sanitized_html, encoding="utf-8")
    if challenge:
        status = "blocked_content"
        error = "The imported Reddit page is a bot/security challenge."
    elif chunks:
        status = "success"
        error = None
    else:
        status = "insufficient_content"
        error = quality_error or "No Reddit post or comment text could be extracted."
    return {
        "cache_key": key,
        "requested_url": url,
        "final_url": url,
        "retrieval_status": status,
        "http_status": None,
        "content_type": "text/html",
        "page_title": title,
        "fetched_at": utc_now(),
        "cache_hit": False,
        "raw_file": str(raw_path),
        "chunks": chunks,
        "error": error,
        "access_method": "manual_reddit_html_import",
        "direct_error": None,
    }


def html_page_record(url: str, path: Path, cache_dir: Path) -> tuple[dict[str, Any], str | None]:
    if path.suffix.lower() in {".mhtml", ".mht"}:
        html = extract_mhtml_html(path)
        method = "manual_mhtml_import"
    else:
        html = path.read_text(encoding="utf-8", errors="replace")
        method = "manual_html_import"
    declared_url = declared_page_url(html)
    if is_reddit_url(url):
        page = reddit_html_page_record(url, html, cache_dir)
        page["final_url"] = declared_url or url
    else:
        page = manual_page_record(url, declared_url or url, "", html, cache_dir)
        page["access_method"] = method
    return page, declared_url


def import_one(url: str, path: Path, cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        page = text_page_record(url, path, cache_dir)
        declared_url = None
    elif suffix in {".html", ".htm", ".mhtml", ".mht"}:
        page, declared_url = html_page_record(url, path, cache_dir)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    audit = {
        "url": url,
        "source_file": str(path),
        "source_sha256": digest,
        "declared_page_url": declared_url,
        "status": page["retrieval_status"],
        "access_method": page["access_method"],
        "raw_file": page["raw_file"],
        "chunk_count": len(page["chunks"]),
        "error": page["error"],
        "imported_at": page["fetched_at"],
    }
    return page, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retrieval_json", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Create the import queue and directory without processing files",
    )
    args = parser.parse_args()

    payload = json.loads(args.retrieval_json.read_text(encoding="utf-8"))
    manual_dir = args.cache_dir / "manual"
    import_dir = manual_dir / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    queue_path = import_dir / "import_queue.json"
    manifest_path = import_dir / "import_manifest.json"
    queue = import_queue(payload)
    write_json(queue_path, {"created_at": utc_now(), "pages": queue})

    print(f"Manual import candidates: {len(queue)}")
    print(f"Queue: {queue_path.resolve()}")
    print(f"Place downloaded files in: {import_dir.resolve()}")
    for item in queue:
        print(f"- {item['save_as']} <- {item['url']} ({item['reason']})")
    if args.prepare_only:
        return

    all_pages = unique_pages(payload)
    imports: list[dict[str, Any]] = []
    imported_count = 0
    for url, current_page in all_pages.items():
        key = url_cache_key(url)
        try:
            source_path = find_import_file(import_dir, key)
            if source_path is None:
                continue
            page, audit = import_one(url, source_path, args.cache_dir)
            audit["previous_status"] = current_page.get("retrieval_status")
            audit["previous_access_method"] = current_page.get("access_method")
            if page["retrieval_status"] == "success":
                cache_path = args.cache_dir / "clean" / f"{key}.json"
                write_json(cache_path, page)
                update_retrieval_payload(payload, url, page)
                audit["cache_file"] = str(cache_path)
                imported_count += 1
            imports.append(audit)
            print(f"{url}: {page['retrieval_status']} ({len(page['chunks'])} chunks)")
        except Exception as exc:
            imports.append(
                {
                    "url": url,
                    "source_file": str(import_dir / f"{key}.*"),
                    "status": "import_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "imported_at": utc_now(),
                }
            )
            print(f"{url}: import_error: {type(exc).__name__}: {exc}")

    write_json(manifest_path, {"created_at": utc_now(), "imports": imports})
    write_json(args.retrieval_json, payload)
    print(f"Successfully imported pages: {imported_count}")
    print(f"Manifest: {manifest_path.resolve()}")
    print(f"Updated retrieval output: {args.retrieval_json.resolve()}")


if __name__ == "__main__":
    main()
