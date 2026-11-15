#!/usr/bin/env python3
"""イベント・変更集合と実Git履歴で、検証の省略範囲と失敗時の全実行を確認する。"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

import ci_conditions as conditions

REPOSITORY = Path(__file__).resolve().parent.parent


class SelectionTest(unittest.TestCase):
    def test_events_other_than_pr_always_run_everything(self):
        for event in ('push', 'workflow_dispatch', 'workflow_call', 'merge_group', '', 'unknown'):
            for paths in (None, [], ['docs/usage.md'], ['README.md']):
                with self.subTest(event=event, paths=paths):
                    self.assertEqual(conditions.select(event, paths), {'distribution': True, 'benchmark': True})

    def test_only_known_non_packaged_markdown_can_skip_additional_jobs(self):
        self.assertEqual(conditions.select('pull_request', ['docs/usage.md', 'skills/zigurui/SKILL.md']),
                         {'distribution': False, 'benchmark': False})
        for path in ('README.md', 'LICENSE', 'LICENSE.md', 'LICENSE.txt', 'COPYING'):
            with self.subTest(path=path):
                self.assertEqual(conditions.select('pull_request', [path, 'docs/releases.md']),
                                 {'distribution': True, 'benchmark': False})

    def test_source_configuration_dependencies_scripts_and_unknown_paths_run_all(self):
        paths = ['src/main.zig', 'src/normalize.zig', 'src/ranking.zig', 'build.zig', 'build.zig.zon',
                 'mise.toml', 'lefthook.yml', 'renovate.json', 'trivy.yaml', '.gitignore',
                 'vendor/libyaml/License', 'vendor/utf8proc/utf8proc.c', 'testdata/unicode/LICENSE.txt',
                 'scripts/install.sh', 'scripts/test-relevance.py', 'scripts/benchmark.py',
                 'scripts/package_release.py', 'scripts/test-release-acceptance.py',
                 'scripts/ci_conditions.py', '.github/workflows/ci.yml', '.github/workflows/distribution.yml',
                 '.github/workflows/security.yml', '.github/workflows/release.yml',
                 '.agents/skills/new/SKILL.md', '.codex/agents/advisor.toml', 'AGENTS.md',
                 'new.md', 'docs/build.py', 'docs/README.MD', '../docs/file.md', '/docs/file.md', '']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(conditions.select('pull_request', ['docs/usage.md', path]),
                                 {'distribution': True, 'benchmark': True})
        for paths in (None, []):
            self.assertEqual(conditions.select('pull_request', paths), {'distribution': True, 'benchmark': True})

    def test_workflow_gates_keep_required_checks_and_conservative_failure_handling(self):
        ci = (REPOSITORY / '.github/workflows/ci.yml').read_text()
        security = (REPOSITORY / '.github/workflows/security.yml').read_text()
        release = (REPOSITORY / '.github/workflows/release.yml').read_text()

        def job(text, name):
            return re.search(rf'^  {name}:\n(.*?)(?=^  \w+:|\Z)', text, re.M | re.S).group(1)

        for text, name in ((ci, 'check'), (security, 'scan')):
            self.assertNotRegex(job(text, name), r'(?m)^    (if|needs):')
            self.assertNotRegex(text, r'(?m)^\s+paths(-ignore)?:')
            self.assertNotIn('pull_request_target', text)
        self.assertIn('name: Scan (${{ matrix.scanner }})', job(security, 'scan'))
        for name in ('distribution', 'benchmark'):
            self.assertIn("!cancelled() && (needs.changes.result != 'success' || "
                          f"needs.changes.outputs.{name} != 'false')", job(ci, name))
        self.assertIn('python3 scripts/test-ci-conditions.py', job(ci, 'check'))
        self.assertIn('python3 scripts/test-relevance.py', job(ci, 'check'))
        self.assertIn('needs: [distribution, security]', job(release, 'publish'))
        for text, prefix in ((ci, 'ci'), (security, 'security')):
            self.assertIn(f'group: {prefix}-${{{{ github.event_name }}}}', text)
            self.assertIn('|| github.run_id', text)
            self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)
            self.assertIn('types: [opened, synchronize, reopened, edited, ready_for_review]', text)
        self.assertIn('group: release-${{ github.ref }}-${{ github.run_id }}', release)


class GitHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='zigurui-ci-conditions-')
        self.addCleanup(self.temp.cleanup)
        self.previous_cwd = Path.cwd()
        self.addCleanup(os.chdir, self.previous_cwd)
        os.chdir(self.temp.name)
        self.git('-c', 'init.templateDir=', 'init', '-b', 'main')
        self.git('config', 'user.name', 'CI Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('config', 'core.hooksPath', '/dev/null')
        Path('docs').mkdir()
        Path('docs/base.md').write_text('base\n')
        Path('src').mkdir()
        Path('src/example.zig').write_text('fixture\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-b', 'topic')
        Path('docs/change.md').write_text('change\n')
        self.commit()
        self.head = self.git('rev-parse', 'HEAD')
        self.git('checkout', 'main')
        self.git('merge', '--no-ff', 'topic', '-m', 'synthetic PR merge')
        self.merge = self.git('rev-parse', 'HEAD')
        self.event_file = Path('.git/event.json')
        self.event = {'pull_request': {'base': {'sha': self.base}, 'head': {'sha': self.head}}}
        self.event_file.write_text(json.dumps(self.event))

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL).decode().strip()

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-m', 'fixture')

    def test_verified_merge_can_skip_only_non_packaged_docs(self):
        self.assertEqual(conditions.changed_paths(self.event, self.merge), ['docs/change.md'])
        self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge),
                         ({'distribution': False, 'benchmark': False}, 'verified-pr-diff'))

    def test_fork_events_use_the_same_verified_diff_without_api_or_extra_permissions(self):
        for action in ('opened', 'synchronize', 'reopened', 'edited', 'ready_for_review'):
            self.event['action'] = action
            self.event['pull_request']['base']['repo'] = {'full_name': 'example/project'}
            self.event['pull_request']['head']['repo'] = {'full_name': 'contributor/project', 'fork': True}
            self.event_file.write_text(json.dumps(self.event))
            with self.subTest(action=action):
                self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[0],
                                 {'distribution': False, 'benchmark': False})

    def test_cli_failure_emits_only_finite_outputs(self):
        output = Path('outputs')
        env = {**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
               'GITHUB_EVENT_PATH': str(Path('missing-event.json').resolve()), 'GITHUB_SHA': self.merge}
        result = subprocess.run([os.sys.executable, str(REPOSITORY / 'scripts/ci_conditions.py'),
                                 '--github-output', str(output)], env=env, capture_output=True, check=True)
        self.assertEqual(output.read_text(), 'distribution=true\nbenchmark=true\n')
        self.assertEqual(json.loads(result.stdout), {'distribution': True, 'benchmark': True, 'reason': 'unknown-full'})
        self.assertEqual(result.stderr, b'')

    def test_rename_from_source_to_docs_retains_both_paths(self):
        self.git('checkout', 'topic')
        Path('src/example.zig').rename('docs/moved.md')
        self.commit()
        self.event['pull_request']['head']['sha'] = self.git('rev-parse', 'HEAD')
        self.git('checkout', '--detach', self.base)
        self.git('merge', '--no-ff', self.event['pull_request']['head']['sha'], '-m', 'expected merge')
        paths = conditions.changed_paths(self.event, self.git('rev-parse', 'HEAD'))
        self.assertIn('src/example.zig', paths)
        self.assertIn('docs/moved.md', paths)
        self.assertEqual(conditions.select('pull_request', paths), {'distribution': True, 'benchmark': True})

    def test_nul_delimited_names_handle_unicode_newline_and_tabs(self):
        self.git('checkout', 'topic')
        path = 'docs/日本語\nwith\ttab.md'
        Path(path).write_text('fixture\n')
        self.commit()
        self.event['pull_request']['head']['sha'] = self.git('rev-parse', 'HEAD')
        self.git('checkout', '--detach', self.base)
        self.git('merge', '--no-ff', self.event['pull_request']['head']['sha'], '-m', 'expected merge')
        self.assertIn(path, conditions.changed_paths(self.event, self.git('rev-parse', 'HEAD')))

    def test_stale_head_unexpected_checkout_and_missing_commit_run_all(self):
        for checkout in (self.head, self.base, 'f' * 40, 'bad; shell'):
            with self.subTest(checkout=checkout):
                self.assertEqual(conditions.decide('pull_request', self.event_file, checkout),
                                 ({'distribution': True, 'benchmark': True}, 'unknown-full'))
        self.event['pull_request']['base']['sha'] = self.merge
        self.event_file.write_text(json.dumps(self.event))
        self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[0],
                         {'distribution': True, 'benchmark': True})

    def test_empty_diff_invalid_json_and_missing_event_run_all(self):
        for event in ({}, {'pull_request': None}, {'pull_request': {'base': {'sha': 'bad'}}}):
            self.event_file.write_text(json.dumps(event))
            self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[0],
                             {'distribution': True, 'benchmark': True})
        self.event_file.write_text('{bad json')
        self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[1], 'unknown-full')
        self.assertEqual(conditions.decide('pull_request', Path('missing'), self.merge)[1], 'unknown-full')
        self.assertEqual(conditions.decide('pull_request', None, self.merge)[1], 'unknown-full')

    def test_git_failures_shallow_history_and_invalid_output_run_all(self):
        original = conditions.git
        for bad in (b'', b'partial', b'\xff\0'):
            def fake(*args):
                return bad if args[0] == 'diff' else original(*args)
            with self.subTest(data=bad), mock.patch.object(conditions, 'git', side_effect=fake):
                self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[1], 'unknown-full')
        for error in (FileNotFoundError(), subprocess.CalledProcessError(128, ['git']),
                      subprocess.TimeoutExpired(['git'], 30)):
            with mock.patch.object(conditions, 'git', side_effect=error):
                self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[1], 'unknown-full')
        with mock.patch.object(conditions, 'git', return_value=b'true\n'):
            self.assertEqual(conditions.decide('pull_request', self.event_file, self.merge)[1], 'unknown-full')


if __name__ == '__main__':
    unittest.main()
