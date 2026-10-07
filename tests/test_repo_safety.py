"""Offline regression tests; no GUI, model clients or network imports."""

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_safety import contains_credentials, prompt_files, resolve_repo_file


def production_functions():
    path = Path(__file__).resolve().parents[1] / 'repo_code_changer.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    namespace = {
        'os': os, 'json': json, 're': re, 'messagebox': Mock(),
        'prompt_files': prompt_files, 'resolve_repo_file': resolve_repo_file,
        'contains_credentials': contains_credentials,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
    namespace['load_custom_instructions'] = lambda: 'Test instructions.'
    return namespace


class RepositorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='repo-safety-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        self.repo.mkdir()
        self.source = self.repo / 'plugin.php'
        self.source.write_text('old line\n', encoding='utf-8')
        self.outside = Path(self.temp.name) / 'outside.php'
        self.outside.write_text('outside line\n', encoding='utf-8')
        self.functions = production_functions()

    def patch(self, path, old='old line', code='new line'):
        return {'file': path, 'action': 'replace', 'lineCode': old, 'code': code}

    def apply(self, changes):
        return self.functions['apply_all_changes'](str(self.repo), json.dumps(changes))

    def test_relative_patch_keeps_original_line_semantics(self):
        self.assertTrue(self.apply([self.patch('plugin.php')]))
        self.assertEqual(self.source.read_text(), 'new line\n')

    def test_entire_batch_is_checked_before_first_write(self):
        for path in ('../outside.php', '..\\outside.php', str(self.outside),
                     'C:\\outside.php', 'C:outside.php', '\\\\server\\share\\x.php',
                     '\\outside.php', 'plugin.php:alternate', '.git/config', 'a/../plugin.php',
                     '.git /config', '.. /outside.php'):
            with self.subTest(path=path):
                self.assertFalse(self.apply([self.patch('plugin.php'), self.patch(path)]))
                self.assertEqual(self.source.read_text(), 'old line\n')
                self.assertEqual(self.outside.read_text(), 'outside line\n')

    def test_invalid_path_types_are_rejected(self):
        for path in (None, 123, '', [], '\x00'):
            with self.subTest(path=path):
                self.assertFalse(self.apply([self.patch(path)]))
                self.assertEqual(self.source.read_text(), 'old line\n')

    def test_nested_in_repo_paths_are_supported(self):
        nested = self.repo / 'src'
        nested.mkdir()
        target = nested / 'test.js'
        target.write_text('old line\n')
        self.assertTrue(self.apply([self.patch('src\\test.js')]))
        self.assertEqual(target.read_text(), 'new line\n')

    def test_symlink_file_and_directory_cannot_escape(self):
        try:
            (self.repo / 'linked.php').symlink_to(self.outside)
            (self.repo / 'linked-dir').symlink_to(self.outside.parent, target_is_directory=True)
        except OSError as error:
            self.skipTest('Host does not permit symlink creation: ' + str(error))
        for path in ('linked.php', 'linked-dir/outside.php'):
            with self.subTest(path=path):
                self.assertFalse(self.apply([self.patch(path, 'outside line')]))
                self.assertEqual(self.outside.read_text(), 'outside line\n')
                self.assertNotIn(path, prompt_files(str(self.repo), []))

    @unittest.skipUnless(os.name == 'nt', 'Windows junction regression')
    def test_windows_junction_cannot_escape(self):
        junction = self.repo / 'junction'
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        command = ('New-Item -ItemType Junction -Path ' + quote(junction)
                   + ' -Target ' + quote(self.outside.parent) + ' | Out-Null')
        subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], check=True, capture_output=True)
        try:
            self.assertFalse(self.apply([self.patch('junction/outside.php', 'outside line')]))
            self.assertEqual(self.outside.read_text(), 'outside line\n')
            self.assertNotIn(os.path.join('junction', 'outside.php'), prompt_files(str(self.repo), []))
        finally:
            os.rmdir(junction)

    def test_ignore_rules_work_without_git_metadata(self):
        (self.repo / '.gitignore').write_text('ignored.txt\nprivate/\n*.log\n!keep.log\n')
        (self.repo / 'ignored.txt').write_text('ignored marker')
        (self.repo / 'drop.log').write_text('log marker')
        (self.repo / 'keep.log').write_text('kept marker')
        private = self.repo / 'private'
        private.mkdir()
        (private / 'payload.txt').write_text('private marker')
        nested = self.repo / 'nested'
        nested.mkdir()
        (nested / '.gitignore').write_text('hidden.txt\n')
        (nested / 'hidden.txt').write_text('nested ignored marker')
        files = prompt_files(str(self.repo), [])
        self.assertIn('plugin.php', files)
        self.assertIn('keep.log', files)
        self.assertNotIn('ignored.txt', files)
        self.assertNotIn('drop.log', files)
        self.assertNotIn(os.path.join('private', 'payload.txt'), files)
        self.assertNotIn(os.path.join('nested', 'hidden.txt'), files)
        self.assertFalse((self.repo / '.git').exists())

    def test_git_ignored_tracked_files_are_also_omitted(self):
        subprocess.run(['git', 'init', '--quiet', str(self.repo)], check=True)
        (self.repo / '.gitignore').write_text('ignored.txt\n')
        (self.repo / 'ignored.txt').write_text('ignored tracked marker')
        subprocess.run(['git', '-C', str(self.repo), 'add', '-f', 'ignored.txt'], check=True)
        files = prompt_files(str(self.repo), [])
        self.assertIn('plugin.php', files)
        self.assertNotIn('ignored.txt', files)
        self.assertFalse(any('.git' in Path(path).parts for path in files))

    def test_missing_git_does_not_export_unfiltered_files(self):
        output = self.repo / 'combined_output'
        with patch('repo_safety.subprocess.run', side_effect=FileNotFoundError):
            with self.assertRaises(RuntimeError):
                self.functions['process_repository'](str(self.repo), str(output), [], 100000, 4)
        self.assertFalse(output.exists())

    def test_prompt_excludes_credentials_binary_and_existing_output(self):
        for name in ('.env', '.env.production', 'wp-config.php', 'credentials.json', 'tls.key'):
            (self.repo / name).write_text('PRIVATE_FILE_MARKER')
        (self.repo / 'hardcoded.py').write_text('api_key = "' + 'test0123456789abcdef012345' + '"')
        (self.repo / 'key.txt').write_text('-----BEGIN PRIVATE KEY-----\nPRIVATE_KEY_MARKER')
        (self.repo / 'binary.dat').write_bytes(b'\x00BINARY_MARKER')
        output = self.repo / 'combined_output'
        output.mkdir()
        (output / 'previous.txt').write_text('PREVIOUS_PROMPT_MARKER')
        combined = self.functions['process_repository'](str(self.repo), str(output), [], 100000, 4)
        self.assertIn('old line', combined)
        for marker in ('PRIVATE_FILE_MARKER', 'test0123456789abcdef012345',
                       'PRIVATE_KEY_MARKER', 'BINARY_MARKER', 'PREVIOUS_PROMPT_MARKER'):
            self.assertNotIn(marker, combined)

    def test_placeholder_configuration_stays_available(self):
        self.assertFalse(contains_credentials('api_key = "your_example_api_key_here"'))
        self.assertFalse(contains_credentials('api_key = os.getenv("OPENAI_API_KEY")'))
        self.assertTrue(contains_credentials('token = "ghp_' + 'a' * 36 + '"'))
        self.assertTrue(contains_credentials('WALMART_API_KEY = "' + 'a' * 24 + '"'))
        self.assertTrue(contains_credentials('db_password = "' + 'a' * 24 + '"'))


if __name__ == '__main__':
    unittest.main()
