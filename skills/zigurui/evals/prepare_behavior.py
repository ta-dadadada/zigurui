#!/usr/bin/env python3
"""独立agent評価用の合成文書・隔離実行器を作る。既存ディレクトリは上書きしない。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

RUNNER = r'''#!/usr/bin/env python3
import hashlib,json,os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
case=root/('case-'+sys.argv[1])
config=json.loads((root/'environment.json').read_text())
args=sys.argv[2:]
if args[:1]==['--']: args=args[1:]
env=dict(os.environ,XDG_CONFIG_HOME=str(case/'config'),XDG_DATA_HOME=str(case/'data'))
def hashes():
    return {str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob('*') if p.is_file() and p.name not in ('commands.jsonl','prompt.txt','initial.json')}
before=hashes()
# 本体更新は操作意図を記録して止める。実バイナリやネットワークを変更しない。
blocked=bool(args and args[0]=='update' and args[1:] not in (['--help'],['-h']))
if blocked:
    code,out,err=97,b'',b'isolated evaluation: binary update blocked\n'
else:
    result=subprocess.run([config['binary'],*args],cwd=case,env=env,capture_output=True,timeout=120)
    code,out,err=result.returncode,result.stdout,result.stderr
record=dict(args=args,exit_code=code,stdout=out.decode(errors='replace'),stderr=err.decode(errors='replace'),blocked=blocked,before=before,after=hashes())
with (case/'commands.jsonl').open('a') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')
sys.stdout.buffer.write(out);sys.stderr.buffer.write(err);sys.exit(code)
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=False)
    binary=args.binary.resolve()
    (root/'environment.json').write_text(json.dumps(dict(binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),indent=2)+'\n')
    (root/'run.py').write_text(RUNNER)
    prompts=json.loads(Path(__file__).with_name('evals.json').read_text())['evals']
    contents={
        1:{'a.md':'---\ntags: [住宅]\n---\nｶﾞﾗｽと断熱\n','b.md':'ガラスのみ\n','c.md':'断熱のみ\n'},
        2:{'a.md':'---\ntags: [住宅]\n---\nガラス\n','b.md':'---\nbroken: [\n---\nガラス\n'},
        3:{'note.md':'古い内容 ガラス\n'},
        4:{'a.md':'---\nitems: [{name: alpha, state: draft}, {name: beta, state: ready}, {name: alpha}, {name: null}]\n---\nガラス\n','b.md':'---\nitems: [[{name: alpha, state: ready}]]\n---\nガラス\n','c.md':'---\nitems: []\n---\nガラス\n','d.md':'---\nitems: [[1]]\n---\nガラス\n'},
        5:{},6:{}}
    for spec in prompts:
        i=spec['id'];case=root/f'case-{i}';case.mkdir();notes=case/'notes';notes.mkdir()
        for name,text in contents[i].items(): (notes/name).write_text(text)
        env=dict(os.environ,XDG_CONFIG_HOME=str(case/'config'),XDG_DATA_HOME=str(case/'data'))
        if i<=4:
            subprocess.run([str(binary),'index','--mode','normalized',str(notes),str(case/'saved.idx')],env=env,capture_output=True,check=True)
        if i==3: (notes/'note.md').write_text('新しい内容 ガラス\n')
        initial={str(p.relative_to(case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in case.rglob('*') if p.is_file()}
        (case/'initial.json').write_text(json.dumps(initial,indent=2)+'\n')
        context='対象はこのcaseの notes/、既存normalized索引は saved.idx。' if i<=4 else '実行ファイルの導入先・公開タグの確認はまだ済んでいません。'
        (case/'prompt.txt').write_text(spec['prompt']+'\n'+context+'\n')
    print(root)


if __name__=='__main__': main()
