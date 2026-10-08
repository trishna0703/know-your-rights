"""Download and validate original sources without extracting their text."""

import argparse
import json
import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

# Initialize native TLS verification before importing HTTP libraries, only for
# the executable entry point. Importing this module must not alter global SSL.
if __name__ == "__main__":
    import truststore

    truststore.inject_into_ssl()

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .sources import DOCUMENT_SOURCES

LOGGER = logging.getLogger(__name__)
RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
TIMEOUT = (10, 60)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def validate_source(source: dict) -> None:
    """Check one source without changing its metadata or making requests."""
    required = {
        "id", "title", "url", "filename", "format", "authority", "publisher",
        "source_type", "document_role", "jurisdiction", "priority",
    }
    missing = required - source.keys()
    if missing:
        raise ValueError(f"Source {source.get('id', '<unknown>')}: missing fields {sorted(missing)}")
    for field in required - {"priority"}:
        if not isinstance(source[field], str) or not source[field].strip():
            raise ValueError(f"Source {source['id']}: {field} must be a nonempty string")
    if type(source["priority"]) is not int or source["priority"] < 1:
        raise ValueError(f"Source {source['id']}: priority must be a positive integer")
    if source["format"] not in {"pdf", "html"}:
        raise ValueError(f"Source {source['id']}: unsupported format {source['format']!r}")
    if source["document_role"] not in {"base_act", "amendment", "rules", "consolidated_act"}:
        raise ValueError(f"Source {source['id']}: invalid document_role")
    url = urlsplit(source["url"])
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError(f"Source {source['id']}: url must be an HTTP(S) URL")
    filename = source["filename"]
    if filename in {".", ".."} or any(character in filename for character in '/\\:'):
        raise ValueError(f"Source {source['id']}: filename must be a simple local filename")
    for field in ("legal_document_family", "amends_document_id"):
        if field in source and (not isinstance(source[field], str) or not source[field].strip()):
            raise ValueError(f"Source {source['id']}: {field} must be a nonempty string")
    for field in ("legal_document_families", "amends_document_ids", "amends_sections"):
        if field in source and (
            not isinstance(source[field], list) or not source[field]
            or any(not isinstance(item, str) or not item.strip() for item in source[field])
        ):
            raise ValueError(f"Source {source['id']}: {field} must be a nonempty list of strings")
    if source["document_role"] == "amendment":
        for singular, plural in (
            ("amends_document_id", "amends_document_ids"),
            ("legal_document_family", "legal_document_families"),
        ):
            if singular not in source and plural not in source:
                raise ValueError(f"Source {source['id']}: amendment requires {singular} or {plural}")


def validate_sources(sources: list[dict]) -> None:
    """Validate the whole configuration, including references across priorities."""
    ids = set()
    for source in sources:
        validate_source(source)
        if source["id"] in ids:
            raise ValueError(f"Duplicate source ID: {source['id']}")
        ids.add(source["id"])
    for source in sources:
        references = list(source.get("amends_document_ids", []))
        if "amends_document_id" in source:
            references.append(source["amends_document_id"])
        for reference in references:
            if reference not in ids:
                raise ValueError(f"Source {source['id']}: amendment references unknown source ID {reference!r}")


