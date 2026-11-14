---
name: zigurui
description: zigurui CLIでローカルMarkdownを検索し、索引を構築・再構築する。保存済みノートの語句検索、表記ゆれや複数語句、frontmatter・ファイル情報による絞り込みに使う。検索エンジン自体の開発やノート編集は対象外。
---

# ziguruiを使う

原文を変更せず、指定された文書群または保存済み索引から根拠となる文書を探す。

## 入力と保存先を確かめる

- 利用する実行ファイルの `zigurui --version` と `zigurui help` を確認する。以下は現行CLIの手順であり、未対応のオプションや更新コマンドを推測しない。
- 検索対象、検索語、保存時点の検索か現在の文書の検索かを依頼から決める。不明な対象ディレクトリを広く走査しない。
- `zigurui paths` で設定を確認する。明示CLI引数が設定より優先されるが、不正な設定は明示引数でも無視されない。設定を勝手に書き換えない。
- 既定索引は再構築で置き換わる。複数文書群を混ぜないよう、通常は索引パスと `--mode` を明示する。保存先は原文とは別の `.idx` などを選び、明示パスの親ディレクトリを先に用意する。既存索引を置き換える場合は、依頼の対象と一致することを確かめる。
- 索引には原文とパスが入る。検索結果・診断にもパスや抜粋、schemaにはキーが含まれる。これらを外部へ送信・公開する権限は検索依頼に含めない。報告は必要な根拠だけに絞る。

## 検索する

`/path/to/notes` と `/path/to/notes.idx` は確認済みの対象・保存先に置き換える。

```sh
zigurui index --mode normalized /path/to/notes /path/to/notes.idx
zigurui search --mode normalized /path/to/notes.idx 'ガラス'
zigurui search --mode literal /path/to/notes.idx 'ｶﾞﾗｽ'
zigurui search --mode normalized --all /path/to/notes.idx 'ガラス' '断熱'
zigurui scan --mode normalized /path/to/notes 'ガラス'
```

- `search` は保存時点の原文・パス、`scan` は現在の文書を読む。索引が既にあれば、検索だけの依頼で再構築しない。
- `literal` は表記を区別する連続部分一致。`normalized` は全角半角・英字の大小などを揃えるが、かな種・同義語は揃えない。literal索引のnormalized検索は失敗するため、必要ならnormalized索引を別に構築する。normalized索引のliteral検索は可能。
- `--mode` はサブコマンド直後、その他のオプションは入力パス前に置く。`--all` では索引パスを省略できない。語句はシェルで安全に引用し、各語句を別引数にする。空白を含む一引数はそのまま一つの連続文字列であり、自動分割されない。
- ANDは同一文書に全語句がある条件。抜粋には全語句が収まらない場合がある。結果はランキングではなくパス順で、1文書1件。0件の正常終了を実行失敗と混同しない。

## metadataで絞る

保存済みmetadataのキーと型を `schema` で確認してから条件を作る。

```sh
zigurui schema /path/to/notes.idx
zigurui search --mode normalized --frontmatter --where /tags '"住宅"' /path/to/notes.idx 'ガラス'
zigurui search --mode normalized --where file:/name '"notes.md"' /path/to/notes.idx 'ガラス'
# 配列透過への対応を確認した版で、別要素でもよい2条件を指定する
zigurui search --mode normalized --frontmatter --where /items/name '"alpha"' --where /items/state '"ready"' /path/to/notes.idx 'ガラス'
```

- YAML条件は `--frontmatter` と併用する。この場合はheaderを除く本文を検索する。通常検索とファイル条件だけの検索はheaderを含む原文を対象とする。
- `--where` はselectorとJSON scalarの2引数。文字列はJSONの二重引用符も渡す。型は区別され、`1`、`1.0`、`"1"` は別条件。複数条件はAND、scalar配列は要素のいずれかとの一致。値やキーを本文の正規化に合わせて変換しない。
- ファイル名・パスは完全一致で、globではない。YAMLキーはJSON Pointer（例 `/author/name`）を使う。配列を通るキー経路は対応版のhelpと仕様で確認する。対応版では `/items/name` の途中にある配列を、多重配列も含めて透過し、各objectの `name` を調べる。JSON Pointerのescapeを使い、数値の経路要素もobjectのキーとして扱う。配列添字と解釈しない。未対応版でこの動作を仮定しない。
- 配列透過に対応する版でも、末端の配列に対するscalar条件は直下のscalarだけを調べる。`[1]` は `1` に一致するが、`[[1]]` は一致しない。繰り返した `--where` は独立したANDで、配列内の別々の要素が条件を満たしてよい。同じ要素への束縛を保証する条件として使わない。
- 配列透過に対応する版のschemaは同じ経路を文書単位で重複除去して集計する。欠落と明示的なnullを区別し、`element_types` は配列の直下要素の型を示す。要素数を文書数と混同しない。
- `--frontmatter` と `schema` は全headerを検証する。1件でも不正・未対応なら部分結果を返さず失敗する。条件から外れる文書も検証対象。0件と報告せず、エラー名と必要最小限の対象情報を伝える。原文の自動修正、文書の除外、フラグを外した無断再試行で失敗を隠さない。

## 再構築と報告

追加・変更・削除を反映するときは、確認した元ディレクトリと同じmodeで `index` を再実行する。差分更新・監視・`update` コマンドはない。互換性エラーや破損でも元文書から再構築し、保持が必要な旧索引は別の保存先で残す。元文書や既存索引を削除しない。

検索対象とmode、保存済み索引か現在の走査か、結果の文書・行番号・必要な抜粋を簡潔に伝える。保存時点が不明なら最新と断定しない。失敗や未検証範囲を結果と分ける。

詳しい仕様は対応するソース配布の `docs/usage.md`（設定・snapshot）、`docs/search-normalization.md`、`docs/search-and.md`、`docs/frontmatter.md` を参照する。設定の相対索引パスはconfig.jsonの場所が基準であり、CLIの相対パスとは異なる。`schema` と `--all` は設定の索引パスを使用しない。
