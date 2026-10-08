# Representative ingestion review

The raw files and manifest were processed locally. No OCR or legal-text merging was performed.

| Source | Parse status | Physical PDF pages | Numbered units | Unnumbered units | Chunks |
|---|---|---:|---:|---:|---:|
| karnataka_rent_act_1999_base | needs_review | 77 | 0 | 0 | 0 |
| karnataka_rent_amendment_2011 | parsed_with_warnings | 2 | 2 | 1 | 3 |
| registration_act_1908 | parsed_with_warnings | N/A (HTML) | 113 | 1 | 132 |

All three sources were processed twice with byte-identical JSON output. All generated chunk IDs are unique within their source and text lengths stay within the configured 2,500-character maximum.

## karnataka_rent_act_1999_base

Title: Karnataka Rent Act, 1999

[Processed document](documents/karnataka_rent_act_1999_base.json) ? [Chunks](chunks/karnataka_rent_act_1999_base.chunks.json)

Warnings:

- PDF text extraction appears corrupted or missing; OCR/manual review required. No chunks created.

## karnataka_rent_amendment_2011

Title: Karnataka Rent (Amendment) Act, 2008

[Processed document](documents/karnataka_rent_amendment_2011.json) ? [Chunks](chunks/karnataka_rent_amendment_2011.chunks.json)

Warnings:

- Unusable/empty PDF pages excluded from sections and chunks: [2]; manual review required for omitted content.

Two amendment sections plus introductory material are preserved. All three chunks cite physical PDF page 1. Page 2 contains a mix of readable English and corrupted legacy-font text; it is retained in the document pages but excluded from sections/chunks.

## registration_act_1908

Title: Registration Act, 1908

[Processed document](documents/registration_act_1908.json) ? [Chunks](chunks/registration_act_1908.chunks.json)

Warnings:

- Section 77 has no heading in the source DOM; wording retained as published, manual review required.
- Section 2001 has no heading in the source DOM; wording retained as published, manual review required.
- Section 4 has no heading in the source DOM; wording retained as published, manual review required.
- 17 unanchored publisher appendix provisions retained separately as appendix_section; review their scope before retrieval.

The 114 parsed units comprise a preface, 96 publisher-anchored section elements and 17 unanchored appendix provisions. Two anchored number-only units (77 and 2001) are preserved for review and excluded from chunking. The year 2001 appears to have been turned into a section label by the publisher DOM. No correction or renumbering is inferred. Appendix provisions remain separate from main sections and carry `appendix_section` labels. They require scope review before retrieval.

## Exact example chunks

These are complete chunk objects from the generated JSON, with no paraphrasing or elision.

```json
{
  "amends_document_id": "karnataka_rent_act_1999_base",
  "amends_document_ids": null,
  "amends_sections": [
    "3(n)"
  ],
  "authority": "Government of Karnataka",
  "chapter": null,
  "chunk_id": "karnataka_rent_amendment_2011:amendment_section_1:1",
  "document_role": "amendment",
  "download_status": "downloaded",
  "filename": "karnataka_rent_amendment_2011.pdf",
  "format": "pdf",
  "jurisdiction": "Karnataka",
  "legal_document_families": null,
  "legal_document_family": "karnataka_rent_act",
  "local_path": "data/raw/pdf/karnataka_rent_amendment_2011.pdf",
  "page_end": 1,
  "page_start": 1,
  "parse_status": "parsed_with_warnings",
  "priority": 1,
  "provision_type": "amendment_section",
  "publisher": "Department of Parliamentary Affairs and Legislation",
  "section_heading": "Short title and commencement",
  "section_number": "1",
  "section_text_end": 106,
  "section_text_start": 0,
  "source_id": "karnataka_rent_amendment_2011",
  "source_type": "official_amendment",
  "text": "(1) This Act may be called the Karnataka Rent (Amendment) Act,\n2008.\n(2) It shall come into force at once.",
  "title": "Karnataka Rent (Amendment) Act, 2008",
  "url": "https://dpal.karnataka.gov.in/storage/pdf-files/28%20of%202011%20(E).pdf"
}
```

```json
{
  "amends_document_id": "karnataka_rent_act_1999_base",
  "amends_document_ids": null,
  "amends_sections": [
    "3(n)"
  ],
  "authority": "Government of Karnataka",
  "chapter": null,
  "chunk_id": "karnataka_rent_amendment_2011:amendment_section_2:1",
  "document_role": "amendment",
  "download_status": "downloaded",
  "filename": "karnataka_rent_amendment_2011.pdf",
  "format": "pdf",
  "jurisdiction": "Karnataka",
  "legal_document_families": null,
  "legal_document_family": "karnataka_rent_act",
  "local_path": "data/raw/pdf/karnataka_rent_amendment_2011.pdf",
  "page_end": 1,
  "page_start": 1,
  "parse_status": "parsed_with_warnings",
  "priority": 1,
  "provision_type": "amendment_section",
  "publisher": "Department of Parliamentary Affairs and Legislation",
  "section_heading": "Amendment of section 3",
  "section_number": "2",
  "section_text_end": 503,
  "section_text_start": 0,
  "source_id": "karnataka_rent_amendment_2011",
  "source_type": "official_amendment",
  "text": "In the Karnataka Rent Act, 1999 (Karnataka Act 34 of 2001), in\nsection 3, in clause (n), after item (ii), the following shall be inserted, namely:-\n\"(iii) A deserted wife of a tenant who has been or is entitled to be in occupation of the matrimonial home or\ntenanted premises of husband; and\n(iv) a divorced wife of a tenant who has a decree of divorce in which the right of residence in the\nmatrimonial home or tenanted premises has been incorporated as one of the conditions of the decree of\ndivorce.\"",
  "title": "Karnataka Rent (Amendment) Act, 2008",
  "url": "https://dpal.karnataka.gov.in/storage/pdf-files/28%20of%202011%20(E).pdf"
}
```

```json
{
  "amends_document_id": null,
  "amends_document_ids": null,
  "amends_sections": null,
  "authority": "Government of India",
  "chapter": "Part X – Of the Effects of Registration and Non-Registration",
  "chunk_id": "registration_act_1908:section_47:1",
  "document_role": "base_act",
  "download_status": "skipped",
  "filename": "registration_act_1908.html",
  "format": "html",
  "jurisdiction": "India",
  "legal_document_families": null,
  "legal_document_family": "registration_act",
  "local_path": "data/raw/html/registration_act_1908.html",
  "page_end": null,
  "page_start": null,
  "parse_status": "parsed_with_warnings",
  "priority": 1,
  "provision_type": "section",
  "publisher": "Indian Kanoon",
  "section_heading": "Time from which registered document operates.",
  "section_number": "47",
  "section_text_end": 194,
  "section_text_start": 0,
  "source_id": "registration_act_1908",
  "source_type": "secondary_reproduction",
  "text": "- A registered document shall operate from the time from which it would have commenced to operate if no registration thereof had been required or made, and not from the time of its registration.",
  "title": "Registration Act, 1908",
  "url": "https://indiankanoon.org/doc/1489134/"
}
```
