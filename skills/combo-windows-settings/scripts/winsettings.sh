#!/bin/bash
# winsettings.sh — combo-windows-settings のドライバ(WSL / Git Bash から powershell.exe を呼ぶ)
#
# 設定はグループ単位。../groups/<name>/ に README.md(手順)と check.ps1(読み取り専用の確認)を
# 置く/消すだけで増減する(データ駆動)。ドライバは Windows の設定を一切変更しない。
#
# 使い方:
#   winsettings.sh list                 グループ一覧(README.md の 1 行目)
#   winsettings.sh show   <group>       グループの手順(README.md)を表示
#   winsettings.sh status [group...]    check.ps1 を実行し OK / NG / MANUAL / INFO を表示(省略時は全件)
#   winsettings.sh lint                 groups/*/ の定義検査(必須ファイル・書き込み系コマンドの禁止・構文)
#
# check.ps1 へ渡す任意の設定(環境変数 WINSET_*。値は Windows 形式。%USERPROFILE% などは展開される):
#   WINSET_SEARCH_EXCLUDE   検索インデックスから除外すべきフォルダ(; 区切り)
#   WINSET_STARTUP_DISABLE  スタートアップで無効にすべきアプリ名の正規表現
#
# 終了コード: 0=全項目 OK / 1=実行失敗 / 2=使い方誤り / 3=powershell.exe が無い
#             6=NG あり / 7=NG は無いが MANUAL(目視確認が要る項目)あり

set -o pipefail
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GROUPS_DIR="$SKILL_DIR/groups"
PRELUDE="$SKILL_DIR/scripts/prelude.ps1"
PS="${WINSET_POWERSHELL:-powershell.exe}"

die()       { printf '[winsettings] ERROR: %s\n' "$*" >&2; exit 1; }
usage_die() { printf '[winsettings] USAGE: %s\n' "$*" >&2; exit 2; }

all_groups() {
    local d
    for d in "$GROUPS_DIR"/*/; do
        d="${d%/}"; d="${d##*/}"
        case "$d" in _*) continue ;; esac
        echo "$d"
    done
}
check_group() { [ -d "$GROUPS_DIR/$1" ] && case "$1" in _*|*/*|'') false ;; *) true ;; esac || usage_die "未登録のグループ: $1(list で確認)"; }
need_ps() { command -v "$PS" >/dev/null 2>&1 || { echo "[winsettings] powershell.exe が見つからない(WSL / Git Bash の Windows 上で実行する)" >&2; exit 3; }; }

# PowerShell 文字列リテラル用に ' を '' へ
psq() { printf "'%s'" "${1//\'/\'\'}"; }

# prelude + 環境注入 + 本体 を UTF-16LE base64 にして -EncodedCommand で渡す(一時ファイル・実行ポリシー不要)
run_ps() {
    local body="$1" script v
    script="$(cat "$PRELUDE")"$'\n'
    for v in $(compgen -e | grep '^WINSET_' | grep -v '^WINSET_POWERSHELL$'); do
        script+="\$env:$v = $(psq "${!v}")"$'\n'
    done
    script+="$body"
    local errf rc
    errf="$(mktemp)"
    "$PS" -NoProfile -NonInteractive -EncodedCommand "$(printf '%s' "$script" | iconv -f UTF-8 -t UTF-16LE | base64 -w0)" 2>"$errf" | tr -d '\r'
    rc=${PIPESTATUS[0]}
    # stderr は CLIXML で返るので <S S="Error">…</S> の本文だけを ERROR 行にする
    if [ -s "$errf" ]; then
        grep -o '<S S="Error">[^<]*</S>' "$errf" | sed -e 's/<S S="Error">//; s#</S>##; s/_x000D__x000A_//g' \
            | awk 'NF && !/^[+] *~/' | sed 's/^/ERROR\t-\t/'
        grep -q '<S S="Error">' "$errf" || grep -v '^#< CLIXML' "$errf" | sed 's/^/ERROR\t-\t/'
    fi
    rm -f "$errf"
    return "$rc"
}

# README.md の「## 確認項目」節の表の 1 列目にある `item` を 1 行ずつ出す(`<…>` を含むものは接頭辞の指定)
readme_items() {
    awk '/^## 確認項目/{f=1; next} /^## /{f=0} f && /^\|/ && !/^[|: -]+$/' "$GROUPS_DIR/$1/README.md" \
        | awk -F'|' '{print $2}' | grep -o '`[^`]*`' | tr -d '`'
}

cmd_list() {
    local g
    for g in $(all_groups); do
        printf '%-24s %s\n' "$g" "$(head -1 "$GROUPS_DIR/$g/README.md" | sed 's/^# *//')"
    done
}

