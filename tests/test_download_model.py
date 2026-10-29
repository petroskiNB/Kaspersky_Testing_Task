import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from downloader.download_model import MARKER, prepare_model, safe_error_metadata


class DownloaderTests(unittest.TestCase):
    revision = "a" * 40

    def test_error_metadata_excludes_exception_text_and_credentials(self):
        import json
        error = PermissionError(13, 'PRIVATE_TOKEN https://example.com/private')
        metadata = safe_error_metadata(error)
        self.assertEqual(metadata['error_type'], 'PermissionError')
        self.assertEqual(metadata['errno'], 13)
        self.assertNotIn('PRIVATE_TOKEN', json.dumps(metadata))
        self.assertNotIn('example.com', json.dumps(metadata))

    def fake_download(self, **kwargs):
        directory = Path(kwargs["local_dir"])
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            (directory / name).write_text("{}", encoding="utf-8")
        (directory / "model.safetensors").write_bytes(b"fake weights for unit tests")

    def test_first_download_and_second_start_without_network(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temporary) / "qwen"
            download = Mock(side_effect=self.fake_download)
            prepare_model("Qwen/test", self.revision, directory, download)
            download.assert_called_once()
            offline = Mock(side_effect=AssertionError("No network allowed"))
            prepare_model("Qwen/test", self.revision, directory, offline)
            offline.assert_not_called()
            self.assertTrue((directory / MARKER).exists())

    def test_interrupted_download_has_no_ready_marker_and_can_retry(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temporary) / "qwen"
            with self.assertRaises(RuntimeError):
                prepare_model("Qwen/test", self.revision, directory, Mock(side_effect=RuntimeError()))
            self.assertFalse((directory / MARKER).exists())
            prepare_model("Qwen/test", self.revision, directory, self.fake_download)
            self.assertTrue((directory / MARKER).exists())

    def test_same_size_corruption_triggers_repair(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temporary) / "qwen"
            prepare_model("Qwen/test", self.revision, directory, self.fake_download)
            weights = directory / "model.safetensors"
            weights.write_bytes(b"X" * weights.stat().st_size)
            download = Mock(side_effect=self.fake_download)
            prepare_model("Qwen/test", self.revision, directory, download)
            download.assert_called_once()

    def test_changed_revision_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary, contextlib.redirect_stdout(io.StringIO()):
            directory = Path(temporary) / "qwen"
            prepare_model("Qwen/test", self.revision, directory, self.fake_download)
            with self.assertRaises(ValueError):
                prepare_model("Qwen/test", "b" * 40, directory, self.fake_download)
