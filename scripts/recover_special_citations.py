#!/usr/bin/env python3
"""Recover evidence text from PMC XML, YouTube captions, and Reddit JSON."""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from capture_blocked_citations import update_retrieval_payload
from retrieve_citation_evidence import (
    normalize_text,
    quality_checked_chunks,
    url_cache_key,
    utc_now,
    write_json,
)


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
USER_AGENT = "Mozilla/5.0 (compatible; GG-AIO-Evidence-Pilot/0.1)"
PMC_RE = re.compile(r"/articles/(PMC\d+)", re.IGNORECASE)
REDDIT_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)


def classify_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in YOUTUBE_HOSTS or host.endswith(".youtube.com"):
        return "youtube"
    if host == "reddit.com" or host.endswith(".reddit.com"):
        return "reddit"
    if "pmc.ncbi.nlm.nih.gov" in host and PMC_RE.search(parsed.path):
        return "pmc"
    return None


def extract_player_response(page_html: str) -> dict[str, Any]:
    marker = "ytInitialPlayerResponse"
    marker_index = page_html.find(marker)
    if marker_index < 0:
        raise ValueError("ytInitialPlayerResponse was not found in the YouTube page.")
    json_start = page_html.find("{", marker_index)
    if json_start < 0:
        raise ValueError("YouTube player JSON did not contain an object.")
    value, _ = json.JSONDecoder().raw_decode(page_html[json_start:])
    if not isinstance(value, dict):
        raise ValueError("YouTube player response was not a JSON object.")
    return value