cmd_show() {
    [ $# -eq 1 ] || usage_die "show <group>"
    check_group "$1"; cat "$GROUPS_DIR/$1/README.md"
}

cmd_status() {
    need_ps
    local groups=("$@") g out ng=0 manual=0 fail=0
    [ ${#groups[@]} -eq 0 ] && mapfile -t groups < <(all_groups)
    for g in "${groups[@]}"; do check_group "$g"; done
    printf '%-24s %-7s %-34s %s\n' GROUP STATUS ITEM DETAIL
    for g in "${groups[@]}"; do
        if ! out="$(run_ps "$(cat "$GROUPS_DIR/$g/check.ps1")")"; then
            printf '%-24s %-7s %-34s %s\n' "$g" ERROR "-" "check.ps1 の実行に失敗"; fail=1
        fi
        local got=0 emitted=()
        while IFS=$'\t' read -r st item detail; do
            case "$st" in
                OK|NG|MANUAL|INFO) got=1 ;;
                ERROR) printf '%-24s %-7s %-34s %s\n' "$g" ERROR "-" "$detail"; fail=1; continue ;;
                '') continue ;;
                *) printf '%-24s %-7s %-34s %s\n' "$g" ERROR "-" "$st $item $detail"; fail=1; continue ;;
            esac
            emitted+=("$item")
            [ "$st" = NG ] && ng=1
            [ "$st" = MANUAL ] && manual=1
            printf '%-24s %-7s %-34s %s\n' "$g" "$st" "$item" "$detail"
        done <<< "$out"
        [ "$got" = 1 ] || { printf '%-24s %-7s %-34s %s\n' "$g" ERROR "-" "結果行が 1 つも無い"; fail=1; }
        # README の確認項目がすべて出ているか(`<…>` を含む項目は接頭辞が一致する行が 1 つあればよい)
        local req e hit exact=()
        mapfile -t exact < <(readme_items "$g" | grep -v '<')
        while IFS= read -r req; do
            [ -n "$req" ] || continue
            hit=0
            for e in "${emitted[@]}"; do
                case "$req" in
                    # 接頭辞の一致は、表に別途書かれた固定の item(例: exclude:dev-dir)を除いて数える
                    *'<'*) [[ "$e" == "${req%%<*}"* ]] && ! printf '%s\n' "${exact[@]}" | grep -qxF -- "$e" && { hit=1; break; } ;;
                    *) [ "$e" = "$req" ] && { hit=1; break; } ;;
                esac
            done
            [ "$hit" = 1 ] || { printf '%-24s %-7s %-34s %s\n' "$g" ERROR "$req" "README の確認項目が check.ps1 から出ていない"; fail=1; }
        done < <(readme_items "$g")
    done
    [ "$fail" = 1 ] && return 1
    [ "$ng" = 1 ] && return 6
    [ "$manual" = 1 ] && return 7
    return 0
}

# check.ps1 は読み取り専用。設定を変えるコマンドレット・コマンドを直書きしたら NG
WRITE_RE='(Set|New|Remove|Rename|Clear|Copy|Move)-Item(Property)?|Set-DOConfig|Set-Service|Stop-|Start-Process|Disable-|Enable-|Uninstall-|powercfg(\.exe)?[[:space:]]+[-/](set|change|s[[:space:]]|setactive|x[[:space:]])|reg(\.exe)?[[:space:]]+(add|delete|import)|Out-File|Set-Content|Add-Content|Tee-Object|Invoke-Expression|\biex\b|Invoke-WmiMethod|Invoke-CimMethod|Set-CimInstance|\[(System\.)?IO\.(File|Directory)\]::(Write|Append|Create|Delete|Move|Copy|Replace)|\.(SetValue|DeleteValue|DeleteSubKey(Tree)?|CreateSubKey)\('
# 文字列照合なので完全ではない(動的に組み立てたコマンドは拾えない)。check.ps1 は追加時に目でも確かめる

