# Know Your Rights ingestion

Requires Python 3.10 or newer. From the project root:

```bash
python -m pip install -r requirements.txt
python -m app.ingestion.downloader
```

The command-line entry point uses `truststore` for native operating-system TLS
certificate verification, including Windows certificate-chain handling. SSL
verification stays enabled. Importing the downloader does not change global SSL
behavior; applications using it directly can initialize `truststore` themselves
before importing HTTP libraries.

The default downloads priority-1 documents. Use `--all` for every document,
`--max-priority 2` for a priority threshold, or `--force` to replace existing files.
Original PDFs and UTF-8 HTML are saved under `data/raw/pdf/` and `data/raw/html/`.
Paths are anchored to the project directory, independent of the working directory.

Reusable functions live in `app.ingestion.downloader`; the source metadata lives
in `app.ingestion.sources`. Individual failures are logged and processing continues.
Each run reuses one HTTP session. HTTP GET requests use connect/read timeouts and
at most two retries for transient failures, honoring `Retry-After` headers.
PDF bodies must start with `%PDF`. HTML validation detects common error/challenge page titles, but cannot
guarantee that every server error or login page is recognizable.

Downloading does not extract text or perform any later RAG processing.

The complete configuration is validated before any requests, including required
fields, document roles, duplicate IDs, and amendment references. Relationships
are taken only from source metadata; amendments remain separate raw documents.
`authority` identifies the issuer and `publisher` identifies the copy's host.

Each completed run atomically writes `data/raw/manifest.json` as a JSON array,
preserving every source field and adding `local_path` and `download_status`.
Statuses are `downloaded`, `skipped`, or `failed` (with the exact `error`). Sources
excluded by the priority threshold have status `not_selected`. Failed sources
remain in the manifest even if every download fails. Invalid configuration aborts
the run before downloading and leaves any existing manifest untouched.

For individual downloads, pass a session explicitly:

```python
from app.ingestion.downloader import _create_session, download_source
from app.ingestion.sources import DOCUMENT_SOURCES

with _create_session() as session:
    download_source(DOCUMENT_SOURCES[0], session=session)
```

## Parse and chunk raw documents

Install `requirements.txt`, then process all available manifest entries:

```bash
python -m app.ingestion.parser --all
```

Or process one document:

```bash
python -m app.ingestion.parser --source-id karnataka_rent_amendment_2011
```

The default without `--all` also processes all available entries. `--manifest`
selects a different manifest file; its local paths are resolved against this
project root and must remain inside `data/raw`. Download statuses `failed` and
`not_selected` are logged and ignored. Missing raw files are logged per document,
and the remaining sources continue.

Outputs are UTF-8 JSON under `data/processed/documents/<source_id>.json` and
`data/processed/chunks/<source_id>.chunks.json`. The document retains all manifest
metadata, normalized page text, sections, extraction warnings and physical PDF
page numbers (starting at 1). HTML has no physical PDF pages; its page fields are
null. Repeated runs on identical inputs produce identical JSON and chunk IDs.

`models.py` contains dataclasses, `parser.py` extracts and identifies legal
provisions, and `chunker.py` splits within each provision. Chunk limits default
to 2,500 characters and fallback overlap to 250 characters; override them with
`--max-chunk-chars` and `--overlap-chars`. The overlap must be less than half the
maximum. Small sections stay intact. Large sections split on subsections/clauses;
only oversized individual units use the overlapping fallback splitter. Chunk
page ranges use character offsets in the section body where available.

PDF quality checks flag control characters, mostly symbols, missing text and
dense legacy Latin-1 font encodings. These heuristics are deliberately basic and
do not establish textual or legal accuracy. Mostly usable documents may retain
good pages with `parsed_with_warnings`; unusable pages are excluded, and sections
are never joined across those gaps. A `needs_review` document produces an empty
chunk list. Original extracted pages remain in its JSON for manual inspection.
No OCR is performed.

HTML parsing recognizes Indian Kanoon's `.akn-act` structure and LegitQuest's
`#judgement .act-detail-content`. Unknown DOMs fail clearly rather than parsing
the whole webpage. Inline references, legal editorial notes and clause labels
are retained. Number-only anchored headings require review and do not generate
chunks. Unanchored publisher appendix provisions are labeled `appendix_section`
and given distinct IDs; their scope must be reviewed before later retrieval.
Whitespace cleanup also restores known Windows punctuation stored as C1 controls.

Parsed files and chunk files are each written atomically. Failed parses write
an empty chunk list to replace stale chunks. No amendment merging, embeddings,
database storage, retrieval or LLM processing is performed.

Run the tests with `python -m unittest discover -s tests -v`.
