#!/usr/bin/env python3
"""Parse PDF files or URLs with the MinerU Agent lightweight API."""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE_URL = "https://mineru.net/api/v1/agent"
PAGE_LIMIT = 20
HTTP_TIMEOUT = 120
POLL_TIMEOUT = 300
POLL_INTERVAL = 5
MAX_RETRIES = 3
RETRY_SECONDS = 10
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class MinerUError(RuntimeError):
    """User-facing MinerU execution error."""


def load_requests() -> Any:
    try:
        import requests  # type: ignore
    except ImportError as exc:
        raise MinerUError(
            "Missing Python package 'requests'. Install it with: "
            "python3 -m pip install requests"
        ) from exc
    return requests


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a local PDF or PDF URL with MinerU and save Markdown.",
    )
    parser.add_argument("source", help="Local PDF path or http(s) PDF URL.")
    parser.add_argument("--out", default="output.md", help="Markdown output path.")
    parser.add_argument("--language", default="ch", help='MinerU language code, default "ch".')
    parser.add_argument("--pages", help='Page range, e.g. "1-20". Use commas for multiple ranges.')
    parser.add_argument("--ocr", action="store_true", help="Force OCR for scanned PDFs.")
    parser.add_argument("--table", dest="enable_table", action="store_true", default=True)
    parser.add_argument("--no-table", dest="enable_table", action="store_false")
    parser.add_argument("--formula", dest="enable_formula", action="store_true", default=True)
    parser.add_argument("--no-formula", dest="enable_formula", action="store_false")
    parser.add_argument("--timeout", type=int, default=POLL_TIMEOUT, help="Polling timeout in seconds.")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="Polling interval in seconds.")
    return parser


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"}


def split_page_ranges(pages: str | None) -> list[str | None]:
    if not pages:
        return [None]
    ranges = [item.strip() for item in pages.split(",") if item.strip()]
    if not ranges:
        raise MinerUError("--pages was provided but no page range was found.")
    return ranges


def auto_page_ranges(page_count: int) -> list[str]:
    total_chunks = math.ceil(page_count / PAGE_LIMIT)
    return [
        f"{chunk * PAGE_LIMIT + 1}-{min((chunk + 1) * PAGE_LIMIT, page_count)}"
        for chunk in range(total_chunks)
    ]


def get_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None

    try:
        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def request_with_retries(method: str, url: str, **kwargs: Any) -> Any:
    requests = load_requests()
    RequestException = requests.exceptions.RequestException
    SSLError = requests.exceptions.SSLError
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.request(method, url, timeout=HTTP_TIMEOUT, **kwargs)
        except SSLError:
            raise MinerUError(
                f"{method} {url} failed with an SSL/TLS error. "
                "Check proxy settings, disable the proxy if needed, and retry."
            )
        except RequestException as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                raise MinerUError(f"{method} {url} failed: {exc}") from exc
            time.sleep(RETRY_SECONDS * attempt)
            continue

        if response.status_code not in RETRYABLE_STATUS:
            return response

        if attempt == MAX_RETRIES:
            return response
        wait = RETRY_SECONDS * attempt
        print(f"HTTP {response.status_code}; retrying in {wait}s...", file=sys.stderr)
        time.sleep(wait)

    raise MinerUError(f"{method} {url} failed: {last_error}")


def response_json(response: Any, context: str) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise MinerUError(f"{context} returned non-JSON response: HTTP {response.status_code}") from exc


def check_http(response: Any, context: str) -> None:
    if response.status_code >= 400:
        raise MinerUError(f"{context} failed: HTTP {response.status_code} {response.text[:300]}")


def submit_url(args: argparse.Namespace, page_range: str | None) -> str:
    payload: dict[str, Any] = {
        "url": args.source,
        "language": args.language,
        "enable_table": args.enable_table,
        "is_ocr": args.ocr,
        "enable_formula": args.enable_formula,
    }
    if page_range:
        payload["page_range"] = page_range

    response = request_with_retries("POST", f"{BASE_URL}/parse/url", json=payload)
    check_http(response, "Submit URL parse task")
    result = response_json(response, "Submit URL parse task")
    if result.get("code") != 0:
        message = str(result.get("msg", "unknown error"))
        if not page_range and looks_like_page_limit(message):
            raise MinerUError(
                "MinerU rejected this URL because it likely exceeds the 20-page limit. "
                'Re-run with --pages, for example: --pages "1-20". '
                f"Original message: {message}"
            )
        raise MinerUError(f"Submit URL parse task failed: {message}")

    return str(result["data"]["task_id"])