cmd_lint() {
    local g f bad=0
    [ -f "$PRELUDE" ] || { echo "NG prelude.ps1 が無い"; bad=1; }
    # 全グループの前に連結される prelude.ps1 も読み取り専用であること
    if [ -f "$PRELUDE" ] && grep -nEi "$WRITE_RE" "$PRELUDE" | grep -v '^[0-9]*:[[:space:]]*#' >/dev/null; then
        echo "NG prelude.ps1 に書き込み系コマンドがある(確認は読み取り専用)"
        grep -nEi "$WRITE_RE" "$PRELUDE" | grep -v '^[0-9]*:[[:space:]]*#' | sed 's/^/    /'
        bad=1
    fi
    for g in $(all_groups); do
        for f in README.md check.ps1; do
            [ -f "$GROUPS_DIR/$g/$f" ] || { echo "NG $g: $f が無い"; bad=1; }
        done
        [ -f "$GROUPS_DIR/$g/README.md" ] && ! head -1 "$GROUPS_DIR/$g/README.md" | grep -q '^# ' && { echo "NG $g: README.md の 1 行目が '# <タイトル>' でない"; bad=1; }
        [ -f "$GROUPS_DIR/$g/README.md" ] && [ -z "$(readme_items "$g")" ] && { echo "NG $g: README.md の「## 確認項目」の表に \`item\` が無い"; bad=1; }
        if [ -f "$GROUPS_DIR/$g/check.ps1" ] && grep -nEi "$WRITE_RE" "$GROUPS_DIR/$g/check.ps1" | grep -v '^[0-9]*:[[:space:]]*#' >/dev/null; then
            echo "NG $g: check.ps1 に書き込み系コマンドがある(確認は読み取り専用)"
            grep -nEi "$WRITE_RE" "$GROUPS_DIR/$g/check.ps1" | grep -v '^[0-9]*:[[:space:]]*#' | sed 's/^/    /'
            bad=1
        fi
    done
    if command -v "$PS" >/dev/null 2>&1; then
        for g in $(all_groups); do
            [ -f "$GROUPS_DIR/$g/check.ps1" ] || continue
            local errs prc
            errs="$(run_ps "\$e=\$null; [void][System.Management.Automation.Language.Parser]::ParseInput(@'
$(cat "$GROUPS_DIR/$g/check.ps1")
'@, [ref]\$null, [ref]\$e); \$e | ForEach-Object { 'L{0}: {1}' -f \$_.Extent.StartLineNumber, \$_.Message }; 'PARSE_DONE'")"
            prc=$?
            # 終了コードと完了マーカーで、構文検査が実際に最後まで走ったことを確かめる
            if [ "$prc" -ne 0 ] || ! grep -qx 'PARSE_DONE' <<< "$errs"; then
                echo "NG $g: 構文検査を実行できなかった(rc=$prc)"; echo "$errs" | grep -vx 'PARSE_DONE' | sed 's/^/    /'; bad=1
            elif [ -n "$(grep -vx 'PARSE_DONE' <<< "$errs")" ]; then
                echo "NG $g: check.ps1 の構文エラー"; grep -vx 'PARSE_DONE' <<< "$errs" | sed 's/^/    /'; bad=1
            fi
        done
    else
        echo "NG powershell.exe が無いので構文検査を実行できない(WSL / Git Bash の Windows 上で実行する)"; bad=1
    fi
    [ "$bad" = 0 ] && echo "lint OK" && return 0
    return 1
}

case "${1:-}" in
    list)   shift; cmd_list "$@" ;;
    show)   shift; cmd_show "$@" ;;
    status) shift; cmd_status "$@" ;;
    lint)   shift; cmd_lint "$@" ;;
    *) usage_die "winsettings.sh {list|show <group>|status [group...]|lint}" ;;
esac
