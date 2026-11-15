#!/usr/bin/env python3
"""匿名合成入力の性能スモーク。通常wall/CPU/RSSと計装区間を分離し、比較を一般化しない。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from benchmark import measure
from benchmark_normalized import parse_profile, RELEVANCE_PHASES


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plain', type=Path, required=True)
    p.add_argument('--profile', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binaries = {key: getattr(args, key).resolve() for key in ['plain', 'profile']}
    cfg = root / 'config' / 'zigurui' / 'config.json'
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'relevance': {'fields': [{'kind': 'body'}, {'kind': 'heading', 'weight': 2}, {'kind': 'metadata', 'pointer': '/label'}]}}))
    env = dict(os.environ, XDG_CONFIG_HOME=str(cfg.parent.parent), HOME=str(root / 'home'))
    workloads = [('broad', ['ガラス', '断熱'], 100, '# ガラス断熱\n' + 'ガラス と 断熱\n' * 20),
                 ('one', ['ガ', '断'], 100, 'ガラス と 断熱\n' * 20),
                 ('long', ['ガラス', '断熱'], 1, 'ガラス___断熱 ' * 10000),
                 ('arrays', ['ガラス', '断熱'], 4, '---\nlabel: [' + ', '.join('ガラスと断熱' for _ in range(1000)) + ']\n---\nガラスと断熱\n')]
    records = []
    for ident, terms, count, text in workloads:
        notes = root / ident
        notes.mkdir()
        for i in range(count):
            (notes / f'n-{i:03}.md').write_text(text, encoding='utf-8')
        index = root / f'{ident}.idx'
        subprocess.run([str(binaries['plain']), 'index', '--mode', 'normalized', str(notes), str(index)], env=env, capture_output=True, check=True)
        tail = ['search', '--mode', 'normalized', '--all', '--rank', 'relevance', str(index), *terms]
        plain = [str(binaries['plain']), *tail]
        profiled = [str(binaries['profile']), *tail]
        warm = subprocess.run(plain, env=env, capture_output=True, check=True)
        probe = subprocess.run(profiled, env=env, capture_output=True, check=True)
        assert warm.stdout == probe.stdout
        parsed = parse_profile(probe.stderr, 'search', 'normalized')
        assert all(k in parsed['timings_ns'] for k in RELEVANCE_PHASES)
        # 計装版は通常版の時間サンプルへ混ぜない。
        samples = [measure(plain, env=env) for _ in range(3)]
        records.append({'workload': ident, 'documents': count, 'plain_samples': samples,
                        'median_wall_ms': statistics.median(s['elapsed_ms'] for s in samples),
                        'median_cpu_ms': statistics.median(1000 * (s['user_seconds'] + s['system_seconds']) for s in samples),
                        'median_rss_bytes': statistics.median(s['peak_rss_bytes'] for s in samples),
                        'instrumented_profile': parsed})
    report = {'schema_version': 1, 'smoke_only': True, 'host_load_controlled': False,
              'concurrency': 'この担当のビルド/テスト/測定は逐次。他プロジェクトは未統制',
              'platform': platform.platform(), 'python': sys.version,
              'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'dirty': subprocess.check_output(['git', 'status', '--short'], text=True),
              'binaries': {key: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for key, path in binaries.items()},
              'records': records}
    (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    for r in records:
        print(f"{r['workload']}: wall={r['median_wall_ms']:.2f}ms cpu={r['median_cpu_ms']:.2f}ms rss={r['median_rss_bytes']} bytes (smoke)")


if __name__ == '__main__':
    main()
