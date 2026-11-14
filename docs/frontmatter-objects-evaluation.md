# 配列内object・多重配列の独立評価

[現行仕様](frontmatter.md)に対し、合成文書のschema、型付き条件、原文の行番号・抜粋を独立した期待結果と照合した。速度・RSS・索引サイズは測定していない。結果は[匿名要約JSON](benchmark-results/frontmatter-objects-correctness-01.json)に記録した。

## 期待結果と検証範囲

YAMLと型付きmetadata対応表を手書きし、製品のparserや集計関数を使わず期待schemaを求めた。別途固定した期待文書集合とも照合し、索引とscanの一致だけを合格条件にしなかった。

新規10文書・14field・31検索条件で次を確認した。

- 配列内objectと多重配列を透過するキーpath、数字・空文字列・escapeを含むキー。
- 文書単位のpresent、missing、null_count、型union。複数要素で同じ値やnullが現れても文書数を重複加算しない。
- element_typesは直下要素の型だけを集計し、空配列・空mapから子fieldを生成しない。
- 独立したwhere条件は別要素で満たしても一致する。終端のscalar比較は直下要素だけを対象とし、`items: [[1]]` の `/items = 1` は一致しない。
- 1文字、日本語、該当なし、本文AND、header除外、正規化の縮約・展開、BOM・CRLFを含む原文の行番号と抜粋。

索引/queryのliteral/literal、normalized/normalized、normalized/literalの3経路でsearch/scanを期待出力と照合した。literal索引/normalized queryは `IndexModeMismatch` と空stdoutを確認した。旧scalar配列・nested mapの12文書・41条件も同じbinaryで回帰確認した。両fixture合計で検索出力432件、schema4件、mode不一致2件が成功した。

不正文書20種では、前後の正常文書を置き、候補外・metadata条件不一致・file条件不一致のsearch/scan、およびschemaの計140件で終了コード1、空stdout、原因と対象パスだけのstderrを確認した。配列要素のmap内重複、escape復号後の重複、後続要素の未対応tagも含む。同名キーが別々のmapに現れる正常fixtureは成功した。

resource境界18組で上限ちょうどと1超過を確認した。既存のheader、深さ、nodes、encoded pointer、schema unionに加え、配列objectのnodes4096/4097、配列objectと多重配列の深さ16/17、配列object下のencoded pointer4096/4097を検証した。schema union上限はschemaだけに適用し、文書単位のsearch/scanは成功した。

## 実行条件と結果

run-idは `frontmatter-objects-correctness-01`、2026-11-14 23:10:31〜23:11:01 JSTに実行した。HEADは `d091bad4a6fbff7ade2ad6203f8e8b8f9b0b82dd`。`src/frontmatter.zig`、仕様書、評価helperと新評価scriptに未コミット変更を含む。通常の `mise exec -- zig build` で作ったDebug版、固定Zig 0.17.1、Python 3.15.2を使用した。

674 CLI呼出しが完了し、596記録checkが成功した。binary SHA-256は `bfd632eb7be0909ac028967f9173260eedb58818b1f5ef59e7cf35ef659af8e4`。実行前後でbinaryと合成入力のSHA-256が一致した。XDG_CONFIG_HOMEはrun内の空設定先に分離した。時間やRSSのサンプルは採取していない。

rawは `.local/benchmarks/frontmatter-objects-correctness-01/results.json`、各CLIのstdout/stderrは同runの `diagnostics/` に保持した。rawにはHEAD、未コミット差分一覧とhash、source・runner・依存script・入力のSHA-256を記録した。匿名要約には個人パスや生の診断を含めていない。過去の[frontmatter評価](frontmatter-evaluation.md)は拡張前の測定記録として保持した。

## 再実行と限界

```sh
python3 -B scripts/test-evaluate-frontmatter.py
python3 -B scripts/test-evaluate-frontmatter-objects.py
python3 -B scripts/evaluate_frontmatter_objects.py \
  --binary zig-out/bin/zigurui --run-id frontmatter-objects-reproduction-01
```

旧helper単体9件、新oracle単体8件が成功した。runnerは未使用run-idだけを受け付け、既存結果を上書きしない。

合成文書による正しさの評価であり、実KB全体の検索品質を代表しない。同一要素AND、ランキング、未実装の表記ゆれ対応は合格条件に含めていない。CLIはbyte spanを返さないためUTF-8のbyte位置は直接照合せず、Zig単体テストの範囲とする。性能比較と索引サイズの検討は今回の対象外。
