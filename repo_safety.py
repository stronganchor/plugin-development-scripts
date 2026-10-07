"""Filesystem boundaries and file selection for repository prompts."""

import ntpath
import os
import re
import subprocess
import tempfile


PRIVATE_DIRS = {'.git', '.hg', '.svn', '.ssh', '.aws', '.azure', 'credentials', 'secrets'}
PRIVATE_NAMES = {
    '.npmrc', '.pypirc', 'auth.json', 'credentials', 'credentials.json',
    'secrets', 'last_combine_path.txt', 'last_apply_path.txt',
}
SECRET_PATTERNS = re.compile(
    r'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'
    r'|\bAKIA[0-9A-Z]{16}\b'
    r'|\bgh[pousr]_[A-Za-z0-9_]{20,}\b'
    r'|\bgithub_pat_[A-Za-z0-9_]{30,}\b'
    r'|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}\b'
)
ASSIGNED_SECRET = re.compile(
    r'''(?i)\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|secret(?:[_-]?key)?|password|token)\b["']?\s*[:=]\s*["']([A-Za-z0-9+/=_-]{16,})["']'''
)


def resolve_repo_file(repo_path, relative_path):
    """Reject absolute, traversal, metadata and escaping symlink/junction paths."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError('A repository-relative file path is required.')
    drive, _ = ntpath.splitdrive(relative_path)
    parts = relative_path.replace('\\', '/').split('/')
    if (drive or relative_path.startswith(('/', '\\')) or '\x00' in relative_path
            or any(part == '..' or ':' in part for part in parts)
            or any(part and part != '.' and part != part.rstrip(' .') for part in parts)
            or any(part.casefold() in {'.git', '.hg', '.svn'} for part in parts)):
        raise ValueError('Patch paths must stay inside the selected repository.')
    root = os.path.realpath(repo_path)
    target = os.path.realpath(os.path.join(root, *parts))
    try:
        contained = os.path.normcase(os.path.commonpath([root, target])) == os.path.normcase(root)
    except ValueError:
        contained = False
    if not contained or os.path.normcase(target) == os.path.normcase(root):
        raise ValueError('File resolves outside the selected repository.')
    return target


def is_private_path(relative_path):
    parts = relative_path.replace('\\', '/').casefold().split('/')
    name = parts[-1]
    return (
        any(part in PRIVATE_DIRS for part in parts)
        or name in PRIVATE_NAMES
        or name.startswith(('.env', 'wp-config', 'id_rsa', 'id_ed25519', 'id_ecdsa', 'secrets.', 'credentials.', 'service-account'))
        or name.endswith(('.pem', '.key', '.p12', '.pfx'))
    )


def contains_credentials(content):
    if SECRET_PATTERNS.search(content):
        return True
    for match in ASSIGNED_SECRET.finditer(content):
        value = match.group(1).casefold()
        if not any(word in value for word in ('example', 'placeholder', 'changeme', 'your_', 'xxxx')):
            return True
    return False


def prompt_files(repo_path, skip_dirs, output_dir=None):
    """Use Git's ignore parser for both checkouts and downloaded ZIP sources."""
    root = os.path.realpath(repo_path)
    excluded_output = os.path.realpath(output_dir) if output_dir else None
    skipped = {name.casefold() for name in skip_dirs} | PRIVATE_DIRS
    candidates = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = [name for name in dirs if name.casefold() not in skipped
                   and not os.path.islink(os.path.join(current, name))
                   and os.path.realpath(os.path.join(current, name)) != excluded_output]
        for name in files:
            relative = os.path.relpath(os.path.join(current, name), root)
            if is_private_path(relative):
                continue
            try:
                target = resolve_repo_file(root, relative)
            except ValueError:
                continue
            if os.path.isfile(target):
                candidates.append(relative)
    if not candidates:
        return []

    payload = b''.join(path.replace('\\', '/').encode('utf-8') + b'\0' for path in candidates)
    command = ['git', '-C', root, 'check-ignore', '--no-index', '--stdin', '-z']
    try:
        result = subprocess.run(command, input=payload, capture_output=True, check=False)
        if result.returncode not in (0, 1):
            # Archives have no .git; a temporary Git directory lets Git read
            # their nested .gitignore rules without changing the source tree.
            with tempfile.TemporaryDirectory(prefix='repo-prompt-ignore-') as git_dir:
                subprocess.run(['git', 'init', '--bare', '--quiet', git_dir], check=True, capture_output=True)
                result = subprocess.run(
                    ['git', '-C', root, '--git-dir=' + git_dir, '--work-tree=' + root,
                     'check-ignore', '--no-index', '--stdin', '-z'],
                    input=payload, capture_output=True, check=False,
                )
        if result.returncode not in (0, 1):
            raise RuntimeError('Git could not check repository ignore rules.')
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError('Git is required to safely select repository prompt files.') from error
    ignored = {path.decode('utf-8') for path in result.stdout.split(b'\0') if path}
    return sorted(path for path in candidates if path.replace('\\', '/') not in ignored)