def _write_atomic(destination: Path, payload: bytes | str) -> None:
    temporary = None
    try:
        options = {"mode": "wb"} if isinstance(payload, bytes) else {
            "mode": "w", "encoding": "utf-8", "newline": ""
        }
        with NamedTemporaryFile(
            dir=destination.parent, suffix=".tmp", delete=False, **options
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retries = Retry(
        total=2,
        backoff_factor=1,
        allowed_methods={"GET"},
        status_forcelist=(429, 500, 502, 503, 504),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    return session


def _destination(source: dict) -> Path:
    for directory in ("pdf", "html"):
        (RAW_DIR / directory).mkdir(parents=True, exist_ok=True)
    source_format = source["format"]
    if source_format not in {"pdf", "html"}:
        raise ValueError(f"Unsupported source format: {source_format!r}")
    filename = source["filename"]
    if not isinstance(filename, str) or not filename or filename in {".", ".."} or any(
        character in filename for character in '/\\:'
    ):
        raise ValueError("Source filename must be a simple local filename")
    return RAW_DIR / source_format / filename


def _validate_response(response: requests.Response, source_format: str) -> None:
    content = response.content
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if not content:
        raise ValueError("Server returned an empty response")
    is_pdf = content.startswith(b"%PDF")
    if source_format == "pdf":
        if not is_pdf:
            raise ValueError(
                f"Expected PDF signature %PDF; received {content_type or 'unknown content type'}"
            )
        return
    if is_pdf or content_type == "application/pdf":
        raise ValueError("Expected HTML; received PDF")
    html = response.text
    if not re.search(r"<!doctype\s+html\b|<html\b|<head\b|<body\b", html, re.I):
        raise ValueError("Expected HTML; response does not look like HTML")
    # Detect common error/interstitial headings even when servers return HTTP 200.
    headings = re.findall(r"<(title|h1)\b[^>]*>(.*?)</\1\s*>", html, re.I | re.S)
    if any(re.search(
        r"access denied|forbidden|not found|service unavailable|internal server error|"
        r"too many requests|just a moment|attention required|captcha|"
        r"\b(?:error|403|404|429|500|502|503|504)\b",
        heading,
        re.I,
    ) for _, heading in headings) or re.search(
        r"<[^>]+\bid=[\"'](?:challenge-form|cf-challenge-running)[\"']|"
        r"verify (?:that )?you are human|checking your browser before accessing",
        html, re.I,
    ):
        raise ValueError("Expected document HTML; received an error or challenge page")


def download_source(source: dict, session: requests.Session, force: bool = False) -> Path:
    """Save one validated source; return its path, skipping existing files by default."""
    validate_source(source)
    destination = _destination(source)
    title = source.get("title", source["filename"])
    if destination.is_file() and not force:
        LOGGER.info("Skipping %s — already exists.", title)
        return destination
    LOGGER.info("Downloading %s...", title)
    with session.get(source["url"], timeout=TIMEOUT) as response:
        response.raise_for_status()
        _validate_response(response, source["format"])
        payload = response.content if source["format"] == "pdf" else response.text
    # Atomic replacement preserves an existing file if downloading/writing fails.
    _write_atomic(destination, payload)
    LOGGER.info("Saved: %s", destination)
    return destination


def download_sources(
    sources: list[dict], max_priority: int | None = None, force: bool = False
) -> None:
    """Process eligible documents independently and log a summary."""
    validate_sources(sources)
    downloaded = skipped = failed = 0
    entries = []
    with _create_session() as session:
        for source in sources:
            entry = dict(source)
            entry["local_path"] = (Path("data/raw") / source["format"] / source["filename"]).as_posix()
            entries.append(entry)
            if max_priority is not None and source["priority"] > max_priority:
                entry["download_status"] = "not_selected"
                continue
            try:
                destination = _destination(source)
                already_exists = destination.is_file() and not force
                download_source(source, session=session, force=force)
                if already_exists:
                    skipped += 1
                    entry["download_status"] = "skipped"
                else:
                    downloaded += 1
                    entry["download_status"] = "downloaded"
            except Exception as error:
                failed += 1
                entry.update(download_status="failed", error=str(error))
                LOGGER.error(
                    "Failed %s (%s) — %s",
                    source.get("title", "unknown source"), source.get("url", "unknown URL"), error,
                )
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = RAW_DIR / "manifest.json"
    _write_atomic(manifest, json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    LOGGER.info("Manifest: %s", manifest)
    LOGGER.info(
        "Download complete\n\nDownloaded: %d\nSkipped: %d\nFailed: %d",
        downloaded, skipped, failed,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-priority", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="Download every priority")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    download_sources(DOCUMENT_SOURCES, None if args.all else args.max_priority, args.force)


if __name__ == "__main__":
    main()
