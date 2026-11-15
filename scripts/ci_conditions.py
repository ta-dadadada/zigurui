#!/usr/bin/env python3
"""PRの追加検証を選ぶ。省略を証明できない場合は配布確認とbenchmarkを両方実行する。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from package_release import LICENSE_NAMES

ALL = {'distribution': True, 'benchmark': True}


def select(event_name: str, paths: list[str] | None) -> dict[str, bool]:
    """通常checkとSecurityは選別しない。PRの既知の非実行文書だけを省略対象にする。"""
    if event_name != 'pull_request' or not paths:
        return ALL.copy()
    distribution = False
    for path in paths:
        parts = PurePosixPath(path).parts
        if not parts or path.startswith('/') or '..' in parts or '\x00' in path:
            return ALL.copy()
        if path in ('README.md', *LICENSE_NAMES):
            distribution = True
        elif path.endswith('.md') and parts[0] in ('docs', 'skills') and len(parts) > 1:
            continue
        else:
            # 未知の設定・依存・実装・workflow・fixture・スクリプトも全実行にする。
            return ALL.copy()
    return {'distribution': distribution, 'benchmark': False}


def git(*arguments: str) -> bytes:
    return subprocess.run(['git', *arguments], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=30).stdout


def changed_paths(event: dict, checkout_sha: str) -> list[str]:
    """PRのbaseを含むheadかつcheckoutのmerge親が一致する場合だけ、NUL区切り差分を採用する。"""
    base = event['pull_request']['base']['sha']
    head = event['pull_request']['head']['sha']
    if not all(isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value)
               for value in (base, head, checkout_sha)):
        raise ValueError('invalid revision')
    if git('rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('incomplete history')
    if git('merge-base', base, head).decode().strip() != base:
        raise ValueError('head does not contain current base')
    parents = git('rev-list', '--parents', '-n', '1', checkout_sha).decode().split()
    if parents != [checkout_sha, base, head]:
        raise ValueError('checkout is not the expected PR merge')
    data = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', base, head, '--')
    if not data or not data.endswith(b'\0'):
        raise ValueError('empty or incomplete diff')
    return [name.decode('utf-8') for name in data[:-1].split(b'\0')]


def decide(event_name: str, event_file: Path | None, checkout_sha: str) -> tuple[dict[str, bool], str]:
    if event_name != 'pull_request':
        return ALL.copy(), 'full-event'
    try:
        event = json.loads(event_file.read_text(encoding='utf-8'))
        paths = changed_paths(event, checkout_sha)
        return select(event_name, paths), 'verified-pr-diff'
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        # 個人パス・PR本文・任意のファイル名・gitの生stderrを出力しない。
        return ALL.copy(), 'unknown-full'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--github-output', type=Path, required=True)
    args = parser.parse_args()
    event_file = os.environ.get('GITHUB_EVENT_PATH')
    selected, reason = decide(os.environ.get('GITHUB_EVENT_NAME', ''),
                              Path(event_file) if event_file else None, os.environ.get('GITHUB_SHA', ''))
    with args.github_output.open('a', encoding='utf-8') as output:
        for name, enabled in selected.items():
            output.write(f'{name}={str(enabled).lower()}\n')
    print(json.dumps({**selected, 'reason': reason}, sort_keys=True))


if __name__ == '__main__':
    main()
