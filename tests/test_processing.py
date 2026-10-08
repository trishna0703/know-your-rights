"""Tests for legal boundaries, source fidelity, quality checks and citations."""

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
import pymupdf

from app.ingestion import parser
from app.ingestion.chunker import chunk_document
from app.ingestion.models import PageSpan, ParsedDocument, ParsedSection


def metadata(**overrides):
    entry = dict(
        id="sample", title="Sample Act", format="pdf", url="https://example.test/act.pdf",
        document_role="base_act", jurisdiction="India", authority="Government",
        publisher="Publisher", source_type="official", legal_document_family="sample_family",
        local_path="data/raw/pdf/sample.pdf", download_status="downloaded",
    )
    entry.update(overrides)
    return entry


def kanoon_html():
    return '''<nav>Login Signup Navigation</nav><div class="maindoc">
    <div class="ad_doc">Advertisement</div><span class="akn-act">
    <section class="akn-preface"><span class="akn-p">An Act to protect tenants.</span></section>
    <span class="akn-body"><section class="akn-part"><h2>Part I - Preliminary</h2>
    <section class="akn-section" id="section_1"><h3>1. Short title.</h3>
    <section class="akn-subsection"><span class="akn-num">(1)</span>
    <span class="akn-content"><span class="akn-p">This Act applies.</span></span></section></section>
    <section class="akn-section" id="section_47"><h3>47. Repairs.</h3>
    <span class="akn-content"><span class="akn-p">Landlords shall maintain the premises.</span></span>
    </section></section></span></span><aside>Unrelated judgment</aside></div><footer>Privacy</footer>'''


