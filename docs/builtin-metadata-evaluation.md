# ファイル名・パスの組み込みmetadata評価

仕様は[frontmatterと組み込み項目](frontmatter.md)、結果は[匿名要約JSON](benchmark-results/builtin-evaluation-01.json)を参照。raw・診断・合成索引はローカルの `.local/benchmarks/builtin-evaluation-01/` に保持した。保存パスや端末情報は公開要約へ含めていない。性能測定は行っていない。

## 確認した結果

10文書の合成fixtureで79検索条件、18エラー条件、5 snapshot条件、旧版との8回帰条件が成功した。検索はliteral/normalizedとnormalized索引上のliteral経路でsearch/scanの全出力を照合した。

- `file:/name`は拡張子を含むbasename。同名の2文書が両方一致し、Unicode・大小文字・全半角・空白・`=`を正規化せず比較した。
- `file:/path`は保存文字列との完全一致。相対指定や同じ場所を指す`..`付きの値へ読み替えなかった。相対パスを保存した場合のroot推測なしは、実装側のv2 encode/decode単体テストで確認した。
- 非stringのJSON scalarは型不一致で0件。未知selectorや非scalarの条件値は`InvalidFilter`。未知selectorは空の索引でも拒否した。
- YAMLの`/file/name`・`/file/path`と組み込みselectorは衝突せず、組み込み条件とfrontmatter条件のANDも成立した。frontmatter条件が混じると`--frontmatter`を必須とした。
- file-only条件では壊れたYAMLを解析せず、原文全体から検索できた。`--frontmatter`を明示すると、ファイル条件に不適合・候補外の不正headerも拒否し、部分出力しなかった。
- 本文AND、入力後のオプション風の語、正規化で長さが変わる本文の原文行・抜粋が期待どおりだった。

schemaの`builtin_fields`は別配列で、`file:/name`、`file:/path`の順にstring型を表示した。各項目のpresent=10 / missing=0 / null_count=0 / element_types=[]を確認し、空の索引でも型定義を残した。既存のfrontmatter用`fields`は旧版と完全一致した。

合成索引作成後にそのrunの原文を更新し、別ファイルも追加した。旧索引は保存時点のschema・本文・名前・パスを使い、scanは現在の文書を使うことを確認した。実ファイルの削除・改名はしていない。

保存形式はv2で、同じ合成文書から旧frontmatter版と新しい版が作成した索引のSHA-256が一致した。raw phrase・AND・frontmatter検索を双方のバイナリと索引で比較して出力が一致し、評価前後のバイナリハッシュも不変だった。

独立評価の単体テスト5件と、schema期待値を更新したfrontmatter評価helperの単体テスト8件も成功。過去の性能測定と匿名要約は再実行・変更していない。実KB、別環境、ファイル更新の自動追従は評価対象外である。

## 再実行

通常ビルド完了後に、新規run-idで実行する。生成物・診断は削除しない。

```sh
python3 -B scripts/test-evaluate-builtin.py
python3 -B scripts/test-evaluate-frontmatter.py
python3 -B scripts/evaluate_builtin.py --run-id builtin-reproduction-01 \
  --binary .local/benchmarks/builtin-plain/bin/zigurui \
  --baseline .local/benchmarks/frontmatter-plain-v2/bin/zigurui \
  --provenance .local/benchmarks/builtin-provenance.json
```
