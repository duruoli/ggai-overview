#!/usr/bin/env python3
"""Capture blocked citation pages through an interactive Chrome session."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import requests
import websocket

from retrieve_citation_evidence import (
    extract_blocks,
    is_challenge_page,
    quality_checked_chunks,
    retrieve_top_chunks,
    sanitize_captured_html,
    url_cache_key,
    utc_now,
    write_json,
)


DEFAULT_CHROME_PATHS = (
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
)


def find_chrome(explicit_path: Path | None) -> Path:
    candidates = [explicit_path] if explicit_path else list(DEFAULT_CHROME_PATHS)
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise SystemExit("Chrome/Edge/Chromium was not found. Pass its executable with --chrome-path.")


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def blocked_queue(payload: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    queue: list[dict[str, Any]] = []
    for record in payload.get("records", []):
        for page in record.get("pages", []):
            if page.get("retrieval_status") == "success" or page["url"] in seen:
                continue
            seen.add(page["url"])
            queue.append(
                {
                    "url": page["url"],
                    "citation_title": page.get("citation_title"),
                    "automatic_status": page.get("retrieval_status"),
                    "automatic_error": page.get("error"),
                    "automatic_access_method": page.get("access_method"),
                    "used_by": [
                        {
                            "dataset_id": candidate_record["dataset_id"],
                            "question": candidate_record["question"],
                            "pair_ids": [
                                pair["pair_id"]
                                for pair in candidate_record.get("claim_citation_pairs", [])
                                if pair["citation_url"] == page["url"]
                            ],
                        }
                        for candidate_record in payload.get("records", [])
                        if any(
                            pair["citation_url"] == page["url"]
                            for pair in candidate_record.get("claim_citation_pairs", [])
                        )
                    ],
                }
            )
    return queue


def is_standard_web_page(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return not (
        host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtu.be"
        or host.endswith(".reddit.com")
        or host == "reddit.com"
    )


class CDPConnection:
    def __init__(self, websocket_url: str):
        self.connection = websocket.create_connection(
            websocket_url, timeout=30, suppress_origin=True
        )
        self.message_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.message_id += 1
        message_id = self.message_id
        self.connection.send(
            json.dumps({"id": message_id, "method": method, "params": params or {}})
        )
        while True:
            response = json.loads(self.connection.recv())
            if response.get("id") != message_id:
                continue
            if "error" in response:
                raise RuntimeError(f"Chrome DevTools error: {response['error']}")
            return response.get("result", {})

    def close(self) -> None:
        self.connection.close()


def wait_for_chrome(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    endpoint = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        try:
            response = requests.get(endpoint, timeout=1)
            if response.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise RuntimeError("Chrome did not expose its local debugging endpoint in time.")


def open_tab(port: int, url: str) -> dict[str, Any]:
    endpoint = f"http://127.0.0.1:{port}/json/new?{quote(url, safe='')}"
    response = requests.put(endpoint, timeout=10)
    response.raise_for_status()
    return response.json()


def close_tab(port: int, target_id: str) -> None:
    try:
        requests.get(f"http://127.0.0.1:{port}/json/close/{target_id}", timeout=5)
    except requests.RequestException:
        pass


def capture_tab(tab: dict[str, Any]) -> tuple[str, str, str]:
    connection = CDPConnection(tab["webSocketDebuggerUrl"])
    try:
        result = connection.call(
            "Runtime.evaluate",
            {
                "expression": """(() => {
                  const clone = document.documentElement.cloneNode(true);
                  clone.querySelectorAll('script, style, noscript, svg, template, link[rel="stylesheet"]').forEach(node => node.remove());
                  return JSON.stringify({html: clone.outerHTML, url: location.href, title: document.title});
                })()""",
                "returnByValue": True,
            },
        )
        value = result.get("result", {}).get("value")
        if not isinstance(value, str):
            raise RuntimeError("Chrome did not return the page HTML.")
        page = json.loads(value)
        return page["html"], page["url"], page["title"]
    finally:
        connection.close()


def manual_page_record(
    url: str,
    final_url: str,
    browser_title: str,
    html: str,
    cache_dir: Path,
) -> dict[str, Any]:
    key = url_cache_key(url)
    raw_path = cache_dir / "manual" / "raw" / f"{key}.html"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    sanitized_html = sanitize_captured_html(html)
    raw_path.write_text(sanitized_html, encoding="utf-8")
    extracted_title, blocks = extract_blocks(sanitized_html)
    challenge = is_challenge_page(extracted_title or browser_title, blocks)
    chunks, quality_error = ([], None) if challenge else quality_checked_chunks(blocks)
    if challenge:
        status = "blocked_content"
        error = "The manually captured page is still a bot/security challenge."
    elif not chunks:
        status = "insufficient_content"
        error = quality_error or "No substantive content could be extracted from the manual capture."
    else:
        status = "success"
        error = None
    return {
        "cache_key": key,
        "requested_url": url,
        "final_url": final_url,
        "retrieval_status": status,
        "http_status": None,
        "content_type": "text/html",
        "page_title": extracted_title or browser_title,
        "fetched_at": utc_now(),
        "cache_hit": False,
        "raw_file": str(raw_path),
        "chunks": chunks,
        "error": error,
        "access_method": "manual_browser",
        "direct_error": None,
    }


def update_retrieval_payload(
    payload: dict[str, Any], url: str, page: dict[str, Any]
) -> None:
    top_k = int(payload.get("retrieval_method", {}).get("top_k", 5))
    for record in payload.get("records", []):
        for item in record.get("pages", []):
            if item["url"] != url:
                continue
            item.update(
                {
                    "final_url": page["final_url"],
                    "retrieval_status": page["retrieval_status"],
                    "http_status": page["http_status"],
                    "content_type": page["content_type"],
                    "page_title": page["page_title"],
                    "access_method": page["access_method"],
                    "direct_error": page["direct_error"],
                    "cache_key": page["cache_key"],
                    "chunk_count": len(page["chunks"]),
                    "error": page["error"],
                }
            )
        for pair in record.get("claim_citation_pairs", []):
            if pair["citation_url"] != url:
                continue
            pair["page_retrieval_status"] = page["retrieval_status"]
            pair["top_chunks"] = retrieve_top_chunks(
                pair["claim_text"], record["question"], page["chunks"], top_k
            )

    unique_pages: dict[str, str] = {}
    for record in payload.get("records", []):
        for item in record.get("pages", []):
            unique_pages[item["url"]] = item["retrieval_status"]
    status_counts: dict[str, int] = {}
    for status in unique_pages.values():
        status_counts[status] = status_counts.get(status, 0) + 1
    payload["summary"]["page_status_counts"] = status_counts
    payload["updated_at"] = utc_now()


def ensure_download_preferences(profile_dir: Path, download_dir: Path) -> None:
    preferences_path = profile_dir / "Default" / "Preferences"
    if preferences_path.exists():
        return
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        preferences_path,
        {
            "download": {
                "default_directory": str(download_dir.resolve()),
                "directory_upgrade": True,
                "prompt_for_download": False,
            }
        },
    )


def reprocess_manifest(
    payload: dict[str, Any],
    retrieval_path: Path,
    cache_dir: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    if not manifest_path.exists():
        raise SystemExit(f"Manual capture manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    processed_urls: set[str] = set()
    for capture in manifest.get("captures", []):
        if capture.get("status") == "skipped" or not capture.get("raw_file"):
            continue
        raw_path = Path(capture["raw_file"])
        if not raw_path.exists():
            continue
        html = raw_path.read_text(encoding="utf-8")
        page = manual_page_record(
            capture["url"],
            capture.get("final_url") or capture["url"],
            "",
            html,
            cache_dir,
        )
        cache_path = cache_dir / "clean" / f"{page['cache_key']}.json"
        write_json(cache_path, page)
        update_retrieval_payload(payload, capture["url"], page)
        processed_urls.add(capture["url"])
        capture.update(
            {
                "status": page["retrieval_status"],
                "cache_file": str(cache_path),
                "chunk_count": len(page["chunks"]),
                "reprocessed_at": utc_now(),
                "quality_error": page["error"],
            }
        )
        print(
            f"{capture['url']}: {page['retrieval_status']} ({len(page['chunks'])} chunks)",
            flush=True,
        )
    for record in payload.get("records", []):
        for source_page in record.get("pages", []):
            url = source_page["url"]
            if url in processed_urls or source_page.get("retrieval_status") == "success":
                continue
            raw_path = cache_dir / "manual" / "raw" / f"{url_cache_key(url)}.html"
            if not raw_path.exists():
                continue
            page = manual_page_record(
                url,
                source_page.get("final_url") or url,
                source_page.get("page_title") or "",
                raw_path.read_text(encoding="utf-8"),
                cache_dir,
            )
            cache_path = cache_dir / "clean" / f"{page['cache_key']}.json"
            write_json(cache_path, page)
            update_retrieval_payload(payload, url, page)
            processed_urls.add(url)
            manifest.setdefault("reprocessed_unlisted", []).append(
                {
                    "url": url,
                    "status": page["retrieval_status"],
                    "raw_file": str(raw_path),
                    "cache_file": str(cache_path),
                    "chunk_count": len(page["chunks"]),
                    "reprocessed_at": utc_now(),
                    "quality_error": page["error"],
                }
            )
            print(f"{url}: {page['retrieval_status']} ({len(page['chunks'])} chunks)", flush=True)
    write_json(manifest_path, manifest)
    write_json(retrieval_path, payload)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retrieval_json", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--chrome-path", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--web-only",
        action="store_true",
        help="Exclude YouTube and Reddit URLs that require dedicated extractors",
    )
    parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help="Re-run extraction and quality checks on HTML already listed in the manual manifest",
    )
    args = parser.parse_args()

    payload = json.loads(args.retrieval_json.read_text(encoding="utf-8"))
    queue = blocked_queue(payload)
    if args.web_only:
        queue = [
            item
            for item in queue
            if is_standard_web_page(item["url"])
            and item.get("automatic_access_method") != "manual_browser"
        ]
    manual_dir = args.cache_dir / "manual"
    queue_path = manual_dir / "blocked_pages_queue.json"
    manifest_path = manual_dir / "manual_capture_manifest.json"
    download_dir = manual_dir / "downloads"
    profile_dir = manual_dir / "browser_profile"
    download_dir.mkdir(parents=True, exist_ok=True)
    write_json(queue_path, {"created_at": utc_now(), "pages": queue})

    print(f"Blocked pages: {len(queue)}")
    print(f"Queue: {queue_path.resolve()}")
    print(f"Captured HTML: {(manual_dir / 'raw').resolve()}")
    print(f"Browser downloads: {download_dir.resolve()}")
    print(f"Updated retrieval output: {args.retrieval_json.resolve()}")
    if args.reprocess_existing:
        reprocess_manifest(payload, args.retrieval_json, args.cache_dir, manifest_path)
        return
    if args.prepare_only or not queue:
        return

    chrome_path = find_chrome(args.chrome_path)
    ensure_download_preferences(profile_dir, download_dir)
    port = free_local_port()
    process = subprocess.Popen(
        [
            str(chrome_path),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir.resolve()}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    manifest: dict[str, Any] = {"created_at": utc_now(), "captures": []}
    try:
        wait_for_chrome(port)
        for index, item in enumerate(queue, start=1):
            print(f"\n[{index}/{len(queue)}] {item['citation_title']}")
            print(item["url"])
            tab = open_tab(port, item["url"])
            try:
                while True:
                    choice = input(
                        "Complete verification/login in Chrome. Then press Enter to capture "
                        "(s=skip, q=stop): "
                    ).strip().lower()
                    if choice == "q":
                        write_json(manifest_path, manifest)
                        write_json(args.retrieval_json, payload)
                        return
                    if choice == "s":
                        manifest["captures"].append(
                            {"url": item["url"], "status": "skipped", "captured_at": utc_now()}
                        )
                        break
                    html, final_url, browser_title = capture_tab(tab)
                    page = manual_page_record(
                        item["url"], final_url, browser_title, html, args.cache_dir
                    )
                    if page["retrieval_status"] != "success":
                        print(f"Capture status: {page['retrieval_status']} — {page['error']}")
                        print("Continue in the same Chrome tab and try again, or enter s to skip.")
                        continue
                    cache_path = args.cache_dir / "clean" / f"{page['cache_key']}.json"
                    write_json(cache_path, page)
                    update_retrieval_payload(payload, item["url"], page)
                    write_json(args.retrieval_json, payload)
                    manifest["captures"].append(
                        {
                            "url": item["url"],
                            "final_url": final_url,
                            "status": "success",
                            "access_method": "manual_browser",
                            "raw_file": page["raw_file"],
                            "cache_file": str(cache_path),
                            "chunk_count": len(page["chunks"]),
                            "captured_at": page["fetched_at"],
                        }
                    )
                    write_json(manifest_path, manifest)
                    print(f"Saved {len(page['chunks'])} chunks and updated retrieval output.")
                    break
            finally:
                close_tab(port, tab["id"])
    finally:
        write_json(manifest_path, manifest)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    print("\nManual capture complete.")
    print(f"Manifest: {manifest_path.resolve()}")


if __name__ == "__main__":
    main()