class ParsingTests(unittest.TestCase):
    def test_usable_and_corrupted_text(self):
        for text in ("This is normal legal wording: (1) A tenant shall pay rent.", "(a) A café is included.", "ಕನ್ನಡ ಕಾನೂನು ಪಠ್ಯ"):
            self.assertTrue(parser.is_text_usable(text))
        for text in ("", "\x01\x04\x05" * 100, "! # $ % & ( ) 5 " * 100, "\ufffd" * 100, "PÀ£ÁðlPÀ ¨ÁrUÉ " * 60):
            self.assertFalse(parser.is_text_usable(text))

    def test_whitespace_retains_labels_and_wording(self):
        self.assertEqual(parser.normalize_whitespace("  47. Repairs\r\n\r\n\r\n(1)  duty\n(a)\twork;"),
                         "47. Repairs\n\n(1) duty\n(a) work;")

    def test_chapter_sections_and_page_ranges(self):
        sections = parser.detect_sections([
            (12, "CHAPTER VII\nSPECIAL OBLIGATIONS\n47. Repairs.- (1) First clause."),
            (13, "(2) Second clause.\n48. Duties.- Another provision."),
        ])
        self.assertEqual([section.section_number for section in sections], ["47", "48"])
        self.assertEqual(sections[0].chapter, "Chapter VII - SPECIAL OBLIGATIONS")
        self.assertEqual((sections[0].page_start, sections[0].page_end), (12, 13))
        self.assertEqual(sections[0].text, "(1) First clause.\n(2) Second clause.")
        self.assertEqual([span.page for span in sections[0].page_spans], [12, 13])

    def test_rules_and_amendment_sections(self):
        rules = parser.detect_sections([(None, "Rule - 1. Title.--\n(1) These rules apply.\nRule 2\nDefinitions")], "rules")
        self.assertEqual([section.section_number for section in rules], ["1", "2"])
        self.assertTrue(all(section.provision_type == "rule" for section in rules))
        amendment = parser.detect_sections([(1, "1. Short title.- This Act applies.\n2. Amendment of section 3.- Insert clause (a).")], "amendment")
        self.assertEqual([section.provision_type for section in amendment], ["amendment_section"] * 2)

    def test_arrangement_of_sections_is_not_actual_sections(self):
        text = "Arrangement of Sections\nSections:\n1. Short title\n2. Amendment\nSTATEMENT OF OBJECTS AND REASONS\nSome background.\n1. Short title.- This Act applies.\n2. Amendment.- Insert wording."
        sections = parser.detect_sections([(1, text)], "amendment")
        self.assertEqual([section.section_number for section in sections if section.section_number], ["1", "2"])

    def test_indian_kanoon_excludes_boilerplate_and_preserves_clauses(self):
        entry = metadata(format="html", url="https://indiankanoon.org/doc/1/")
        doc = parser.parse_indian_kanoon(BeautifulSoup(kanoon_html(), "html.parser"), entry)
        text = "\n".join(section.text for section in doc.sections)
        for unwanted in ("Navigation", "Login", "Advertisement", "Unrelated judgment", "Privacy"):
            self.assertNotIn(unwanted, text)
        self.assertIn("(1)", text)
        self.assertIn("This Act applies.", text)
        self.assertEqual(doc.sections[1].chapter, "Part I - Preliminary")
        self.assertIsNone(doc.sections[1].page_start)

    def test_legitquest_uses_its_own_container(self):
        html = '<nav>Login</nav><div id="judgement"><div class="act-detail-content"><h2>Rule - 1. Title.--</h2><p>(1) These rules apply.</p></div></div><footer>Advertising</footer>'
        doc = parser.parse_legitquest(BeautifulSoup(html, "html.parser"), metadata(document_role="rules"))
        self.assertEqual(doc.sections[0].section_number, "1")
        self.assertNotIn("Login", doc.sections[0].text)
        self.assertNotIn("Advertising", doc.sections[0].text)
        with self.assertRaises(ValueError):
            parser.parse_legitquest(BeautifulSoup('<body>Full webpage</body>', 'html.parser'), metadata())
        with self.assertRaises(ValueError):
            parser.parse_indian_kanoon(BeautifulSoup('<body>Full webpage</body>', 'html.parser'), metadata())

    def test_number_only_anchored_html_section_requires_review(self):
        html = kanoon_html().replace('47. Repairs.', '47.')
        doc = parser.parse_indian_kanoon(BeautifulSoup(html, 'html.parser'), metadata())
        self.assertEqual(doc.sections[-1].parse_status, 'needs_review')
        self.assertNotIn('47', [chunk['section_number'] for chunk in chunk_document(doc)])

    def test_pdf_physical_pages_and_mostly_usable_document(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'sample.pdf'
            with pymupdf.open() as pdf:
                for text in (
                    '1. Repairs.- (1) The landlord shall keep the premises in good repair.\n' + 'Legal wording retained. ' * 12,
                    '(2) The tenant shall report necessary repairs.\n' + 'Legal wording retained. ' * 12,
                    '!!! ### $$$',
                ):
                    pdf.new_page().insert_textbox(pymupdf.Rect(40,40,550,700), text)
                pdf.save(path)
            doc = parser.parse_pdf(path, metadata())
            self.assertEqual(doc.page_count, 3)
            self.assertEqual([page.page for page in doc.pages], [1, 2, 3])
            self.assertEqual(doc.parse_status, 'parsed_with_warnings')
            self.assertEqual((doc.sections[0].page_start, doc.sections[0].page_end), (1,2))
            self.assertNotIn('!!!', doc.sections[0].text)

    def test_wholly_broken_pdf_requires_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'broken.pdf'
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((40,40), '! # $ % & ' * 20)
                pdf.save(path)
            doc = parser.parse_pdf(path, metadata())
            self.assertEqual(doc.parse_status, 'needs_review')
            self.assertEqual(chunk_document(doc), [])


class ChunkingTests(unittest.TestCase):
    def document(self, text):
        return ParsedDocument('sample', 'Sample', 'base_act', 'India', sections=[
            ParsedSection('47', 'Repairs', 'Chapter VII', text, 12, 13, 'section'),
        ])

    def test_small_sections_are_separate_chunks(self):
        doc = self.document('(1) First clause.\n(2) Second clause.')
        doc.sections.append(ParsedSection('48', 'Duties', None, 'Unrelated provision.', provision_type='section'))
        chunks = chunk_document(doc)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]['text'], doc.sections[0].text)
        self.assertEqual([chunk['section_number'] for chunk in chunks], ['47','48'])

    def test_large_section_splits_on_subsections(self):
        text = '(1) ' + 'First duty. ' * 25 + '\n(2) ' + 'Second duty. ' * 25
        chunks = chunk_document(self.document(text), max_chunk_chars=400, overlap_chars=30)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0]['text'].startswith('(1)'))
        self.assertTrue(chunks[1]['text'].startswith('(2)'))
        self.assertTrue(all(chunk['section_number'] == '47' and chunk['section_heading'] == 'Repairs' for chunk in chunks))

    def test_fallback_overlap_bound_and_page_offsets(self):
        text = '(1) ' + 'The landlord shall keep the premises in good repair. ' * 80
        doc = self.document(text)
        doc.sections[0].page_spans = [PageSpan(0, len(text)//2, 12), PageSpan(len(text)//2, len(text), 13)]
        chunks = chunk_document(doc, max_chunk_chars=400, overlap_chars=40)
        covered = set()
        for chunk in chunks:
            self.assertLessEqual(len(chunk['text']), 400)
            start, end = chunk['section_text_start'], chunk['section_text_end']
            self.assertEqual(chunk['text'], text[start:end])
            covered.update(range(start,end))
        self.assertTrue(all(index in covered for index, character in enumerate(text) if not character.isspace()))
        self.assertEqual((chunks[0]['page_start'], chunks[0]['page_end']), (12,12))
        self.assertEqual((chunks[-1]['page_start'], chunks[-1]['page_end']), (13,13))
        self.assertLess(chunks[1]['section_text_start'], chunks[0]['section_text_end'])

    def test_amendment_metadata_and_deterministic_ids(self):
        entry = metadata(document_role='amendment', legal_document_families=['family_a','family_b'],
                         amends_document_ids=['act_a','act_b'], amends_sections=['3(n)'])
        original = deepcopy(entry)
        doc = parser._new_document(entry)
        doc.sections = parser.detect_sections([(1,'2. Amendment of section 3.- Insert new wording.')], 'amendment')
        first = chunk_document(doc)
        self.assertEqual(first, chunk_document(doc))
        self.assertEqual(first[0]['amends_document_ids'], ['act_a','act_b'])
        self.assertEqual(first[0]['legal_document_families'], ['family_a','family_b'])
        self.assertEqual(first[0]['amends_sections'], ['3(n)'])
        self.assertEqual(first[0]['chunk_id'], 'sample:amendment_section_2:1')
        self.assertEqual(entry, original)

    def test_repeated_numbers_have_unique_ids(self):
        doc = self.document('First wording.')
        doc.sections.append(deepcopy(doc.sections[0]))
        chunks = chunk_document(doc)
        self.assertEqual(len({chunk['chunk_id'] for chunk in chunks}), 2)


class ProcessingTests(unittest.TestCase):
    def test_manifest_metadata_and_deterministic_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root/'data/raw/html/sample.html'
            raw.parent.mkdir(parents=True)
            raw.write_text(kanoon_html(), encoding='utf-8')
            entry = metadata(format='html', url='https://indiankanoon.org/doc/1/', local_path='data/raw/html/sample.html',
                             publisher='Manifest publisher', custom_metadata={'value': [1,2]})
            manifest = root/'data/raw/manifest.json'
            manifest.write_text(json.dumps([entry]), encoding='utf-8')
            loaded = parser.load_manifest(manifest)[0]
            outputs = root/'data/processed'
            doc = parser.process_source(loaded, project_root=root, processed_dir=outputs)
            self.assertEqual(doc.source_metadata, entry)
            document_path = outputs/'documents/sample.json'
            chunks_path = outputs/'chunks/sample.chunks.json'
            first = (document_path.read_bytes(), chunks_path.read_bytes())
            parser.process_source(loaded, project_root=root, processed_dir=outputs)
            self.assertEqual(first, (document_path.read_bytes(), chunks_path.read_bytes()))
            chunks=json.loads(chunks_path.read_text(encoding='utf-8'))
            self.assertEqual(chunks[0]['publisher'], 'Manifest publisher')
            self.assertEqual(chunks[0]['custom_metadata'], {'value':[1,2]})

    def test_failed_and_not_selected_manifest_sources_are_ignored(self):
        entries=[metadata(id='failed', download_status='failed'), metadata(id='unselected',download_status='not_selected')]
        with patch.object(parser,'load_manifest',return_value=entries), patch.object(parser,'process_source') as process:
            self.assertEqual(parser.process_manifest(), [])
            process.assert_not_called()

    def test_parse_failure_clears_stale_chunks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            path=root/'data/raw/html/sample.html'
            path.parent.mkdir(parents=True)
            path.write_text('<html>Website without legal DOM</html>', encoding='utf-8')
            entry=metadata(format='html', url='https://indiankanoon.org/doc/1/', local_path='data/raw/html/sample.html')
            outputs=root/'data/processed'
            parser._write_json(outputs/'chunks/sample.chunks.json', [{'stale':True}])
            doc=parser.process_source(entry, project_root=root, processed_dir=outputs)
            self.assertEqual(doc.parse_status, 'failed')
            self.assertEqual(json.loads((outputs/'chunks/sample.chunks.json').read_text()), [])


if __name__ == '__main__':
    unittest.main()
