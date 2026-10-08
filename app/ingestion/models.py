"""Normalized legal content and its provenance, without inferred relationships."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedPage:
    page: int
    text: str
    usable: bool


@dataclass
class PageSpan:
    """Character offsets in a section body, mapped to a physical PDF page."""

    start: int
    end: int
    page: int


@dataclass
class ParsedSection:
    section_number: str | None
    heading: str | None
    chapter: str | None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    provision_type: str | None = None
    page_spans: list[PageSpan] = field(default_factory=list)
    parse_status: str = "parsed"
    parse_warning: str | None = None


@dataclass
class ParsedDocument:
    source_id: str
    title: str
    document_role: str
    jurisdiction: str
    legal_document_family: str | None = None
    legal_document_families: list[str] | None = None
    amends_document_id: str | None = None
    amends_document_ids: list[str] | None = None
    amends_sections: list[str] | None = None
    sections: list[ParsedSection] = field(default_factory=list)
    pages: list[ParsedPage] = field(default_factory=list)
    page_count: int = 0
    parse_status: str = "parsed"
    parse_warning: str | None = None
    warnings: list[str] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
