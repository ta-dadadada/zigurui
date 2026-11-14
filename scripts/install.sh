#!/bin/sh
# Releaseの検証と配置だけを行い、利用者の設定・索引・原本には触れない。
set -eu

fail() { printf 'zigurui installer: %s\n' "$*" >&2; exit 1; }
usage() {
    cat <<'EOF'
Usage: sh install.sh --version vX.Y.Z[-alpha.N|-beta.N|-rc.N] [--channel stable|prerelease] [--bin-dir /absolute/path]
版の指定は必須。プレリリースには --channel prerelease も指定する。
既定の配置先は $HOME/.local/bin。既存の管理外ファイルは上書きしない。
EOF
}

main() {
    version=
    channel=stable
    bin_dir=
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --version|--channel|--bin-dir)
                [ "$#" -ge 2 ] || fail "$1 には値が必要です"
                case "$1" in
                    --version) version=$2 ;;
                    --channel) channel=$2 ;;
                    --bin-dir) bin_dir=$2 ;;
                esac
                shift 2 ;;
            --help|-h) usage; exit 0 ;;
            *) fail "不明な引数: $1" ;;
        esac
    done
    # 非対応環境ではダウンロードやディレクトリ作成を行わない。
    platform=$(uname -s)-$(uname -m)
    case "$platform" in
        Darwin-arm64) target=macos-arm64 ;;
        Linux-x86_64) target=linux-x86_64 ;;
        *) fail "非対応のOS・CPUです: ${platform}（macOS arm64 / Linux x86_64に対応）" ;;
    esac
    [ -n "$version" ] || fail '導入する版を --version で指定してください。プレリリースには --channel prerelease も必要です'
    [ "${#version}" -le 64 ] || fail '版の指定が長すぎます'
    number='(0|[1-9][0-9]*)'
    core="v$number\.$number\.$number"
    case "$channel" in
        stable) pattern="^$core$" ;;
        prerelease) pattern="^$core-(alpha|beta|rc)\.$number$" ;;
        *) fail 'channelはstableまたはprereleaseです' ;;
    esac
    printf '%s\n' "$version" | LC_ALL=C grep -Eq "$pattern" || fail '版の形式とchannelが一致しません'
    if [ -z "$bin_dir" ]; then
        [ -n "${HOME:-}" ] || fail 'HOMEまたは--bin-dirを指定してください'
        bin_dir=$HOME/.local/bin
    fi
    case "$bin_dir" in /*) ;; *) fail '配置先は絶対パスで指定してください' ;; esac
    # 改行はreceiptとPATHの表示を曖昧にするため受け付けない。
    case "$bin_dir" in *'
'*) fail '配置先に改行は使用できません' ;; esac
    for tool in curl tar mktemp awk; do
        command -v "$tool" >/dev/null 2>&1 || fail "必要なコマンドがありません: $tool"
    done
    if command -v sha256sum >/dev/null 2>&1; then
        hash_tool=sha256sum
    elif command -v shasum >/dev/null 2>&1; then
        hash_tool=shasum
    else
        fail 'SHA-256の検証にsha256sumまたはshasumが必要です'
    fi
    printf '導入予定: %s (%s, %s)\n配置先: %s/zigurui\n' "$version" "$channel" "$target" "$bin_dir"
    mkdir -p "$bin_dir" || fail "配置先を作成できません: $bin_dir"
    bin_dir=$(CDPATH= cd -- "$bin_dir" && pwd -P)
    destination=$bin_dir/zigurui
    metadata=$bin_dir/.zigurui-install
    [ ! -L "$metadata" ] || fail '導入記録がシンボリックリンクです'
    [ ! -e "$metadata" ] || [ -d "$metadata" ] || fail '導入記録の場所がディレクトリではありません'
    lock=$bin_dir/.zigurui-install.lock
    mkdir "$lock" 2>/dev/null || fail "配置先に書き込めないか、別の導入が進行中です: $lock"
    # 自分が作成した作業領域だけを片付ける。利用者のデータ領域は対象にしない。
    work=
    trap 'if [ -n "$work" ]; then rm -rf "$work"; fi; rmdir "$lock"' EXIT
    trap 'exit 1' HUP INT TERM
    check_destination
    work=$(mktemp -d "$bin_dir/.zigurui-stage.XXXXXXXX") || fail '一時領域を作成できません'
    stem=zigurui-$version-$target
    asset=$stem.tar.gz
    base=https://github.com/ta-dadadada/zigurui/releases/download/$version
    fetch "$base/$asset" "$work/$asset"
    fetch "$base/SHA256SUMS" "$work/SHA256SUMS"
    expected=$(awk -v name="$asset" '$2 == name { n++; value=$1 } END { if(n != 1 || length(value) != 64 || value ~ /[^0-9a-f]/) exit 1; print value }' "$work/SHA256SUMS") || fail '対象アセットのチェックサムが欠落・重複・不正です'
    actual=$(digest "$work/$asset")
    [ "$actual" = "$expected" ] || fail 'SHA-256が一致しません。既存バイナリは維持しました'
    # 許可した通常ファイルだけを標準出力へ展開し、リンクやパストラバーサルを拒否する。
    tar -tzf "$work/$asset" > "$work/members" || fail 'アーカイブを読み取れません'
    awk -v stem="$stem" '
        { prefix = stem "/"; name = substr($0, length(prefix) + 1)
          if (substr($0, 1, length(prefix)) != prefix || name !~ /^(zigurui|README[.]md|LICENSE(-utf8proc[.]md|-libyaml[.]txt|[.]md|[.]txt)?|COPYING)$/ || seen[$0]++) exit 1 }
        END { if (!seen[stem "/zigurui"] || !seen[stem "/README.md"] || !seen[stem "/LICENSE-utf8proc.md"] || !seen[stem "/LICENSE-libyaml.txt"]) exit 1 }
    ' "$work/members" || fail 'アーカイブの内容が想定と異なります'
    tar -tvzf "$work/$asset" > "$work/types" || fail 'アーカイブの種類を確認できません'
    awk 'substr($0, 1, 1) != "-" { exit 1 }' "$work/types" || fail 'アーカイブに通常ファイル以外が含まれています'
    mkdir "$work/documents"
    while IFS= read -r member; do
        name=${member#*/}
        if [ "$name" = zigurui ]; then output=$work/zigurui; else output=$work/documents/$name; fi
        tar -xOzf "$work/$asset" "$member" > "$output" || fail "展開できません: $member"
    done < "$work/members"
    [ -s "$work/zigurui" ] && [ -s "$work/documents/LICENSE-utf8proc.md" ] && [ -s "$work/documents/LICENSE-libyaml.txt" ] || fail '必要なファイルが空です'
    chmod 755 "$work/zigurui"
    binary_hash=$(digest "$work/zigurui")
    printf '%s\n%s\n' "$binary_hash" "$version" > "$work/receipt"
    # 検証後も配置先の管理状態を再確認する。
    check_destination
    for directory in "$metadata/licenses" "$metadata/receipts"; do
        [ ! -L "$directory" ] || fail "導入記録の下にシンボリックリンクがあります: $directory"
    done
    mkdir -p "$metadata/licenses" "$metadata/receipts"
    # ライセンスを残せない場合はバイナリを切り替えない。
    license_dir=$(mktemp -d "$metadata/licenses/$version.XXXXXXXX")
    cp "$work/documents/"* "$license_dir/" || fail 'ライセンスとREADMEを保存できません'
    # 各版のハッシュに対応する記録を先に確定する。切替中断後も新旧どちらを再実行できる。
    [ ! -L "$metadata/receipts/$binary_hash" ] || fail '導入記録がシンボリックリンクです'
    mv -f "$work/receipt" "$metadata/receipts/$binary_hash" || fail '導入記録を保存できません'
    mv -f "$work/zigurui" "$destination" || fail 'バイナリを配置できません'
    printf '導入完了: %s\nライセンス: %s\n' "$destination" "$license_dir"
    case ":${PATH:-}:" in
        *":$bin_dir:"*) ;;
        *) printf 'PATHに次のディレクトリを追加してください: %s\nシェル設定は変更していません。\n' "$bin_dir" ;;
    esac
    active=$(command -v zigurui || :)
    if [ -n "$active" ] && [ "$active" != "$destination" ]; then
        printf '現在PATHで選ばれるzigurui: %s\n導入先を直接指定して確認してください: %s --version\n' "$active" "$destination"
    fi
}

