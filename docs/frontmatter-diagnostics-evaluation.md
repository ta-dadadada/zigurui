# frontmatter失敗時の診断評価

仕様は[frontmatterの停止時診断](frontmatter.md)、結果は[匿名要約JSON](benchmark-results/frontmatter-diagnostics-01.json)を参照。通常ビルドの`zig-out/bin/zigurui`を合成文書だけで検証し、性能測定はしていない。raw・診断・生成索引はローカルの `.local/benchmarks/frontmatter-diagnostics-01/` に保持した。公開要約に保存パスや文書内容を含めていない。

## 結果

- 停止78件でexit code 1、標準出力が空、理由と問題ファイルの文脈が期待どおりだった。問題文書の前後に検索語を含む正常文書を置き、部分的な検索結果やschemaが出ないことを確認した。
- 構文不正、未対応の明示tag、headerサイズ上限、数値範囲外をsearch/scan/schemaで検証した。検索候補外、ファイル条件に不適合、frontmatter条件に不適合の文書でも全体を停止した。
- ファイル名のLF・CR・tab・ESC・DEL、引用符・backslash、Unicodeの7ケースを確認した。通常診断は物理1行で、制御文字は生で出ず、JSONとして復号したパスが元の問題ファイルと一致した。
- 診断を固定のcontext・error名・引用pathだけに限定し、libyamlの生文面や余分な文字列を認めない検査を通した。header値と本文へ置いた固有の検査用文字列も標準エラーに出なかった。
- schema全体の4096 paths上限では`schema_aggregation`を表示し、unionが超過した時点の追加文書を文脈として示した。その文書単独の構文違反とは扱っていない。同じ文書群の本文検索は成功した。
- raw検索・file-only検索など成功すべき54件も確認した。壊れたYAMLを含む文書自体をfile-only条件で選んでも、YAMLを解析せず原文を検索できた。

診断検査scriptの単体テスト5件も成功し、評価前後のバイナリハッシュは一致した。過去の性能測定・匿名要約は再実行・変更していない。

## 検証の限界

実行時OOM、stderrの途中書き込み失敗、結果出力開始後のstdout I/O失敗の注入は行っていない。出力開始後にOSの書き込みが失敗した場合、既に出たバイトの撤回は保証しない。今回の1行検査は通常ビルドが対象で、計測専用ビルドの追加profile JSONは対象外。実データや別環境でも未検証である。

## 再実行

通常ビルド・テスト完了後、新規run-idで実行する。生成物や診断は削除しない。

```sh
python3 -B scripts/test-evaluate-frontmatter-diagnostics.py
python3 -B scripts/evaluate_frontmatter_diagnostics.py \
  --run-id frontmatter-diagnostics-reproduction-01 \
  --binary zig-out/bin/zigurui
```
