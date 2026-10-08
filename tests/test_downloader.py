import tempfile
import json
from copy import deepcopy
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from app.ingestion import downloader


def response(body, content_type="text/html", status=200):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result.encoding = "utf-8"
    result.headers["Content-Type"] = content_type
    return result


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = patch.object(downloader, "RAW_DIR", Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source = dict(
            id="example", title="Example", filename="example.pdf", format="pdf",
            url="https://example.test/file", priority=1, authority="Government",
            publisher="Publisher", source_type="official", document_role="base_act",
            jurisdiction="India", legal_document_family="example_family",
        )

    def session(self, result):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value.__enter__.return_value = result
        return patch.object(downloader, "_create_session", return_value=session)

    def test_pdf_skip_and_force(self):
        with self.session(response(b"%PDF-1.7 test", "application/octet-stream")) as factory:
            session = factory.return_value
            path = downloader.download_source(self.source, session)
            self.assertEqual(path.read_bytes(), b"%PDF-1.7 test")
            downloader.download_source(self.source, session)
            self.assertEqual(session.get.call_count, 1)
            downloader.download_source(self.source, session, force=True)
            self.assertEqual(session.get.call_count, 2)

    def test_html_utf8(self):
        self.source.update(format="html", filename="example.html")
        body = "<html><title>Act</title><body>café</body></html>".encode()
        with self.session(response(body)) as factory:
            path = downloader.download_source(self.source, factory.return_value)
        self.assertEqual(path.read_bytes(), body)

    def test_reject_mismatches_and_error_pages(self):
        for result, expected in [
            (response(b"<html>Error</html>"), "pdf"),
            (response(b"<html>Error</html>", "application/pdf"), "pdf"),
            (response(b"%PDF-1.7", "application/pdf"), "html"),
            (response(b"<html><title>Access Denied</title></html>"), "html"),
            (response(b"<html><h1>Service Unavailable</h1></html>"), "html"),
            (response(b'<html><form id="challenge-form"></form></html>'), "html"),
            (response(b"plain text"), "html"),
            (response(b"", "application/pdf"), "pdf"),
        ]:
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                downloader._validate_response(result, expected)

    def test_failed_force_preserves_existing(self):
        path = downloader._destination(self.source)
        path.write_bytes(b"original")
        with self.session(response(b"denied", status=403)) as factory, self.assertRaises(requests.HTTPError):
            downloader.download_source(self.source, factory.return_value, force=True)
        self.assertEqual(path.read_bytes(), b"original")

    def test_batch_continues_and_filters(self):
        sources = [self.source, dict(self.source, id="second", filename="second.pdf"), dict(self.source, id="third", priority=2)]
        with patch.object(downloader, "download_source", side_effect=[ValueError("bad response"), Path("second.pdf")]) as download:
            with self.assertLogs(downloader.LOGGER, level="INFO") as logs:
                downloader.download_sources(sources, max_priority=1)
        self.assertEqual(download.call_count, 2)
        self.assertIn("Downloaded: 1\nSkipped: 0\nFailed: 1", "\n".join(logs.output))
        self.assertIs(download.call_args_list[0].kwargs["session"], download.call_args_list[1].kwargs["session"])
        entries = json.loads((downloader.RAW_DIR / "manifest.json").read_text())
        self.assertEqual([entry["download_status"] for entry in entries], ["failed", "downloaded", "not_selected"])
        self.assertEqual(entries[0]["error"], "bad response")

    def test_batch_reuses_and_closes_one_session(self):
        sources = [self.source, dict(self.source, id="second", filename="second.pdf")]
        with self.session(response(b"%PDF-1.7")) as factory:
            downloader.download_sources(sources)
        factory.assert_called_once_with()
        self.assertEqual(factory.return_value.get.call_count, 2)
        factory.return_value.__exit__.assert_called_once()

    def test_invalid_configuration_prevents_all_requests(self):
        amendment = dict(self.source, id="amendment", document_role="amendment", amends_document_ids=["missing"])
        cases = [
            [self.source, dict(self.source)],
            [self.source, amendment],
            [dict(self.source, document_role="invalid")],
            [dict(self.source, document_role="amendment")],
            [{key: value for key, value in self.source.items() if key != "publisher"}],
            [dict(amendment, amends_document_ids="example")],
        ]
        for sources in cases:
            with self.subTest(sources=sources), patch.object(downloader, "_create_session") as factory:
                with self.assertRaises(ValueError):
                    downloader.download_sources(sources, max_priority=1)
                factory.assert_not_called()

    def test_manifest_preserves_metadata_and_relationships_for_skipped_sources(self):
        sources = deepcopy(downloader.DOCUMENT_SOURCES)
        original = deepcopy(sources)
        for source in sources:
            downloader._destination(source).write_bytes(b"existing")
        with patch.object(downloader, "_create_session") as factory:
            downloader.download_sources(sources)
        factory.return_value.__enter__.return_value.get.assert_not_called()
        entries = json.loads((downloader.RAW_DIR / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sources, original)
        self.assertEqual(len(entries), len(sources))
        for source, entry in zip(sources, entries):
            self.assertEqual({key: entry[key] for key in source}, source)
            self.assertEqual(entry["download_status"], "skipped")
            self.assertEqual(entry["local_path"], f"data/raw/{source['format']}/{source['filename']}")

    def test_retry_policy_is_bounded(self):
        with downloader._create_session() as session:
            retry = session.get_adapter("https://").max_retries
            self.assertEqual(retry.total, 2)
            self.assertTrue(retry.respect_retry_after_header)
            self.assertIn(429, retry.status_forcelist)
            self.assertNotIn(403, retry.status_forcelist)


if __name__ == "__main__":
    unittest.main()