digest() {
    # ファイル名のエスケープ表記がハッシュへ混ざらないよう標準入力を使う。
    if [ "$hash_tool" = sha256sum ]; then sha256sum < "$1"; else shasum -a 256 < "$1"; fi | awk '{print $1}'
}
fetch() {
    curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL "$1" -o "$2" || fail "取得できません: $1（既存バイナリは維持しました）"
}
check_destination() {
    [ ! -L "$destination" ] || fail '配置先はシンボリックリンクです。他の管理方法で更新するか別の--bin-dirを指定してください'
    if [ -e "$destination" ]; then
        [ -f "$destination" ] && [ ! -L "$metadata/receipts" ] || fail '管理外の配置先は上書きしません。別の--bin-dirを指定してください'
        installed_hash=$(digest "$destination")
        receipt=$metadata/receipts/$installed_hash
        [ -f "$receipt" ] && [ ! -L "$receipt" ] || fail '配置先が導入記録と異なります。他の管理方法で更新するか別の--bin-dirを指定してください'
        recorded=$(sed -n '1p' "$receipt")
        [ "$installed_hash" = "$recorded" ] || fail '配置先が導入記録と異なります'
    fi
}

# pipe経由でも、スクリプトを最後まで取得してから実処理を始める。
main "$@"
