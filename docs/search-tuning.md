# 検索の調整ガイド

人が操作する場合も、AIエージェントへ任せる場合も、まず基本機能で対象と条件を決め、必要な順位だけを調整する。特定の文書群で良い重みを、他の利用者向けの標準とは扱わない。

- [準備と権限](#準備と権限)
- [基本機能と変更の反映](#基本機能と変更の反映)
- [非公開の比較環境](#非公開の比較環境)
- [説明と比較](#説明と比較)
- [採用と復元](#採用と復元)
- [AIエージェントへの依頼](#aiエージェントへの依頼)

## 準備と権限

```sh
zigurui --version
zigurui help
zigurui paths
```

利用する版で `--rank relevance` と `--explain` が使えることを確認する。使えなければ調整を止め、版の確認へ戻る。バイナリ更新は別の操作であり、自動実行しない。`paths` が示す設定ファイルをローカルで読み、元の内容とファイルの有無を記録する。索引のmodeは構築時の記録で確認し、不明な場合は推測で再構築せず確認する。

対象の索引・文書、検索してよい語句、評価記録の保存先、設定を書き換えてよい範囲を決める。検索・比較の依頼だけで、設定反映・索引の上書き・実文書の収集や複製が許可されたとは扱わない。既存索引があれば、その保存原文を使って比較できる。現在の文書を読む `scan` は、対象ディレクトリへのアクセスも許可されている場合に限る。

検索語、正解文書、schemaのキー・値、索引の原文・パス、explainの生データ、設定のバックアップは非公開に保つ。共有リポジトリ・同期フォルダ・公開レポートへ置かず、外部サービスへの送信や、未指定ディレクトリの探索を評価のために追加しない。AIエージェントには、利用する実行環境でこれらを扱ってよいかも確認する。公開できるのは、別途確認した匿名の要約や合成例だけ。

## 基本機能と変更の反映

連続文字列は一引数、複数語句のANDは `--all`、表記差は `--mode normalized`、本文限定や型付き絞り込みは `--frontmatter` / `--where` を使う。見出し一致の優先だけなら `--rank heading` で足りる。[利用ガイド](usage.md#現在の機能と制約)で検索対象と条件を確認してから、relevanceを試す。

| 変えるもの | 反映・索引への影響 |
| --- | --- |
| rank、field、重み、近接、絞り込み | 検索時に適用。再構築不要。保存済み原文とパスを使う |
| 検索mode | normalized検索にはnormalized索引が必要。normalized索引のliteral検索は可能。自動変換なし |
| index_path | 読込先・保存先の既定を変えるだけ。既存索引の移動・変換なし |
| 原文の追加・変更・削除 | 保存索引へ反映するには、別途許可された構築・差分更新が必要 |

CLIの明示指定が設定より優先され、設定省略時は組み込み既定値になる。不正な設定はCLI上書きでも無視されない。[設定の契約](usage.md#設定ファイル)と[field・信号の契約](usage.md#relevanceの順位と説明を見る)を正本とする。

順位は一致済み候補だけを並べ替える。候補集合にない正解は、fieldを追加しても重みを変えても取り戻せない。正解が候補にない場合は、語句・mode・AND・絞り込み・保存時点を先に見直す。metadata値だけで候補を新たに探す機能と混同しない。

## 非公開の比較環境

以下はsh互換の例。Python 3は候補設定の一項目を編集するためだけに使う。パス・語句は架空で、許可された対象の絶対パスに置き換える。通常の設定を編集せず、設定ファイルだけを新しい非公開ディレクトリへ退避し、比較用に読む。索引・原文のコピーは不要。

```sh
umask 077
trial=$(mktemp -d "${TMPDIR:-/tmp}/zigurui-tuning.XXXXXX")
active_config='/absolute/path/to/config.json'  # paths の config_file
index='/absolute/path/to/notes.idx'           # 確認済みのnormalized索引
mkdir -p "$trial/baseline/zigurui" "$trial/candidate/zigurui"
# 既存設定がない場合は、その事実を記録して {} を保存する
if [ -e "$active_config" ]; then
  cp -p "$active_config" "$trial/original.json"
else
  printf '{}\n' > "$trial/original.json"
fi
cp "$trial/original.json" "$trial/baseline/zigurui/config.json"
cp "$trial/original.json" "$trial/candidate/zigurui/config.json"
```

途中のコマンドが失敗したら、その時点で止める。存在しない設定の代わりに、読めない設定を `{}` として回避しない。退避した設定の相対index_pathは基準ディレクトリが変わるため、比較コマンドでは必ず索引の絶対パスを明示する。通常の検索は保存済みheaderも対象にする。`--frontmatter` を付けるかどうかも、両条件で揃える。

まず近接の一要素だけを変更する例:

```sh
python3 - "$trial/candidate/zigurui/config.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
config = json.loads(path.read_text(encoding='utf-8'))
config.setdefault('relevance', {})['proximity_weight'] = 0
path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
PY
```

次のJSONはfield追加の別の例で、比較用設定に適用する場合も元の他項目を保持する。`fields` は既定への追加でなく全置換なので、残したいfieldをすべて列挙する。`/subject` は架空のキー。自分の許可済みschemaを確認し、必要な経路だけを選ぶ。重みは例示であり推奨最適値ではない。

```json
{
  "relevance": {
    "fields": [
      {"kind": "body", "weight": 1},
      {"kind": "heading", "weight": 2},
      {"kind": "file_name", "weight": 1},
      {"kind": "metadata", "pointer": "/subject", "weight": 2}
    ]
  }
}
```

metadata追加、重み変更、ordered_weight変更を一度に行わず、前の条件から一要素だけ変える。fieldの重み0は寄与を消すが抽出やYAML検証は止めない。処理も避けるならfieldを除外する。値の境界・無効化・上限の詳細は[利用ガイド](usage.md#relevanceの順位と説明を見る)を参照。

## 説明と比較

同じ索引・版・語句・mode・絞り込みで比較する。以下はANDの例で、1語なら `--all` を外して引数を一つにする。`--rank relevance` を明示することで、rank既定がpathのままでも採点を試せる。

```sh
XDG_CONFIG_HOME="$trial/baseline" zigurui search --mode normalized --all --rank relevance --explain "$index" 'ガラス' '断熱' > "$trial/baseline.json"
XDG_CONFIG_HOME="$trial/candidate" zigurui search --mode normalized --all --rank relevance --explain "$index" 'ガラス' '断熱' > "$trial/candidate.json"
XDG_CONFIG_HOME="$trial/candidate" zigurui search --mode normalized --all --rank relevance "$index" 'ガラス' '断熱'
```

終了コードを確認し、失敗した出力ファイルを0件の検索結果として評価しない。不正な設定やYAML、資源上限エラーでは止める。暗黙の全再構築、文書除外、フラグを外す再試行、原文修正、削除へ進まない。

explainはJSONのschema_versionとalgorithmを確認して読む。`settings` で適用された設定、各結果の `term_count` で重複排除後の語句数、`fields` で `coverage_score`・`proximity_score`・`ordered_score`、`total` で合計を確認する。windowは原文バイト位置ではない。単位と値番号は[説明JSONの契約](usage.md#relevanceの順位と説明を見る)を参照する。生データを外部の解析サービスへ渡さない。

比較前に「この語で探したい文書」と上位に欲しい理由をローカルで決める。調整に使う語句と、最後に確認する語句を分ける。上位数件の到達しやすさだけでなく、正解が下がる例、複数見出し・配列要素に語が分散する例、該当なし、1語、広い語・1文字・多数候補、長文・多数metadata値も確認する。候補集合と原文位置が変わったら、順位改善として採用せず条件や失敗を調べる。小さい合成例だけの結果は自分の文書群全体へ一般化しない。

性能は説明JSONの生成を外した通常検索で、baseline/candidateを各1回予熱してから、順番を入れ替えつつ複数回測る。例えば同じ通常検索の先頭にシェルの `time` を付けるとwall/user/systemの確認ができる。使う環境にRSS計測手段があるならpeak RSSも別に記録する。計測中は他のビルド・テスト・ベンチを動かさず、負荷を統制できなければスモークと記録する。版・設定差分・mode・件数・反復数・時間・メモリ・悪化例・エラーを非公開で残し、品質と費用を分けて採否を決める。全候補の総仕事量は制限されないため、値ごとの上限があることだけで安定費用を保証しない。

## 採用と復元

候補設定と比較結果を確認し、依頼で許可された範囲だけを通常設定へ反映する。AIエージェントへ比較だけを頼んだ場合は、設定差分と副作用を示し、書き換える前に承認を得る。すでに対象・項目の反映が許可されていれば、その範囲内で進められる。

反映直前に通常設定が退避時点から変わっていないことを比較する。変わっていたら他の編集を上書きせず差分を見直す。コピーした比較設定を丸ごと戻すのではなく、採用した項目だけを元の通常設定に適用する。通常設定で `paths` と同じ検索を再実行し、rank既定まで変更した場合はCLIの `--rank` を外した結果も確認する。field・重みの変更だけなら再構築しない。

元に戻す場合は、現在の設定も必要な記録として保持し、承認された対象へ `original.json` の元の内容を復元する。後から加えた無関係な変更がある場合は丸ごと上書きしない。元が設定なしで今回新規作成した場合、元の状態への復元にはそのファイルを取り除く必要があるので削除の許可を確認する。順位だけを一時的にpathへ戻すならCLIの `--rank path` を使えるが、設定不正の回避にはならない。

比較ディレクトリやバックアップも非公開の記録であり、終了時に自動削除しない。残す記録と不要な一時出力を分け、対象・理由・失う記録を確認してから許可されたものだけを整理する。

## AIエージェントへの依頼

[利用者Skill](../skills/zigurui/SKILL.md)を使う場合も、本ガイドを参照する。例えば次のように、対象と許可を明示できる。

> 指定した索引だけを使い、ローカルで順位設定を比較してください。原文の取得・複製、外部送信、通常設定や索引の上書き、削除は許可しません。基本機能で足りるかを先に確認し、一項目ずつ候補設定を比較してください。設定差分、良化・悪化例、費用、未確認事項を示し、反映は待ってください。

メンテナーが採点実装や評価器を変更する場合の手順は、[開発環境の運用](development.md)と[relevanceの設計・検証](relevance-ranking.md)へ分離している。本ガイドの調整にソース編集は不要。
