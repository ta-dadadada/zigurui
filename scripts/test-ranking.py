#!/usr/bin/env python3
"""匿名合成文書で順位・既定順・検索集合・抜粋・snapshotを検証する。出力は削除せず保持する。"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=Path('zig-out/bin/zigurui'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    docs = root / 'notes'
    docs.mkdir()
    texts = {
        'a.md': 'ガラス 断熱\n',
        'b.md': 'ガラス 断熱\n# ガラス\n',
        'c.md': 'ガラス 断熱\n## 断熱\n',
        'd.md': '```md\n# ガラス\n```\n断熱\n',
        'e.md': '---\n# ガラス\ntags: [住宅]\n---\nガラス 断熱\n',
        'f.md': '# ｶﾞﾗｽ\n断熱\n',
        'g.md': '# ガラス\n# ガラス\n断熱\n',
        'h.md': '> # ガラス\n断熱\n',
        'i.md': 'ガラス\n===\n断熱\n',
        'j.md': '# 無関係\n',
        'k.md': '---\n# ガラス\n断熱\n',
        'l.md': '   ~~~~lang\n# ガラス\n   ~~~~\n断熱\n',
    }
    for name, text in texts.items():
        (docs / name).write_text(text, encoding='utf-8')
    initial = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in docs.iterdir()}
    env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'), XDG_DATA_HOME=str(root / 'data'))
    commands = []

    def run(*argv, success=True):
        result = subprocess.run([str(binary), *map(str, argv)], env=env, capture_output=True, text=True, check=False)
        commands.append((list(map(str, argv)), result.returncode))
        assert (result.returncode == 0) == success, (argv, result.stderr)
        return result.stdout

    def names(output):
        return [Path(line.split(':', 1)[0]).name for line in output.splitlines()]

    index = root / 'saved.idx'
    run('index', '--mode', 'normalized', docs, index)
    index_hash = hashlib.sha256(index.read_bytes()).hexdigest()
    for mode in ['literal', 'normalized']:
        for all_terms in [False, True]:
            opts = ['--mode', mode] + (['--all'] if all_terms else [])
            terms = ['ガラス', '断熱'] if all_terms else ['ガラス']
            plain = run('search', *opts, index, *terms)
            ranked = run('search', *opts, '--rank', 'heading', index, *terms)
            expected_priority = ['b.md'] + (['c.md'] if all_terms else []) + (['f.md'] if mode == 'normalized' else []) + ['g.md']
            expected_rest = sorted(set(texts) - set(expected_priority) - {'j.md'} - ({'f.md'} if mode == 'literal' else set()))
            assert names(ranked) == expected_priority + expected_rest, (mode, all_terms, names(ranked))
            assert sorted(plain.splitlines()) == sorted(ranked.splitlines()), '文書・位置・抜粋が変化'
            assert run('search', *opts, '--rank', 'path', index, *terms) == plain
            assert run('scan', *opts, '--rank', 'heading', docs, *terms) == ranked
            assert names(plain) == sorted(names(plain))
    assert run('search', '--rank', 'heading', index, '該当なし') == ''
    assert names(run('search', '--rank', 'heading', '--where', 'file:/name', '"a.md"', index, 'ガラス')) == ['a.md']
    run('search', '--rank', 'invalid', index, 'ガラス', success=False)
    run('search', '--rank', 'heading', '--rank', 'path', index, 'ガラス', success=False)
    run('index', '--rank', 'heading', docs, index, success=False)
    run('search', '--frontmatter', '--rank', 'heading', index, 'ガラス', success=False)
    assert initial == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in docs.iterdir()}
    assert index_hash == hashlib.sha256(index.read_bytes()).hexdigest()

    valid = root / 'metadata-notes'
    valid.mkdir()
    for name, tag, heading in [('a.md', '住宅', ''), ('b.md', '住宅', '# ガラス\n'), ('c.md', 'その他', '# ガラス\n')]:
        (valid / name).write_text(f'---\ntags: [{tag}]\n---\nガラス 断熱\n{heading}', encoding='utf-8')
    metadata_index = root / 'metadata.idx'
    run('index', '--mode', 'normalized', valid, metadata_index)
    opts = ['--mode', 'normalized', '--frontmatter', '--where', '/tags', '"住宅"']
    plain = run('search', *opts, metadata_index, 'ガラス')
    ranked = run('search', *opts, '--rank', 'heading', metadata_index, 'ガラス')
    assert names(ranked) == ['b.md', 'a.md']
    assert sorted(plain.splitlines()) == sorted(ranked.splitlines())
    assert run('scan', *opts, '--rank', 'heading', valid, 'ガラス') == ranked

    # snapshot評価用に、この合成文書だけを意図して変更する。
    before = run('search', '--rank', 'heading', index, 'ガラス')
    (docs / 'b.md').write_text('ガラス 断熱\n', encoding='utf-8')
    assert run('search', '--rank', 'heading', index, 'ガラス') == before
    assert names(run('scan', '--rank', 'heading', docs, 'ガラス'))[0] == 'g.md'
    assert index_hash == hashlib.sha256(index.read_bytes()).hexdigest()

    import json
    (root / 'result.json').write_text(json.dumps({'passed': True, 'commands': commands, 'snapshot_source_mutation': 'b.mdのみ意図して変更', 'performance_claim': False}, ensure_ascii=False, indent=2) + '\n')
    print(f'OK: {len(commands)} commands; ranking, parity, filters, snapshot, preservation')


if __name__ == '__main__':
    main()
