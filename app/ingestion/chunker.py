"""Split within legal provisions; never combine separate sections."""

import re

from .models import ParsedDocument, ParsedSection

MAX_CHUNK_CHARS = 2500
CHUNK_OVERLAP_CHARS = 250


def _fallback_ranges(text: str, start: int, end: int, maximum: int, overlap: int):
    while start < end:
        stop = min(start + maximum, end)
        if stop < end:
            # Prefer a word boundary, but guarantee progress for unbroken text.
            boundary = max(text.rfind(" ", start + maximum // 2, stop),
                           text.rfind("\n", start + maximum // 2, stop))
            if boundary > start:
                stop = boundary
        yield start, stop
        if stop == end:
            break
        start = max(start + 1, stop - overlap)


def _section_ranges(text: str, maximum: int, overlap: int):
    if len(text) <= maximum:
        yield 0, len(text)
        return
    # Numeric subsections first; letter/roman clauses if there are no subsections.
    starts = [match.start() for match in re.finditer(r"(?m)^\s*\(\d+[A-Za-z]?\)\s*", text)]
    if not starts:
        starts = [match.start() for match in re.finditer(r"(?m)^\s*\([a-z]+\)\s*", text)]
    boundaries = sorted({0, *starts, len(text)})
    for start, end in zip(boundaries, boundaries[1:]):
        if end - start <= maximum:
            yield start, end
        else:
            yield from _fallback_ranges(text, start, end, maximum, overlap)


def _page_range(section: ParsedSection, start: int, end: int):
    pages = [span.page for span in section.page_spans if span.start < end and span.end > start]
    return (min(pages), max(pages)) if pages else (section.page_start, section.page_end)


def chunk_document(
    parsed_document: ParsedDocument,
    max_chunk_chars: int = MAX_CHUNK_CHARS,
    overlap_chars: int = CHUNK_OVERLAP_CHARS,
) -> list[dict]:
    """Return deterministic chunks with source, section and page provenance."""
    if max_chunk_chars <= 0 or not 0 <= overlap_chars < max_chunk_chars // 2:
        raise ValueError("Chunk maximum must be positive; overlap must be less than half the maximum")
    if parsed_document.parse_status not in {"parsed", "parsed_with_warnings"}:
        return []
    chunks = []
    seen = {}
    for index, section in enumerate(parsed_document.sections, start=1):
        if section.parse_status != "parsed":
            continue
        text = section.text or section.heading or ""
        if not text.strip():
            continue
        unit = f"{section.provision_type or 'section'}_{section.section_number or 'unnumbered_' + str(index)}"
        seen[unit] = seen.get(unit, 0) + 1
        if seen[unit] > 1:
            unit += f"_occurrence_{seen[unit]}"
        for number, (start, end) in enumerate(_section_ranges(text, max_chunk_chars, overlap_chars), start=1):
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start == end:
                continue
            page_start, page_end = _page_range(section, start, end)
            chunk = dict(parsed_document.source_metadata)
            chunk.pop("id", None)
            chunk.update(
                chunk_id=f"{parsed_document.source_id}:{unit}:{number}",
                source_id=parsed_document.source_id, title=parsed_document.title,
                document_role=parsed_document.document_role, jurisdiction=parsed_document.jurisdiction,
                legal_document_family=parsed_document.legal_document_family,
                legal_document_families=parsed_document.legal_document_families,
                amends_document_id=parsed_document.amends_document_id,
                amends_document_ids=parsed_document.amends_document_ids,
                amends_sections=parsed_document.amends_sections,
                chapter=section.chapter, section_number=section.section_number,
                section_heading=section.heading, provision_type=section.provision_type,
                page_start=page_start, page_end=page_end, text=text[start:end],
                section_text_start=start, section_text_end=end,
                parse_status=parsed_document.parse_status,
            )
            chunks.append(chunk)
    return chunks
