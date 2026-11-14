#!/bin/sh
# checkoutごとのツールを準備する。共有hookやローカル資料は変更しない。
set -eu
cd "$(dirname "$0")/.."
git rev-parse --show-toplevel >/dev/null
command -v mise >/dev/null
mise trust mise.toml
mise install
mise exec -- lefthook validate
hook=$(git rev-parse --git-path hooks/pre-commit)
if [ -f "$hook" ] && [ -x "$hook" ] && grep -q 'call_lefthook run "pre-commit"' "$hook"; then
    printf '%s\n' '既存のLefthook hookを利用します。'
else
    printf '%s\n' 'pre-commit hookが未導入、または別の設定です。共有hookを確認してから mise exec -- lefthook install を実行してください。' >&2
    exit 1
fi
mise exec -- zig version
