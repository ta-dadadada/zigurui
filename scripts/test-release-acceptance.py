#!/usr/bin/env python3
"""検証済み配布archiveを対象OSで導入し、匿名文書と隔離環境で移行を確認する。"""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from package_release import archive_stem, validate_tag, verify

PREVIOUS = 'v0.1.0-alpha.4'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--previous', type=Path, required=True, help='公開済みalpha.4の両archiveとSHA256SUMS')
    parser.add_argument('--target', choices=['macos-arm64', 'linux-x86_64'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    validate_tag(args.tag)
    expected = {'macos-arm64': ('Darwin', 'arm64'), 'linux-x86_64': ('Linux', 'x86_64')}
    assert (platform.system(), platform.machine()) == expected[args.target], '対象OS/CPUで実行してください'
    current, previous = args.directory.resolve(), args.previous.resolve()
    verify(args.tag, current)
    verify(PREVIOUS, previous)
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('installer_tests', Path(__file__).with_name('test-install.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def prepare(directory):
        # 配布物だけを取得スタブに渡す。unameは上の実ホスト検査に対応させる。
        for path in directory.iterdir():
            shutil.copyfile(path, fixture.assets / path.name)

    def run(binary, *argv, success=True):
        result = subprocess.run([str(binary), *map(str, argv)], env=fixture.env, capture_output=True)
        assert (result.returncode == 0) == success, result.stderr.decode(errors='replace')
        return result.stdout

    def install(tag, destination):
        channel = 'prerelease' if '-' in tag else 'stable'
        result = subprocess.run(['sh', str(module.SCRIPT), '--version', tag, '--channel', channel,
                                 '--bin-dir', str(destination)], env=fixture.env, capture_output=True)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        binary = destination / 'zigurui'
        assert run(binary, '--version') == f'zigurui {tag}\n'.encode()
        return binary

    fixture = module.InstallTest()
    fixture.setUp()
    try:
        fixture.env.update(TEST_OS=expected[args.target][0], TEST_CPU=expected[args.target][1])
        for key in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
            fixture.env[f'XDG_{key}_HOME'] = str(fixture.home / key.lower())
        prepare(current)
        fresh = install(args.tag, fixture.home / 'fresh-bin')
        scripts = Path(__file__).resolve().parent
        for name in ('test-cli.py', 'test-incremental-cli.py', 'test-update.py'):
            subprocess.run([sys.executable, str(scripts / name), str(fresh)], env=fixture.env, check=True)
        for name in ('test-ranking.py', 'test-relevance.py'):
            subprocess.run([sys.executable, str(scripts / name), '--binary', str(fresh),
                            '--output', str(root / name.removesuffix('.py'))], env=fixture.env, check=True)

        prepare(previous)
        binary = install(PREVIOUS, fixture.home / 'upgrade-bin')
        notes = fixture.home / 'notes'
        notes.mkdir()
        source = notes / 'sample.md'
        source.write_text('---\ntags: [sample]\n---\n# ガラス\nＡＢＣ ガラス 断熱\n', encoding='utf-8')
        index = fixture.home / 'snapshot.idx'
        run(binary, 'index', '--mode', 'normalized', notes, index)
        cfg = Path(fixture.env['XDG_CONFIG_HOME']) / 'zigurui/config.json'
        cfg.parent.mkdir(parents=True)
        cfg.write_text('{}\n')
        protected = {p: p.read_bytes() for p in (source, index, cfg)}
        old = binary.read_bytes()
        prepare(current)
        channel = 'prerelease' if '-' in args.tag else 'stable'
        for failure in ('fetch', 'checksum'):
            if failure == 'fetch':
                fixture.env['FAIL_FETCH'] = '1'
            else:
                sums = fixture.assets / 'SHA256SUMS'
                sums.write_text(sums.read_text().replace(
                    hashlib.sha256((current / f'{archive_stem(args.tag, args.target)}.tar.gz').read_bytes()).hexdigest(), '0' * 64))
            run(binary, 'update', '--version', args.tag, '--channel', channel, success=False)
            assert binary.read_bytes() == old
            assert all(p.read_bytes() == data for p, data in protected.items())
            fixture.env.pop('FAIL_FETCH', None)
            prepare(current)
        run(binary, 'update', '--version', args.tag, '--channel', channel)
        assert run(binary, '--version') == f'zigurui {args.tag}\n'.encode()
        assert binary.read_bytes() == fresh.read_bytes()
        assert all(p.read_bytes() == data for p, data in protected.items())
        assert run(binary, 'search', '--mode', 'normalized', index, 'abc')
        assert run(binary, 'search', '--mode', 'normalized', '--frontmatter', '--where', '/tags', '"sample"', index, 'ガラス')
        assert b'"algorithm":"relevance-v1"' in run(binary, 'search', '--rank', 'relevance', '--explain', index, 'ガラス').replace(b' ', b'')
        run(binary, 'index', '--mode', 'normalized', '--incremental', notes, index)
        assert source.read_bytes() == protected[source] and cfg.read_bytes() == protected[cfg]
        # 未公開stableのバイナリを装わず、既存installer fixtureのshell実行ファイルへ切り替える。
        stable = 'v0.1.0' if args.tag != 'v0.1.0' else 'v0.1.1'
        fixture.fixture(tag=stable, target=args.target)
        before = {p: p.read_bytes() for p in (source, index, cfg)}
        run(binary, 'update', '--version', stable)
        assert run(binary, '--version') == f'zigurui {stable}\n'.encode()
        assert all(p.read_bytes() == data for p, data in before.items())
    finally:
        fixture.doCleanups()
    print('PASS: archive SHA256, native install, CLI/ranking/oracle, real alpha.4 migration, failure preservation, stable mock')


if __name__ == '__main__':
    main()