def submit_file(args: argparse.Namespace, page_range: str | None) -> str:
    source_path = Path(args.source)
    payload: dict[str, Any] = {
        "file_name": source_path.name,
        "language": args.language,
        "enable_table": args.enable_table,
        "is_ocr": args.ocr,
        "enable_formula": args.enable_formula,
    }
    if page_range:
        payload["page_range"] = page_range

    response = request_with_retries("POST", f"{BASE_URL}/parse/file", json=payload)
    check_http(response, "Create signed upload")
    result = response_json(response, "Create signed upload")
    if result.get("code") != 0:
        raise MinerUError(f"Create signed upload failed: {result.get('msg', 'unknown error')}")

    data = result["data"]
    task_id = str(data["task_id"])
    file_url = str(data["file_url"])

    requests = load_requests()
    with source_path.open("rb") as file_obj:
        put_response = requests.put(file_url, data=file_obj, timeout=HTTP_TIMEOUT)

    if put_response.status_code not in (200, 201):
        raise MinerUError(f"File upload failed: HTTP {put_response.status_code} {put_response.text[:300]}")

    return task_id


def looks_like_page_limit(message: str) -> bool:
    lowered = message.lower()
    return "20" in lowered and ("page" in lowered or "pages" in lowered or "页" in message)


def poll_markdown(task_id: str, timeout: int, interval: int) -> str:
    labels = {
        "uploading": "uploading",
        "pending": "pending",
        "running": "running",
        "waiting-file": "waiting for upload",
    }
    start = time.time()
    while time.time() - start < timeout:
        response = request_with_retries("GET", f"{BASE_URL}/parse/{task_id}")
        check_http(response, f"Poll task {task_id}")
        result = response_json(response, f"Poll task {task_id}")
        data = result.get("data") or {}
        state = data.get("state")
        elapsed = int(time.time() - start)

        if state == "done":
            markdown_url = data.get("markdown_url")
            if not markdown_url:
                raise MinerUError(f"Task {task_id} finished without markdown_url.")
            return download_markdown(str(markdown_url))

        if state == "failed":
            raise MinerUError(f"Task {task_id} failed: {data.get('err_msg', 'unknown error')}")

        print(f"[{elapsed}s] Task {task_id}: {labels.get(state, state or 'unknown')}...", file=sys.stderr)
        time.sleep(interval)

    raise MinerUError(f"Task {task_id} timed out after {timeout}s.")


def download_markdown(markdown_url: str) -> str:
    response = request_with_retries("GET", markdown_url)
    check_http(response, "Download Markdown")
    response.encoding = response.encoding or "utf-8"
    return response.text


def select_ranges(args: argparse.Namespace) -> list[str | None]:
    if args.pages:
        return split_page_ranges(args.pages)

    if is_url(args.source):
        return [None]

    source_path = Path(args.source)
    if not source_path.exists():
        raise MinerUError(f"Local PDF not found: {source_path}")
    if not source_path.is_file():
        raise MinerUError(f"Input is not a file: {source_path}")

    page_count = get_page_count(source_path)
    if page_count and page_count > PAGE_LIMIT:
        ranges = auto_page_ranges(page_count)
        print(f"Detected {page_count} pages; splitting into: {', '.join(ranges)}", file=sys.stderr)
        return ranges

    return [None]


def parse_one(args: argparse.Namespace, page_range: str | None) -> str:
    label = page_range or "full document"
    print(f"Submitting {label}...", file=sys.stderr)
    task_id = submit_url(args, page_range) if is_url(args.source) else submit_file(args, page_range)
    return poll_markdown(task_id, timeout=args.timeout, interval=args.interval)


def write_output(path: Path, parts: list[tuple[str | None, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        content = parts[0][1]
    else:
        sections = []
        for page_range, markdown in parts:
            title = page_range or "full document"
            sections.append(f"\n\n<!-- MinerU page_range: {title} -->\n\n{markdown.strip()}\n")
        content = "\n".join(sections).strip() + "\n"
    path.write_text(content, encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    ranges = select_ranges(args)
    parsed_parts = [(page_range, parse_one(args, page_range)) for page_range in ranges]
    output_path = Path(args.out)
    write_output(output_path, parsed_parts)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output_path = run(args)
    except MinerUError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote Markdown to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
