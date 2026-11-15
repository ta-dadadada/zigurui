#!/usr/bin/env python3
"""匿名合成入力だけで通常/説明、独立区間oracle、設定、snapshot互換を検証する。"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import subprocess

from relevance_oracle import field, transform


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary', type=Path, default=Path('zig-out/bin/zigurui'))
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    binary, root = args.binary.resolve(), args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    notes = root / 'notes'
    notes.mkdir()
    cfg = root / 'config' / 'zigurui' / 'config.json'
    cfg.parent.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('XDG_')}
    env.update(HOME=str(root / 'home'), XDG_CONFIG_HOME=str(cfg.parent.parent))
    commands = 0

    def config(value):
        cfg.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def run(*argv, success=True):
        nonlocal commands
        commands += 1
        r = subprocess.run([str(binary), *map(str, argv)], capture_output=True, env=env)
        assert (r.returncode == 0) == success, (argv, r.stderr.decode(errors='replace'))
        if not success:
            assert r.stdout == b'', 'エラー時に部分結果を出力'
        return r

    def explain(terms, mode='literal', source=None, command='scan', **kw):
        return json.loads(run(command, '--mode', mode, '--all', '--rank', 'relevance', '--explain', source or notes, *terms, **kw).stdout)

    texts = {
        'a.md': 'ガラス\n断熱\n',
        'b.md': 'ガラス___断熱 ガラス断熱\n# ガラス断熱\n',
        'c.md': '断熱ガラス\n',
        'd.md': 'ガラス___断熱\n',
        'e.md': '---\nlabel: [ガラス, 断熱, 10, false, null]\n---\nガラス 断熱\n',
        'f.md': '---\nlabel: [ガラス断熱, ガラス断熱]\n---\nガラス 断熱\n',
        'g.md': '---\nnested: [{label: ガラス断熱}, {label: 断熱}, {label: 10}]\n---\nガラス 断熱\n',
        'ガラス断熱.md': 'ガラス 断熱\n',
        'unicode.md': 'ＡＢＣ ｶﾞﾗｽ か\u3099 Straße\n',
        'missing.md': '無関係\n',
    }
    for name, text in texts.items():
        (notes / name).write_text(text, encoding='utf-8')
    original = {n: hashlib.sha256((notes / n).read_bytes()).hexdigest() for n in texts}
    config({})
    index = root / 'saved.idx'
    run('index', '--mode', 'normalized', notes, index)
    saved = hashlib.sha256(index.read_bytes()).hexdigest()
    for mode in ['literal', 'normalized']:
        for terms in [['ガラス', '断熱'], ['ガラス'], ['ガラス', 'ガラス', '断熱']]:
            opts = ['--mode', mode, '--all']
            baseline = run('search', *opts, index, *terms).stdout
            ranked = run('search', *opts, '--rank', 'relevance', index, *terms).stdout
            data = explain(terms, mode, index, 'search')
            assert data == explain(terms, mode)
            names = [Path(r['path']).name for r in data['results']]
            assert [Path(l.split(':', 1)[0]).name for l in ranked.decode().splitlines()] == names
            assert sorted(baseline.splitlines()) == sorted(ranked.splitlines())
            expected = sorted(n for n, t in texts.items() if all(transform(q, mode) in transform(t, mode) for q in terms))
            assert sorted(names) == expected
            for result in data['results']:
                assert result['total'] == sum(f['total'] for f in result['fields'])
                assert result['term_count'] == len(set(transform(t, mode) for t in terms))
                for f in result['fields']:
                    assert f['total'] == f['coverage_score'] + f['proximity_score'] + f['ordered_score']
            assert [(r['total'], r['path']) for r in data['results']] == sorted([(r['total'], r['path']) for r in data['results']], key=lambda x: (-x[0], x[1]))
    assert hashlib.sha256(index.read_bytes()).hexdigest() == saved
    assert original == {n: hashlib.sha256((notes / n).read_bytes()).hexdigest() for n in texts}

    # 型・配列・任意経路を実装から独立した値リストで照合する。
    for pointer, values in [('/label', {'e.md': ['ガラス', '断熱'], 'f.md': ['ガラス断熱'] * 2}),
                            ('/nested/label', {'g.md': ['ガラス断熱', '断熱']})]:
        config({'relevance': {'fields': [{'kind': 'metadata', 'pointer': pointer, 'weight': 2}]}})
        for r in explain(['ガラス', '断熱'])['results']:
            expected = field(values.get(Path(r['path']).name, []), ['ガラス', '断熱'], 'literal')
            for k, v in expected.items():
                assert r['fields'][0][k] == v, (k, r, expected)
    # 完全一致filterは正規化採点と独立。
    config({'relevance': {'fields': [{'kind': 'metadata', 'pointer': '/label'}]}})
    exact = run('search', '--mode', 'normalized', '--frontmatter', '--where', '/label', '"ｶﾞﾗｽ"', '--rank', 'relevance', index, 'ガラス').stdout
    assert exact == b''
    assert explain(['ガラス', '断熱'], source=index, command='search') == explain(['ガラス', '断熱'])

    # 短文の全出現直積oracle。乱数seed固定、実装の最短窓算法を写さない。
    small = root / 'oracle'
    small.mkdir()
    rng = random.Random(731)
    cases = [('a_____b_ab', ['a', 'b']), ('ba', ['a', 'b']), ('abcd', ['abcd', 'ab']),
             ('ababa', ['aba', 'bab']), ('abcdef___x', ['abcdef', 'abc', 'x']),
             ('ガラス断熱', ['ガラス', '断熱']), ('ｶﾞﾗｽ断熱', ['ガラス', '断熱']),
             ('か\u3099Ａ', ['が', 'a']), ('ab\nba', ['a', 'b']), ('a b', ['a b'])]
    cases += [(''.join(rng.choice('ab_') for _ in range(12)), ['a', 'b', 'ab']) for _ in range(70)]
    for i, (text, terms) in enumerate(cases):
        # 固定評価の問題・ラベルは別関数で定義し、ここで重みを調整しない。
        folder = small / str(i)
        folder.mkdir()
        (folder / 'one.md').write_text(text, encoding='utf-8')
        config({'relevance': {'fields': [{'kind': 'body'}]}})
        data = explain(terms, 'normalized', folder)
        expected_hit = all(transform(t, 'normalized') in transform(text, 'normalized') for t in terms)
        assert len(data['results']) == int(expected_hit)
        if not expected_hit:
            continue
        r = data['results'][0]
        expected = field([text], terms, 'normalized', lines=True)
        for k, v in expected.items():
            assert r['fields'][0][k] == v, (text, terms, k, r, expected)
        assert r['total'] == expected['coverage'] + expected['proximity'] + expected['ordered']

    # metadataの有効化範囲、後半失敗、重み0、非候補の不正YAML。
    invalid = notes / 'z-invalid.md'
    invalid.write_text('---\nlabel: [PRIVATE_VALUE\n---\nガラス 断熱\n', encoding='utf-8')
    for weight in [0, 1]:
        config({'relevance': {'fields': [{'kind': 'metadata', 'pointer': '/label', 'weight': weight}]}})
        for extra in [[], ['--explain']]:
            r = run('scan', '--rank', 'relevance', *extra, notes, 'ガラス', success=False)
            assert b'InvalidFrontmatter' in r.stderr and b'z-invalid.md' in r.stderr and b'PRIVATE_VALUE' not in r.stderr
        assert explain(['不存在'])['results'] == []
        assert len(explain(['Strasse'], 'normalized')['results']) == 1
    config({})
    for rank in ['path', 'heading', 'relevance']:
        run('scan', '--rank', rank, notes, 'ガラス')
    # 使用済fixtureは削除せず、別の場所へ移動して保全する。
    invalid.rename(root / 'invalid-preserved.md')
    bad_configs = [{'rank': 'other'}, {'relevance': None}, {'relevance': {'unknown': 1}},
                   {'relevance': {'fields': []}}, {'relevance': {'fields': [{'kind': 'metadata'}]}},
                   {'relevance': {'fields': [{'kind': 'body', 'pointer': '/x'}]}},
                   {'relevance': {'fields': [{'kind': 'metadata', 'pointer': '/x~2'}]}},
                   {'relevance': {'fields': [{'kind': 'body'}, {'kind': 'body'}]}},
                   {'relevance': {'fields': [{'kind': 'body', 'other': 1}]}}]
    bad_configs += [{'relevance': {'fields': [{'kind': 'body', 'weight': w}]}} for w in [-1, 101, None, True, '1']]
    bad_configs += [{'relevance': {'ordered_weight': w}} for w in [-1, 101, None, True, '1']]
    bad_configs += [{'relevance': {'fields': [{'kind': 'body'}] * 33}}]
    for c in bad_configs:
        config(c)
        run('scan', notes, 'ガラス', success=False)
    for raw in ['{"relevance":{"ordered_weight":1e999}}', '{"rank":"path","rank":"relevance"}', '{"relevance":{"fields":[{"kind":"body","weight":1,"weight":2}]}}']:
        cfg.write_text(raw, encoding='utf-8')
        run('scan', notes, 'ガラス', success=False)
    # 構造パスと理由を示し、入力した値・経路・未知キーを診断へ出さない。
    secret = 'SYNTHETIC_PRIVATE_SENTINEL'
    diagnostic_cases = [
        ({'relevance': []}, 'relevance:', 'JSONオブジェクト'),
        ({'relevance': {secret: secret}}, 'relevance:', '未知のキー'),
        ({'relevance': {'fields': {}}}, 'relevance.fields:', '配列'),
        ({'relevance': {'fields': []}}, 'relevance.fields:', 'fieldは1件以上'),
        ({'relevance': {'fields': [None]}}, 'fields[0]:', 'JSONオブジェクト'),
        ({'relevance': {'fields': [{}]}}, 'fields[0].kind:', 'kindが必要'),
        ({'relevance': {'fields': [{'kind': 1}]}}, 'fields[0].kind:', '文字列'),
        ({'relevance': {'fields': [{'kind': secret}]}}, 'fields[0].kind:', 'body/heading'),
        ({'relevance': {'fields': [{'kind': 'body', secret: secret}]}}, 'fields[0]:', '未知のキー'),
        ({'relevance': {'fields': [{'kind': 'metadata', 'pointer': 1}]}}, 'fields[0].pointer:', '文字列'),
        ({'relevance': {'fields': [{'kind': 'metadata'}]}}, 'fields[0].pointer:', '必要'),
        ({'relevance': {'fields': [{'kind': 'metadata', 'pointer': '/' + secret + '~2'}]}}, 'fields[0].pointer:', '有効なmetadata'),
        ({'relevance': {'fields': [{'kind': 'body', 'pointer': '/' + secret}]}}, 'fields[0].pointer:', '指定できません'),
        ({'relevance': {'fields': [{'kind': 'body'}, {'kind': 'body'}]}}, 'fields[1].kind:', '重複'),
        ({'relevance': {'fields': [{'kind': 'body', 'weight': secret}]}}, 'fields[0].weight:', '数値'),
        ({'relevance': {'fields': [{'kind': 'body'}, {'kind': 'heading', 'weight': 101}]}}, 'fields[1].weight:', '0〜100'),
        ({'relevance': {'proximity_weight': False}}, 'relevance.proximity_weight:', '数値'),
        ({'relevance': {'proximity_weight': -1}}, 'relevance.proximity_weight:', '0〜100'),
        ({'relevance': {'ordered_weight': 101}}, 'relevance.ordered_weight:', '0〜100'),
    ]
    for c, location, reason in diagnostic_cases:
        config(c)
        r = run('scan', '--rank', 'path', notes, 'ガラス', success=False)
        message = r.stderr.decode()
        assert location in message and reason in message, message
        assert secret not in message, message

    # 設定変更だけで信号を個別停止でき、同じ索引・候補・位置を使える。
    snapshot = (index.read_bytes(), index.stat().st_mtime_ns)
    config({})
    baseline = explain(['ガラス', '断熱'], source=index, command='search')
    positions = {r['path']: (r['offset'], r['end']) for r in baseline['results']}
    for proximity, ordered in [(0, 0), (0, 1), (1, 0), (100, 100)]:
        config({'relevance': {'proximity_weight': proximity, 'ordered_weight': ordered}})
        data = explain(['ガラス', '断熱'], source=index, command='search')
        assert data['schema_version'] == baseline['schema_version'] == 1
        assert data['algorithm'] == baseline['algorithm'] == 'relevance-v1'
        assert {r['path']: (r['offset'], r['end']) for r in data['results']} == positions
        for result in data['results']:
            for score in result['fields']:
                expected = score['coverage_score'] + proximity * score['weight'] * score['proximity'] + ordered * score['weight'] * score['ordered']
                assert abs(score['total'] - expected) < 1e-9
                if proximity == 0:
                    assert score['proximity_score'] == 0
                if ordered == 0:
                    assert score['ordered_score'] == 0
        normal = run('search', '--mode', 'literal', '--all', '--rank', 'relevance', index, 'ガラス', '断熱').stdout
        assert [line.split(':', 1)[0] for line in normal.decode().splitlines()] == [r['path'] for r in data['results']]
    for weight in [0, 100]:
        config({'relevance': {'fields': [{'kind': 'body', 'weight': weight}], 'proximity_weight': 0, 'ordered_weight': 0}})
        data = explain(['ガラス', '断熱'], source=index, command='search')
        assert {r['path']: (r['offset'], r['end']) for r in data['results']} == positions
        assert all(len(r['fields']) == 1 and r['total'] == weight * r['fields'][0]['coverage'] for r in data['results'])
        if weight == 0:
            assert [r['path'] for r in data['results']] == sorted(positions)
    assert (index.read_bytes(), index.stat().st_mtime_ns) == snapshot
    # 異なるfieldで件数上限を単独検証し、重複拒否による偽陽性を避ける。
    maximum_fields = [{'kind': 'metadata', 'pointer': f'/synthetic_{i}', 'weight': 0} for i in range(32)]
    config({'relevance': {'fields': maximum_fields}})
    maximum = explain(['Strasse'], 'normalized')
    assert len(maximum['results']) == 1 and len(maximum['results'][0]['fields']) == 32
    config({'relevance': {'fields': maximum_fields + [{'kind': 'metadata', 'pointer': '/synthetic_32', 'weight': 0}]}})
    r = run('scan', '--rank', 'path', notes, 'ガラス', success=False)
    assert b'relevance.fields:' in r.stderr
    config({})
    path_expected = run('scan', '--rank', 'path', notes, 'ガラス').stdout
    heading_expected = run('scan', '--rank', 'heading', notes, 'ガラス').stdout
    assert run('scan', notes, 'ガラス').stdout == path_expected
    config({'rank': 'relevance'})
    default_rank = run('scan', notes, 'ガラス').stdout
    assert default_rank == run('scan', '--rank', 'relevance', notes, 'ガラス').stdout
    assert default_rank != path_expected, '順位指定を無視しても通るfixture'
    assert run('scan', '--rank', 'path', notes, 'ガラス').stdout == path_expected
    assert run('scan', '--rank', 'heading', notes, 'ガラス').stdout == heading_expected
    run('scan', '--explain', notes, 'ガラス')
    config({})
    for argv in [('scan', '--explain', notes, 'ガラス'), ('index', '--rank', 'relevance', notes, index),
                 ('scan', '--rank', 'relevance', '--explain', '--explain', notes, 'ガラス')]:
        run(*argv, success=False)
    config({'relevance': {'fields': [{'kind': 'body'}]}})
    before = explain(['ガラス', '断熱'], source=index, command='search')
    (notes / 'b.md').write_text('ガラス_______断熱\n', encoding='utf-8')
    assert before == explain(['ガラス', '断熱'], source=index, command='search')
    run('index', '--mode', 'normalized', '--incremental', notes, index)
    rebuilt = root / 'rebuilt.idx'
    run('index', '--mode', 'normalized', notes, rebuilt)
    assert explain(['ガラス', '断熱'], source=index, command='search') == explain(['ガラス', '断熱'], source=rebuilt, command='search')
    assert explain(['ガラス', '断熱'], source=index, command='search') == explain(['ガラス', '断熱'])

    # v2 fixture生成器は既存結合テストから再利用し、保存実装から生成しない。
    import sys
    old_argv = sys.argv
    sys.argv = ['legacy_fixture']
    try:
        spec = importlib.util.spec_from_file_location('legacy_fixture', Path(__file__).with_name('test-incremental-cli.py'))
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
    finally:
        sys.argv = old_argv
    v2 = root / 'legacy.idx'
    v2.write_bytes(legacy.legacy_index([(p, p.read_text(encoding='utf-8')) for p in sorted(notes.iterdir())]))
    assert explain(['ガラス', '断熱'], source=v2, command='search') == explain(['ガラス', '断熱'])
    limits = root / 'limits'
    limits.mkdir()
    (limits / 'a.md').write_text('a b', encoding='utf-8')
    (limits / 'z.md').write_text('a' * 100001 + 'b', encoding='utf-8')
    for extra in [[], ['--explain']]:
        r = run('scan', '--all', '--rank', 'relevance', *extra, limits, 'a', 'b', success=False)
        assert b'RelevanceLimitExceeded' in r.stderr
    many = root / 'many-terms'
    many.mkdir()
    terms = [f'語{i}' for i in range(65)]
    (many / 'one.md').write_text(' '.join(terms), encoding='utf-8')
    r = run('scan', '--all', '--rank', 'relevance', many, *terms, success=False)
    assert b'RelevanceLimitExceeded' in r.stderr
    report = {'passed': True, 'commands': commands, 'oracle_cases': len(cases), 'performance_claim': False}
    (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
