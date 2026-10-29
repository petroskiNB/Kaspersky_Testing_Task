import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_dependencies import main


class DependencyLockTests(unittest.TestCase):
    def verify(self, lock, installed, extras=()):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as temporary:
            path = Path(temporary) / 'requirements.txt'
            path.write_text(lock, encoding='utf-8')
            distributions = [SimpleNamespace(metadata={'Name': name}, version=version)
                             for name, version in installed.items()]
            with patch('sys.argv', ['check', str(path), '--allow-extra', *extras]), \
                 patch('importlib.metadata.distributions', return_value=distributions), \
                 contextlib.redirect_stdout(io.StringIO()):
                main()

    def test_normalized_names_match_and_explicit_build_tools_allowed(self):
        self.verify('typing_extensions==1.0\n', {'typing-extensions': '1.0', 'pip': '26.0'}, ['pip'])

    def test_version_mismatch_fails(self):
        with self.assertRaises(SystemExit):
            self.verify('example==1.0\n', {'example': '2.0'})

    def test_unpinned_transitive_dependency_fails(self):
        with self.assertRaises(SystemExit):
            self.verify('example==1.0\n', {'example': '1.0', 'unlisted': '3.0'})