def select_caption_track(player: dict[str, Any]) -> dict[str, Any] | None:
    tracks = (
        player.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    if not tracks:
        return None
    english = [track for track in tracks if track.get("languageCode", "").lower().startswith("en")]
    candidates = english or tracks
    return sorted(candidates, key=lambda track: track.get("kind") == "asr")[0]


def parse_caption_json(payload: dict[str, Any]) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current_words: list[str] = []
    start_ms: int | None = None
    end_ms = 0
    for event in payload.get("events", []):
        segments = event.get("segs") or []
        text = normalize_text("".join(segment.get("utf8", "") for segment in segments))
        if not text:
            continue
        if start_ms is None:
            start_ms = int(event.get("tStartMs", 0))
        end_ms = int(event.get("tStartMs", 0)) + int(event.get("dDurationMs", 0))
        current_words.extend(text.split())
        if len(current_words) >= 120:
            blocks.append(
                {
                    "heading": f"Transcript {format_time(start_ms)}–{format_time(end_ms)}",
                    "text": " ".join(current_words),
                }
            )
            current_words = []
            start_ms = None
    if current_words:
        blocks.append(
            {
                "heading": f"Transcript {format_time(start_ms or 0)}–{format_time(end_ms)}",
                "text": " ".join(current_words),
            }
        )
    return blocks


def transcript_entries_to_blocks(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    payload = {
        "events": [
            {
                "tStartMs": int(float(entry.get("start", 0)) * 1000),
                "dDurationMs": int(float(entry.get("duration", 0)) * 1000),
                "segs": [{"utf8": entry.get("text", "")}],
            }
            for entry in entries
        ]
    }
    return parse_caption_json(payload)


def format_time(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() == "youtu.be":
        return parsed.path.strip("/") or None
    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/", 3)[2]
    return parse_qs(parsed.query).get("v", [None])[0]


def recover_youtube(
    session: requests.Session, url: str, cache_dir: Path, existing_page: dict[str, Any]
) -> dict[str, Any]:
    key = url_cache_key(url)
    raw_html_path = cache_dir / "raw" / f"{key}.html"
    if raw_html_path.exists():
        page_html = raw_html_path.read_text(encoding="utf-8", errors="replace")
    else:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        page_html = response.text
        raw_html_path.parent.mkdir(parents=True, exist_ok=True)
        raw_html_path.write_text(page_html, encoding="utf-8")
    player = extract_player_response(page_html)
    title = player.get("videoDetails", {}).get("title") or existing_page.get("page_title", "")
    video_id = youtube_video_id(url)
    if not video_id:
        raise ValueError("YouTube video ID could not be parsed.")
    vendor_dir = Path(__file__).resolve().parents[1] / ".vendor"
    if str(vendor_dir) not in sys.path:
        sys.path.insert(0, str(vendor_dir))
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=["en"])
        entries = transcript.to_raw_data()
    except Exception as exc:
        return page_record(
            url,
            key,
            "youtube_captions",
            "transcript_unavailable",
            title,
            [],
            None,
            f"{type(exc).__name__}: {exc}",
        )
    caption_path = cache_dir / "raw" / f"{key}.captions.json"
    write_json(caption_path, entries)
    chunks, quality_error = quality_checked_chunks(transcript_entries_to_blocks(entries))
    status = "success" if chunks else "transcript_unavailable"
    return page_record(
        url,
        key,
        "youtube_captions",
        status,
        title,
        chunks,
        str(caption_path),
        quality_error,
    )


def reddit_blocks(payload: Any) -> tuple[str, list[dict[str, str]]]:
    if not isinstance(payload, list) or not payload:
        return "", []
    post_data = payload[0]["data"]["children"][0]["data"]
    title = normalize_text(post_data.get("title", ""))
    blocks: list[dict[str, str]] = []
    selftext = normalize_text(post_data.get("selftext", ""))
    if selftext and selftext not in {"[deleted]", "[removed]"}:
        blocks.append({"heading": "Post", "text": selftext})
    if len(payload) > 1:
        for child in payload[1].get("data", {}).get("children", []):
            data = child.get("data", {})
            body = normalize_text(data.get("body", ""))
            if body and body not in {"[deleted]", "[removed]"}:
                blocks.append({"heading": "Comment", "text": body})
    return title, blocks


def recover_reddit(session: requests.Session, url: str, cache_dir: Path) -> dict[str, Any]:
    key = url_cache_key(url)
    thread_id_match = REDDIT_ID_RE.search(urlparse(url).path)
    if not thread_id_match:
        raise ValueError("Reddit thread ID could not be parsed.")
    api_url = f"https://www.reddit.com/comments/{thread_id_match.group(1)}.json?raw_json=1&limit=50"
    response = session.get(api_url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    raw_path = cache_dir / "raw" / f"{key}.reddit.json"
    write_json(raw_path, payload)
    title, blocks = reddit_blocks(payload)
    chunks, quality_error = quality_checked_chunks(blocks)
    return page_record(
        url,
        key,
        "reddit_json",
        "success" if chunks else "insufficient_content",
        title,
        chunks,
        str(raw_path),
        quality_error,
    )


def pmc_blocks(xml_text: str) -> tuple[str, list[dict[str, str]]]:
    soup = BeautifulSoup(xml_text, "xml")
    article_title = soup.find("article-title")
    title = normalize_text(article_title.get_text(" ", strip=True)) if article_title else ""
    body = soup.find("body")
    blocks: list[dict[str, str]] = []
    if body is not None:
        for paragraph in body.find_all("p"):
            heading_node = paragraph.find_previous("title")
            heading = normalize_text(heading_node.get_text(" ", strip=True)) if heading_node else ""
            text = normalize_text(paragraph.get_text(" ", strip=True))
            if text:
                blocks.append({"heading": heading, "text": text})
    if not blocks:
        abstract = soup.find("abstract")
        if abstract is not None:
            for paragraph in abstract.find_all("p"):
                text = normalize_text(paragraph.get_text(" ", strip=True))
                if text:
                    blocks.append({"heading": "Abstract", "text": text})
    return title, blocks


def recover_pmc(session: requests.Session, url: str, cache_dir: Path) -> dict[str, Any]:
    key = url_cache_key(url)
    match = PMC_RE.search(urlparse(url).path)
    if not match:
        raise ValueError("PMC identifier could not be parsed.")
    pmc_id = match.group(1).upper().removeprefix("PMC")
    api_url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=pmc&id={pmc_id}&retmode=xml"
    )
    response = session.get(api_url, timeout=45)
    response.raise_for_status()
    raw_path = cache_dir / "raw" / f"{key}.pmc.xml"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(response.content)
    title, blocks = pmc_blocks(response.text)
    chunks, quality_error = quality_checked_chunks(blocks)
    method = "pmc_efetch_xml" if BeautifulSoup(response.text, "xml").find("body") else "pmc_efetch_abstract"
    return page_record(
        url,
        key,
        method,
        "success" if chunks else "insufficient_content",
        title,
        chunks,
        str(raw_path),
        quality_error,
    )


def page_record(
    url: str,
    key: str,
    method: str,
    status: str,
    title: str,
    chunks: list[dict[str, Any]],
    raw_file: str | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "cache_key": key,
        "requested_url": url,
        "final_url": url,
        "retrieval_status": status,
        "http_status": None,
        "content_type": None,
        "page_title": title,
        "fetched_at": utc_now(),
        "cache_hit": False,
        "raw_file": raw_file,
        "chunks": chunks,
        "error": error,
        "access_method": method,
        "direct_error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("retrieval_json", type=Path)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.retrieval_json.read_text(encoding="utf-8"))
    pages: dict[str, dict[str, Any]] = {}
    for record in payload.get("records", []):
        for page in record.get("pages", []):
            if page.get("retrieval_status") != "success" and classify_url(page["url"]):
                pages.setdefault(page["url"], page)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"})
    for index, (url, existing_page) in enumerate(pages.items(), start=1):
        kind = classify_url(url)
        try:
            if kind == "youtube":
                recovered = recover_youtube(session, url, args.cache_dir, existing_page)
            elif kind == "reddit":
                recovered = recover_reddit(session, url, args.cache_dir)
            elif kind == "pmc":
                recovered = recover_pmc(session, url, args.cache_dir)
            else:
                continue
        except Exception as exc:
            recovered = page_record(
                url,
                url_cache_key(url),
                f"{kind}_specialized",
                "recovery_error",
                existing_page.get("page_title", ""),
                [],
                None,
                f"{type(exc).__name__}: {exc}",
            )
        cache_path = args.cache_dir / "clean" / f"{recovered['cache_key']}.json"
        write_json(cache_path, recovered)
        update_retrieval_payload(payload, url, recovered)
        write_json(args.retrieval_json, payload)
        print(
            f"[{index}/{len(pages)}] {kind}: {recovered['retrieval_status']} "
            f"({len(recovered['chunks'])} chunks) {url}",
            flush=True,
        )

    print(json.dumps(payload["summary"], indent=2), flush=True)
    print(f"Updated: {args.retrieval_json}", flush=True)


if __name__ == "__main__":
    main()
