"""Parse manifest-listed raw legal documents and save structured JSON and chunks."""

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import logging
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import unicodedata
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
import pymupdf

from .chunker import CHUNK_OVERLAP_CHARS, MAX_CHUNK_CHARS, chunk_document
from .models import PageSpan, ParsedDocument, ParsedPage, ParsedSection

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MANIFEST_PATH = PROJECT_ROOT / "data" / "raw" / "manifest.json"
SECTION_RE = re.compile(
    r"^(?:(Rule|Section)\s*[-–—]?\s*)?(\d+[A-Za-z]?)\s*\.\s+(.+)$", re.I
)
CHAPTER_RE = re.compile(r"^(CHAPTER|PART)\s*[-–—]?\s*([IVXLCDM]+|\d+)\b(.*)$", re.I)


def normalize_whitespace(text: str) -> str:
    """Retain wording, punctuation and clause labels while cleaning spacing."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Some saved HTML contains Windows punctuation as C1 controls. Restore only
    # these known punctuation code points, never alter legal words or labels.
    text = text.translate(str.maketrans({
        "\x85": "…", "\x91": "‘", "\x92": "’", "\x93": "“",
        "\x94": "”", "\x96": "–", "\x97": "—",
    }))
    text = re.sub(r"[^\S\n]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def is_text_usable(text: str) -> bool:
    """Reject obvious encoding corruption; this does not verify legal accuracy."""
    characters = [character for character in text if not character.isspace()]
    if not characters:
        return False
    total = len(characters)
    bad = sum(
        character == "\ufffd" or unicodedata.category(character)[0] == "C"
        for character in characters
    )
    letters = sum(character.isalpha() for character in characters)
    normal = sum(
        character.isalnum() or unicodedata.category(character).startswith(("P", "M"))
        or character in "₹$%+="
        for character in characters
    )
    # Legacy Indian font encodings can map script glyphs to dense Latin-1
    # accented letters/currency symbols. They pass isalpha(), but are not words.
    legacy_latin = sum(0x80 <= ord(character) <= 0xFF and not character.isspace() for character in characters)
    return (
        bad / total <= 0.02 and letters / total >= 0.35 and normal / total >= 0.85
        and legacy_latin / total <= 0.10
    )


def _new_document(metadata: dict) -> ParsedDocument:
    return ParsedDocument(
        source_id=metadata["id"], title=metadata["title"],
        document_role=metadata["document_role"], jurisdiction=metadata["jurisdiction"],
        **{field: deepcopy(metadata.get(field)) for field in (
            "legal_document_family", "legal_document_families", "amends_document_id",
            "amends_document_ids", "amends_sections",
        )},
        source_metadata=deepcopy(metadata),
    )


def _warn(document: ParsedDocument, message: str) -> None:
    document.warnings.append(message)
    document.parse_warning = "; ".join(document.warnings)
    LOGGER.warning("%s: %s", document.source_id, message)


def _split_heading(remainder: str) -> tuple[str, str]:
    # Legal PDFs commonly separate the heading from its body with .-, .— or --.
    delimiter = re.search(r"\.\s*[-–—]+|\s+[-–—]{1,2}\s+", remainder)
    if delimiter:
        return remainder[:delimiter.start()].strip(), remainder[delimiter.end():].strip()
    # A sentence-ending heading followed by the first numeric subsection.
    delimiter = re.search(r"\.\s+(?=\(\d+[A-Za-z]?\))", remainder)
    if delimiter:
        return remainder[:delimiter.start()].strip(), remainder[delimiter.end():].strip()
    return remainder.rstrip("."), ""


def detect_sections(
    pages: list[tuple[int | None, str]], document_role: str = "base_act"
) -> list[ParsedSection]:
    """Detect provision boundaries while retaining physical page provenance."""
    lines = [(page, line) for page, text in pages for line in normalize_whitespace(text).splitlines()]
    result = []
    current = None
    body = []
    chapter = None
    chapter_waiting = False
    in_contents = False

    def append_line(line, page):
        nonlocal current
        if current is None:
            current = ParsedSection(None, "Preamble / introductory material", chapter, "", provision_type="preamble")
        body.append((line, page))
        if page is not None:
            current.page_start = page if current.page_start is None else min(current.page_start, page)
            current.page_end = page if current.page_end is None else max(current.page_end, page)

    def flush():
        nonlocal current, body
        if current is not None:
            pieces = []
            offset = 0
            for line, page in body:
                if pieces:
                    offset += 1
                pieces.append(line)
                if page is not None and line:
                    current.page_spans.append(PageSpan(offset, offset + len(line), page))
                offset += len(line)
            current.text = "\n".join(pieces).strip()
            # Leading/trailing empty lines would invalidate the recorded offsets.
            # append_line is called only with nonempty lines.
            if current.text or current.section_number is not None:
                result.append(current)
        current, body = None, []

    for page, line in lines:
        line = line.strip()
        if not line or (page is not None and line.isdigit()):
            continue
        if re.fullmatch(r"Arrangement of (?:Sections|Rules)", line, re.I):
            in_contents = True
            continue
        if in_contents:
            if re.match(r"STATEMENT OF OBJECTS|KARNATAKA ACT|An Act\b|Be it enacted", line, re.I):
                in_contents = False
            else:
                continue
        chapter_match = CHAPTER_RE.match(line)
        if chapter_match:
            flush()
            chapter = f"{chapter_match[1].title()} {chapter_match[2]}"
            suffix = chapter_match[3].strip(" -–—")
            if suffix:
                chapter += " - " + suffix
            chapter_waiting = not suffix
            continue
        section_match = SECTION_RE.match(line)
        if chapter_waiting:
            chapter_waiting = False
            if not section_match and not line.startswith("(") and len(line) < 180:
                chapter += " - " + line
                continue
        # Also accept stand-alone labels such as 'Rule 1' or 'Section 2'.
        label_match = re.fullmatch(r"(Rule|Section)\s+(\d+[A-Za-z]?)", line, re.I)
        if section_match or label_match:
            flush()
            match = section_match or label_match
            number = match[2]
            heading, inline = _split_heading(match[3]) if section_match else (None, "")
            kind = "rule" if (match[1] or "").lower() == "rule" or document_role == "rules" else (
                "amendment_section" if document_role == "amendment" else "section"
            )
            current = ParsedSection(number, heading, chapter, "", page, page, kind)
            if inline:
                append_line(inline, page)
        else:
            append_line(line, page)
    flush()
    return result


def parse_pdf(path: Path, metadata: dict) -> ParsedDocument:
    document = _new_document(metadata)
    with pymupdf.open(path) as pdf:
        document.page_count = len(pdf)
        for number, page in enumerate(pdf, start=1):
            raw_text = page.get_text("text", sort=True)
            document.pages.append(ParsedPage(number, normalize_whitespace(raw_text), is_text_usable(raw_text)))
    usable = [page for page in document.pages if page.usable]
    total_chars = sum(len(re.sub(r"\s", "", page.text)) for page in document.pages)
    usable_chars = sum(len(re.sub(r"\s", "", page.text)) for page in usable)
    blank = [page.page for page in document.pages if not page.text]
    # A mostly readable document may keep good pages; wholly broken extraction
    # or predominantly image-only documents must await OCR/manual review.
    if not usable or usable_chars / max(1, total_chars) < 0.70 or len(blank) > document.page_count / 2:
        document.parse_status = "needs_review"
        _warn(document, "PDF text extraction appears corrupted or missing; OCR/manual review required. No chunks created.")
        return document
    omitted = [page.page for page in document.pages if not page.usable]
    if omitted:
        document.parse_status = "parsed_with_warnings"
        _warn(document, f"Unusable/empty PDF pages excluded from sections and chunks: {omitted}; manual review required for omitted content.")
    # Never merge a section across a missing page: that would join nonadjacent
    # legal wording and conceal the gap in citations.
    run = []
    for page in document.pages:
        if page.usable:
            run.append((page.page, page.text))
        elif run:
            document.sections.extend(detect_sections(run, document.document_role))
            run = []
    if run:
        document.sections.extend(detect_sections(run, document.document_role))
    if not any(section.section_number for section in document.sections):
        _warn(document, "No numbered sections detected; usable text retained as unnumbered units.")
        document.parse_status = "parsed_with_warnings"
    return document


def _clean_root(root: Tag) -> None:
    for unwanted in root.select(
        "script, style, template, nav, header, footer, form, button, iframe, aside, "
        ".ad_doc, .ads, .advertisement, .premium-banner, .related-documents, .related-links, .login, .signup"
    ):
        unwanted.decompose()


def _html_text(root: Tag) -> str:
    """Render block boundaries without breaking inline references/clause labels."""
    pieces = []

    def visit(node):
        if isinstance(node, NavigableString):
            # Comments are NavigableString subclasses, but are not legal wording.
            if type(node) is NavigableString:
                pieces.append(str(node))
            return
        if not isinstance(node, Tag):
            return
        classes = node.get("class", [])
        block = node.name in {"p", "section", "h1", "h2", "h3", "h4", "li", "tr"} or "akn-p" in classes
        if block or node.name == "br":
            pieces.append("\n")
        for child in node.children:
            visit(child)
        if "akn-num" in classes:
            pieces.append(" ")
        if block:
            pieces.append("\n")

    visit(root)
    return normalize_whitespace("".join(pieces))


def parse_indian_kanoon(soup: BeautifulSoup, metadata: dict) -> ParsedDocument:
    root = soup.select_one(".akn-act")
    if root is None or not root.select_one(".akn-body .akn-section"):
        raise ValueError("Indian Kanoon legal DOM not recognized: expected .akn-act with .akn-body .akn-section")
    _clean_root(root)
    document = _new_document(metadata)
    preface = root.select_one(".akn-preface")
    if preface is not None:
        document.sections.append(ParsedSection(None, "Preface", None, _html_text(preface), provision_type="preamble"))
    supplemental = 0
    for element in root.select(".akn-body .akn-section"):
        if element.find_parent(class_="akn-section") is not None:
            continue
        heading_tag = element.find(["h3", "h4"], recursive=False)
        if heading_tag is None:
            raise ValueError("Indian Kanoon section has no recognized heading")
        heading_text = normalize_whitespace(heading_tag.get_text(" ", strip=True))
        match = SECTION_RE.match(heading_text)
        number_only = re.fullmatch(r"(\d+[A-Za-z]?)\.", heading_text)
        if not match and not number_only:
            raise ValueError(f"Indian Kanoon section heading not recognized: {heading_text[:100]}")
        number = match[2] if match else number_only[1]
        heading = match[3].strip() if match else None
        if heading is None:
            document.parse_status = "parsed_with_warnings"
            _warn(document, f"Section {number} has no heading in the source DOM; wording retained as published, manual review required.")
        # The DOM establishes the heading/body boundary, so never guess it from
        # periods inside abbreviations or quoted amendment wording.
        heading_tag.decompose()
        body = _html_text(element)
        parent = element.find_parent(class_=re.compile(r"^akn-(part|chapter)$"))
        chapter_heading = parent.find(["h2", "h3"], recursive=False) if parent else None
        chapter = normalize_whitespace(chapter_heading.get_text(" ", strip=True)) if chapter_heading else None
        kind = "amendment_section" if document.document_role == "amendment" else "section"
        if not element.get("id"):
            kind = "appendix_section"
            supplemental += 1
        section = ParsedSection(number, heading, chapter, body, provision_type=kind)
        if heading is None and element.get("id"):
            # An anchored number-only heading can be malformed publisher markup
            # (the current copy even treats a footnote year as a section number).
            section.parse_status = "needs_review"
            section.parse_warning = "Anchored section has a number-only heading; excluded from chunks pending review."
        document.sections.append(section)
    if supplemental:
        document.parse_status = "parsed_with_warnings"
        _warn(document, f"{supplemental} unanchored publisher appendix provisions retained separately as appendix_section; review their scope before retrieval.")
    return document


def parse_legitquest(soup: BeautifulSoup, metadata: dict) -> ParsedDocument:
    root = soup.select_one("#judgement .act-detail-content")
    if root is None:
        raise ValueError("LegitQuest legal DOM not recognized: expected #judgement .act-detail-content")
    _clean_root(root)
    document = _new_document(metadata)
    document.sections = detect_sections([(None, _html_text(root))], document.document_role)
    if not any(section.section_number for section in document.sections):
        raise ValueError("LegitQuest content has no recognized legal provisions")
    return document


def parse_html(path: Path, metadata: dict) -> ParsedDocument:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    host = (urlsplit(metadata["url"]).hostname or "").lower().removeprefix("www.")
    if host == "indiankanoon.org":
        document = parse_indian_kanoon(soup, metadata)
    elif host == "legitquest.com":
        document = parse_legitquest(soup, metadata)
    else:
        raise ValueError(f"No legal HTML parser configured for host {host!r}")
    if not is_text_usable("\n".join(section.text or section.heading or "" for section in document.sections)):
        document.parse_status = "needs_review"
        _warn(document, "HTML legal text appears unusable; manual review required. No chunks created.")
    return document


def load_manifest(path: Path = MANIFEST_PATH) -> list[dict]:
    entries = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(entries, list):
        raise ValueError("Manifest must be a JSON array of source entries")
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Manifest entries must be objects")
        for field in ("id", "title", "document_role", "jurisdiction", "url", "format", "local_path", "download_status"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise ValueError(f"Manifest entry requires nonempty {field!r}")
        identifier = entry["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
            raise ValueError(f"Unsafe source ID: {identifier!r}")
        if identifier in seen:
            raise ValueError(f"Duplicate source ID in manifest: {identifier}")
        seen.add(identifier)
    return entries


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def process_source(
    entry: dict, project_root: Path = PROJECT_ROOT, processed_dir: Path = PROCESSED_DIR,
    max_chunk_chars: int = MAX_CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> ParsedDocument:
    """Parse one available manifest source, then save its document and chunks."""
    if entry["download_status"] not in {"downloaded", "skipped"}:
        raise ValueError(f"Source {entry['id']} is not available: {entry['download_status']}")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", entry["id"]):
        raise ValueError("Unsafe source ID")
    path = (project_root / entry["local_path"]).resolve()
    if not path.is_relative_to((project_root / "data/raw").resolve()):
        raise ValueError("Manifest local_path must be within data/raw")
    if not path.is_file():
        raise FileNotFoundError(f"Raw file does not exist: {path}")
    try:
        if entry["format"] == "pdf":
            document = parse_pdf(path, entry)
        elif entry["format"] == "html":
            document = parse_html(path, entry)
        else:
            raise ValueError(f"Unsupported source format: {entry['format']}")
    except Exception as error:
        document = _new_document(entry)
        document.parse_status = "failed"
        _warn(document, str(error))
    chunks = chunk_document(document, max_chunk_chars, overlap_chars)
    document_path = processed_dir / "documents" / f"{entry['id']}.json"
    chunks_path = processed_dir / "chunks" / f"{entry['id']}.chunks.json"
    _write_json(document_path, asdict(document))
    # Write [] for review/failed documents to replace any stale successful chunks.
    _write_json(chunks_path, chunks)
    LOGGER.info(
        "%s: status=%s pages=%d sections=%d chunks=%d\nDocument: %s\nChunks: %s",
        entry["id"], document.parse_status, document.page_count, len(document.sections), len(chunks),
        document_path, chunks_path,
    )
    return document


def process_manifest(
    manifest_path: Path = MANIFEST_PATH, source_id: str | None = None,
    max_chunk_chars: int = MAX_CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[ParsedDocument]:
    if max_chunk_chars <= 0 or not 0 <= overlap_chars < max_chunk_chars // 2:
        raise ValueError("Chunk maximum must be positive; overlap must be less than half the maximum")
    entries = load_manifest(manifest_path)
    if source_id is not None and not any(entry["id"] == source_id for entry in entries):
        raise ValueError(f"Source ID not found in manifest: {source_id}")
    documents = []
    for entry in entries:
        if source_id is not None and entry["id"] != source_id:
            continue
        if entry["download_status"] not in {"downloaded", "skipped"}:
            LOGGER.info("Ignoring %s: download_status=%s", entry["id"], entry["download_status"])
            continue
        try:
            documents.append(process_source(entry, max_chunk_chars=max_chunk_chars, overlap_chars=overlap_chars))
        except Exception as error:
            LOGGER.error("Failed processing %s: %s", entry["id"], error)
    return documents


def main() -> None:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    selection = argument_parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="Process every available manifest source (default)")
    selection.add_argument("--source-id", help="Process one manifest source")
    argument_parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    argument_parser.add_argument("--max-chunk-chars", type=int, default=MAX_CHUNK_CHARS)
    argument_parser.add_argument("--overlap-chars", type=int, default=CHUNK_OVERLAP_CHARS)
    args = argument_parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        documents = process_manifest(args.manifest, args.source_id, args.max_chunk_chars, args.overlap_chars)
    except (ValueError, OSError) as error:
        argument_parser.exit(1, f"Processing failed: {error}\n")
    LOGGER.info("Processing complete: %d documents, %d need review, %d failed", len(documents),
                sum(document.parse_status == "needs_review" for document in documents),
                sum(document.parse_status == "failed" for document in documents))


if __name__ == "__main__":
    main()
