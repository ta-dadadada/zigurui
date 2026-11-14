# libyamlの取得元とローカル修正

- 版: 0.2.5
- 上流: https://github.com/yaml/libyaml
- 版のcommit: `2c891fc7a770e8ba2fec34fc6b545c672beb37e6`
- ライセンス: MIT。正本は同梱の `License`。
- 取得日: 2026-11-14。公式GitHubの固定版ファイルを取得。
- depth制限のみ上流commit `51843fe48257c6b7b6e70cdec1db634f64a40818` からbackport。
- 上流修正: https://github.com/yaml/libyaml/commit/51843fe48257c6b7b6e70cdec1db634f64a40818
- `depth-limit.patch` は原版から同梱版への差分。上流のテストCLI変更は含めない。
- parser用の4個のCソースと2個のヘッダーだけを同梱し、静的に組み込む。

| ファイル | 原版SHA-256 | 同梱版SHA-256 |
| --- | --- | --- |
| `include/yaml.h` | `de39c9fc2b2c8584775e6d0dc0f13c8e778ceba47d419cff9d85465ca35d6954` | `495a37d3f4821cd462a58c8983f3a39781f211edfbbbcf34c017d1e999d31801` |
| `src/yaml_private.h` | `8dffe95c784665eba4ee88169456ebd41137a6d80ad13d536df6ad10d7a4d459` | `8dffe95c784665eba4ee88169456ebd41137a6d80ad13d536df6ad10d7a4d459` |
| `src/api.c` | `71f9263a2c77237762c8fa5e7c3d2bdd35723ecf1e2898222c7a603a5bf3f2db` | `71f9263a2c77237762c8fa5e7c3d2bdd35723ecf1e2898222c7a603a5bf3f2db` |
| `src/parser.c` | `b418ed8d6ef7e86dbd0e59c8dc7a00c8e48200b0252dd41e25d00d4616c7b2dc` | `0c4c48b374025ad09f076bd4e68cb36062a300cdedbcb4afd69a6d09bec646b3` |
| `src/reader.c` | `c428a44b177391557bdf49177c1dc0216edf2a2bbe0e24ca2ef2725ef7f01e9d` | `c428a44b177391557bdf49177c1dc0216edf2a2bbe0e24ca2ef2725ef7f01e9d` |
| `src/scanner.c` | `bc3b30ceec0c696925e9703845874d7fafefb42bfbd3006dc4a02fc6d646f2d1` | `bc3b30ceec0c696925e9703845874d7fafefb42bfbd3006dc4a02fc6d646f2d1` |
| `License` | `c40112449f254b9753045925248313e9270efa36d226b22d82d4cc6c43c57f29` | `c40112449f254b9753045925248313e9270efa36d226b22d82d4cc6c43c57f29` |

修正差分のSHA-256: `5ff690fa9a32dfa9e5523ff1cfb3d40f83cada51043644835bd25db94e2455df`。

更新時は版・commit・全hashと限定差分を更新し、YAML構文・深さ/byte/node上限・拒否構文・C解放・配布ライセンス・macOS/Linuxビルドを再検証する。C内部のmallocはZig allocatorの確保失敗テストからは注入できない。
