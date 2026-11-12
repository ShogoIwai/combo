#!/bin/bash
# toolsetup.sh — combo-linux-tool-setup のドライバ
#
# 対象ツールは ../tools/<name>.sh を 1 ファイル置く/消すだけで増減する(データ駆動)。
# ドライバは sudo を一切実行しない。root 権限が要る操作は SUDO_REQUEST として
# 標準出力に列挙し、終了コード 3 で止まる(ユーザーが実行してから再実行する)。
#
# 使い方:
#   toolsetup.sh list                     登録ツールの一覧(説明・依存)
#   toolsetup.sh status  [tool...]        導入版・最新版・状態(ok/outdated/missing)
#   toolsetup.sh install [tool...]        未導入なら依存ごと導入(導入済みは設定同期のみ)
#   toolsetup.sh update  [tool...]        導入済みを最新へ(未導入は install と同じ)
#   toolsetup.sh remove  tool... --yes    削除(--yes 必須・依存元が残るなら拒否・未導入なら残存設定の後始末のみ)
#   toolsetup.sh lint                     tools/*.sh の定義検査(必須関数・sudo 直書き禁止)
# 共通オプション:
#   --dry-run   変更系コマンドを実行せず表示だけする(ファイル・nvm 状態も変えない)
#   --yes       remove など破壊的操作の確認を済ませたことを示す
#
# 終了コード: 0=成功 / 1=失敗 / 2=使い方誤り / 3=sudo 依頼あり(ユーザー実行待ち)
#             4=確認待ち(--yes が要る) / 5=自己更新の拒否(別の端末・エージェントで実行)
#
# 注意: nvm.sh は set -u と相性が悪いので -u は使わない。

set -o pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$SKILL_DIR/tools"
MARK="combo-linux-tool-setup"

DRY_RUN=0
ASSUME_YES=0
SUDO_REQUESTS=()

# ---------------------------------------------------------------- 共通ヘルパ
log()   { printf '[toolsetup] %s\n' "$*" >&2; }
warn()  { printf '[toolsetup] WARN: %s\n' "$*" >&2; }
die()   { printf '[toolsetup] ERROR: %s\n' "$*" >&2; exit 1; }
usage_die() { printf '[toolsetup] USAGE: %s\n' "$*" >&2; exit 2; }

# 変更系コマンドは必ず run を通す(--dry-run で表示のみになる)
run() {
    if [ "$DRY_RUN" = 1 ]; then
        printf '[dry-run] %s\n' "$*" >&2
        return 0
    fi
    log "+ $*"
    eval "$@"
}

# root 権限が要る操作を依頼リストへ積む。ドライバも tools/*.sh も sudo を直接呼ばない。
# 例: need_sudo "apt-get install -y curl" "nvm の取得に curl が要る"
# 依頼は「理由のコメント行」と「そのまま貼れるコマンド行」の 2 行で出す。
need_sudo() {
    SUDO_REQUESTS+=("# $2" "sudo $1")
}

# パッケージマネージャ別のインストールコマンド文字列(依頼文用)
pkg_install_cmd() {
    if command -v apt-get >/dev/null 2>&1; then echo "apt-get install -y $*"
    elif command -v dnf >/dev/null 2>&1; then echo "dnf install -y $*"
    elif command -v yum >/dev/null 2>&1; then echo "yum install -y $*"
    elif command -v zypper >/dev/null 2>&1; then echo "zypper install -y $*"
    else echo "<package-manager> install $*"
    fi
}

# 前提コマンドが無ければ sudo 依頼を積む。戻り値 1 = 足りない
require_cmd() {
    local cmd="$1" pkg="${2:-$1}" why="${3:-$1 が必要}"
    command -v "$cmd" >/dev/null 2>&1 && return 0
    need_sudo "$(pkg_install_cmd "$pkg")" "$why"
    return 1
}

flush_sudo_requests() {
    [ "${#SUDO_REQUESTS[@]}" -eq 0 ] && return 0
    echo "SUDO_REQUEST: 以下を root 権限で実行してから同じコマンドを再実行してください"
    printf '%s\n' "${SUDO_REQUESTS[@]}" | awk 'NF && !seen[$0]++' | sed 's/^/  /'
    return 3
}

# ---------------------------------------------------------------- dry-run の計画状態
# dry-run では依存の導入は実際には起きないので、「導入される予定の版」を記録し、
# 後続ツールは planned_version で導入済みとみなして計画を続ける。
declare -A PLANNED=()
planned_version() { printf '%s' "${PLANNED[$1]:-}"; }

# ---------------------------------------------------------------- nvm / node
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

nvm_load() {
    [ -s "$NVM_DIR/nvm.sh" ] || return 1
    declare -F nvm >/dev/null || . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || return 1
    nvm use default >/dev/null 2>&1 || true
    return 0
}

# ---------------------------------------------------------------- npm グローバルパッケージ
npm_ready() { nvm_load && command -v npm >/dev/null 2>&1; }

npm_pkg_version() {   # 導入版(無ければ空)
    npm_ready || return 0
    # npm root -g はパス中の UUID 風文字列を *** に伏せることがあるので node の位置から求める
    local pj; pj="$(dirname "$(dirname "$(command -v node)")")/lib/node_modules/$1/package.json"
    [ -f "$pj" ] || return 0
    PJ="$pj" node -p 'JSON.parse(require("fs").readFileSync(process.env.PJ,"utf8")).version' 2>/dev/null
}
# 最新版は npm を使わずレジストリから直接取る(node 未導入でも status/dry-run が通る)
npm_pkg_latest() {
    local enc="${1/\//%2f}"
    curl -fsSL --max-time 15 "https://registry.npmjs.org/$enc/latest" 2>/dev/null \
        | grep -o '"version":"[^"]*"' | head -n1 | cut -d'"' -f4
}
# dry-run で npm がまだ使えなくても、計画上使える見込みがあれば計画を続ける:
#   - node 自体が導入予定、または
#   - nvm が導入予定(=nvm.sh を入れ直す)で、node が導入済み(版ディレクトリが残っている)
# nvm の導入予定だけでは可にしない(node が無ければ npm は使えない)。
npm_planned() {
    [ "$DRY_RUN" = 1 ] || return 1
    [ -n "$(planned_version node)" ] && return 0
    [ -n "$(planned_version nvm)" ] && [ -n "$(installed_version node)" ]
}
npm_pkg_install() {
    if ! npm_ready; then
        npm_planned || die "npm が使えない(node 未導入)"
    fi
    run "npm install -g '$1@latest'"
}
npm_pkg_remove()  { npm_ready || die "npm が使えない"; run "npm uninstall -g '$1'"; }

# ---------------------------------------------------------------- シェル初期化ファイルの管理ブロック
# 例: rc_block ~/.cshrc node 'setenv PATH "$HOME/.nvm/versions/node/<ver>/bin:${PATH}"' [legacy_regex]
#   - "# >>> combo-linux-tool-setup <id> >>>" 〜 "# <<< ... <<<" の間だけを書き換える
#   - マーカーが壊れている(片方だけ・重複・順序逆)ときは何も変えずに失敗する
#   - legacy_regex に一致する管理外の行(手作業で足した行)は、その位置にブロックを
#     置き換えて入れる(PATH の優先順位を変えないため)。ただし書き込み時に旧行が複数ある、
#     または既存ブロックと旧行が混在するときは、まとめると優先順位が変わり得るので何も変えずに失敗する
#   - 内容が変わるときだけ <file>.bak.<日時>.<pid> を作ってから書く(バックアップ失敗なら書かない)
#   - body が空ならブロックを削除する
#   - dry-run では差分を表示するだけで、ファイルが無くても作らない
rc_block() {
    local file="$1" id="$2" body="$3" legacy="${4:-}"
    local begin="# >>> $MARK $id >>>" end="# <<< $MARK $id <<<"
    local src="$file"; [ -e "$file" ] || src=/dev/null

    # マーカー検査
    local nb ne lb le
    nb="$(grep -cxF -- "$begin" "$src")"; ne="$(grep -cxF -- "$end" "$src")"
    if [ "$nb" -gt 1 ] || [ "$ne" -gt 1 ] || [ "$nb" != "$ne" ]; then
        warn "$file の管理ブロック $id のマーカーが不正(開始 $nb / 終了 $ne)。手で直してから再実行してください"
        return 1
    fi
    if [ "$nb" = 1 ]; then
        lb="$(grep -nxF -- "$begin" "$src" | cut -d: -f1)"; le="$(grep -nxF -- "$end" "$src" | cut -d: -f1)"
        [ "$lb" -lt "$le" ] || { warn "$file の管理ブロック $id の終了マーカーが開始より前にある"; return 1; }
    fi

    # 書き込み(body あり)のとき、手書きの旧行が複数ある/既存ブロックと旧行が混在するなら拒否する。
    # 1 か所へまとめると、間に挟まる他の PATH 行との優先順位が変わり得るため(削除は順位を変えないので許す)
    if [ -n "$body" ] && [ -n "$legacy" ]; then
        local nl
        nl="$(BEGIN_M="$begin" END_M="$end" LEGACY="$legacy" awk '
            $0 == ENVIRON["BEGIN_M"] { inb=1; next }
            $0 == ENVIRON["END_M"]   { inb=0; next }
            !inb && $0 ~ ENVIRON["LEGACY"] { n++ }
            END { print n+0 }' "$src")"
        if [ "$nl" -gt 1 ] || { [ "$nl" -ge 1 ] && [ "$nb" = 1 ]; }; then
            warn "$file に管理ブロック $id の対象行が複数ある(管理ブロック $nb / 手書きの行 $nl)。PATH の優先順位が変わり得るので自動ではまとめない。1 つに整理してから再実行してください"
            return 1
        fi
    fi

    local tmp; tmp="$(mktemp)" || return 1
    BEGIN_M="$begin" END_M="$end" BODY="$body" LEGACY="$legacy" awk '
        BEGIN { b=ENVIRON["BEGIN_M"]; e=ENVIRON["END_M"]; body=ENVIRON["BODY"]; lg=ENVIRON["LEGACY"]; done=0; inblk=0 }
        function emit() { if (!done && body != "") { print b; print body; print e } done=1 }
        $0 == b { inblk=1; emit(); next }
        inblk { if ($0 == e) inblk=0; next }
        lg != "" && $0 ~ lg { emit(); next }
        { print; last=$0 }
        END { if (!done && body != "") { if (NR > 0 && last != "") print ""; print b; print body; print e } }
    ' "$src" > "$tmp" || { rm -f "$tmp"; return 1; }
    if cmp -s "$src" "$tmp"; then rm -f "$tmp"; return 0; fi
    if [ "$DRY_RUN" = 1 ]; then
        printf '[dry-run] %s の管理ブロック %s を更新:\n' "$file" "$id" >&2
        # 差分は +/- 行だけ。管理外の行に認証情報があり得るので password を含む行は出さない
        diff -u "$src" "$tmp" | sed -n '3,$p' | grep '^[+-]' | grep -v -i 'password' >&2
        rm -f "$tmp"; return 0
    fi
    local bak=""
    if [ -e "$file" ]; then
        bak="$file.bak.$(date +%Y%m%d%H%M%S).$$"
        cp -p "$file" "$bak" || { rm -f "$tmp"; warn "バックアップ $bak を作れない。$file は変更しない"; return 1; }
    fi
    cat "$tmp" > "$file" || { rm -f "$tmp"; warn "$file に書けない${bak:+(バックアップ: $bak)}"; return 1; }
    rm -f "$tmp"
    log "$file の管理ブロック $id を更新${bak:+(バックアップ: $bak)}"
}

# ---------------------------------------------------------------- 自己更新の検出
# このスクリプトを起動したプロセスの祖先(=実行中のエージェント)が使っている
# ツールを返す。npm の更新・削除は実行中の配置を置き換えるため(実行ファイルが
# "(deleted)" になる実例あり)、自分を動かしているツールは触らせない。
# tools/<name>.sh の TOOL_SELF_REGEX(祖先の exe/cmdline に対する ERE)で判定する。
ANCESTRY=""
collect_ancestry() {
    [ -n "$ANCESTRY" ] && return 0
    local p=$PPID exe cmd
    while [ -n "$p" ] && [ "$p" -gt 1 ] 2>/dev/null; do
        exe="$(readlink "/proc/$p/exe" 2>/dev/null)"
        cmd="$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)"
        ANCESTRY+="$exe $cmd"$'\n'
        p="$(awk '{print $4}' "/proc/$p/stat" 2>/dev/null)"
    done
}
hosts_self() {   # $1=tool $2=操作 → 0 ならこのツールが実行中エージェントを動かしていて、その操作は止める
    local ops; ops="$(tool_field "$1" TOOL_SELF_BLOCK)"; ops="${ops:-update remove}"
    case " $ops " in *" $2 "*) ;; *) return 1 ;; esac
    local re; re="$( load_tool "$1"; tool_self_regex )"
    [ -n "$re" ] || return 1
    collect_ancestry
    printf '%s' "$ANCESTRY" | grep -qE -- "$re"
}
block_self() {   # $1=操作 $2...=対象
    local op="$1" t hits=(); shift
    for t in "$@"; do hosts_self "$t" "$op" && hits+=("$t"); done
    [ "${#hits[@]}" -eq 0 ] && return 0
    if [ "$DRY_RUN" = 1 ]; then
        warn "実行中のエージェントが ${hits[*]} で動いている。本実行は拒否される(exit 5)"
        return 0
    fi
    echo "SELF_UPDATE_BLOCKED: 実行中のエージェントが ${hits[*]} で動いているため $op しない。"
    echo "  もう一方のエージェント、またはエージェント外の端末で次を実行してください:"
    echo "  bash $SKILL_DIR/scripts/toolsetup.sh $op ${*}"
    exit 5
}

# ---------------------------------------------------------------- ツール定義の読み込み
# tools/<name>.sh が定義するもの(テンプレートは tools/_template.sh.txt)
#   TOOL_DESC        説明 1 行
#   TOOL_DEPS        依存ツール名(空白区切り)。依存は先に install される
#   TOOL_SELF_REGEX  実行中エージェントがこのツールで動いているかの判定(祖先の exe/cmdline に ERE)  任意
#   TOOL_SELF_BLOCK  上が一致したとき止める操作(既定 "update remove")                 任意
#   tool_version     導入版を出力(未導入なら何も出さない)       必須
#   tool_uptodate    0=指定どおり(更新不要)。版の一致だけで判断できないツール用(未定義なら導入版=最新版で判定)  任意
#   tool_owned       このスキルが入れて管理している版(remove の対象・判定に使う)。未定義なら tool_version  任意
#   tool_latest      最新版を出力(取れなければ何も出さない)。ローカル状態を変えないこと  必須
#   tool_prereq      前提チェック。不足は require_cmd/need_sudo で積み、1 を返す  任意
#   tool_install     導入                                         必須
#   tool_update      更新(未定義なら tool_install を使う)         任意
#   tool_sync        版に関係ない設定の同期・修復(冪等)。install/update の度に呼ぶ  任意
#   tool_remove      削除(実行後 tool_version が空になること)   必須
#   tool_residue     未導入でも残る設定(管理ブロック・別名等)を 1 行ずつ出力。無ければ何も出さない  任意
#   tool_cleanup     tool_residue の残存設定を消す(冪等)。未導入時の remove で呼ぶ  任意
#   tool_post        導入/更新後にユーザーへ伝える次の手順を出力  任意
TOOL_FUNCS="tool_version tool_owned tool_uptodate tool_latest tool_prereq tool_install tool_update tool_sync tool_remove tool_residue tool_cleanup tool_post tool_self_regex"

all_tools() {
    local f
    for f in "$TOOLS_DIR"/*.sh; do [ -e "$f" ] && basename "$f" .sh; done
}

tool_exists() { [ -f "$TOOLS_DIR/$1.sh" ]; }

load_tool() {
    local name="$1" fn
    tool_exists "$name" || usage_die "未登録のツール: $name (tools/$name.sh が無い)"
    for fn in $TOOL_FUNCS; do unset -f "$fn"; done
    TOOL_DESC=""; TOOL_DEPS=""; TOOL_SELF_REGEX=""; TOOL_SELF_BLOCK=""
    # shellcheck disable=SC1090
    . "$TOOLS_DIR/$name.sh"
    tool_self_regex() { printf '%s' "$TOOL_SELF_REGEX"; }
}

tool_field() { ( load_tool "$1"; eval "printf '%s' \"\$$2\"" ); }

# 依存を先に並べた順序(トポロジカル順)
RESOLVED=()
resolve() {
    local name="$1" stack="${2:-}" d
    case " ${RESOLVED[*]} " in *" $name "*) return 0 ;; esac
    case " $stack " in *" $name "*) die "循環依存: $stack $name" ;; esac
    tool_exists "$name" || usage_die "未登録のツール: $name"
    for d in $(tool_field "$name" TOOL_DEPS); do resolve "$d" "$stack $name"; done
    RESOLVED+=("$name")
}

# 更新不要か。tool_uptodate があればそれ、無ければ導入版と最新版の一致
is_uptodate() {   # $1=tool $2=導入版 $3=最新版
    ( load_tool "$1"; declare -F tool_uptodate >/dev/null || exit 2; tool_uptodate >/dev/null 2>&1 ); local r=$?
    [ $r -ne 2 ] && return $r
    [ -n "$3" ] && [ "${2#v}" = "${3#v}" ]
}
installed_version() { ( load_tool "$1"; tool_version 2>/dev/null | head -n1 ); }
# remove の対象になる版。tool_owned があればそれ(PATH 上の管理外の導入物を据え置くツール用)
owned_version()     { ( load_tool "$1"; if declare -F tool_owned >/dev/null; then tool_owned; else tool_version; fi 2>/dev/null | head -n1 ); }
latest_version()    { ( load_tool "$1"; tool_latest 2>/dev/null | head -n1 ); }
# 未導入でも残る設定(1 行 1 件。無ければ空)
residue_of()        { ( load_tool "$1"; declare -F tool_residue >/dev/null || exit 0; tool_residue 2>/dev/null ) | paste -sd, -; }
# dry-run の計画中は「導入予定」も導入済みとみなす
effective_version() { local v; v="$(installed_version "$1")"; [ -n "$v" ] || v="$(planned_version "$1")"; printf '%s' "$v"; }

# ---------------------------------------------------------------- サブコマンド
cmd_list() {
    local t
    printf '%-14s %-24s %s\n' TOOL DEPS DESCRIPTION
    for t in $(all_tools); do
        printf '%-14s %-24s %s\n' "$t" "$(tool_field "$t" TOOL_DEPS)" "$(tool_field "$t" TOOL_DESC)"
    done
}

cmd_status() {
    local t inst lat st rc=0
    [ $# -eq 0 ] && set -- $(all_tools)
    printf '%-14s %-14s %-14s %s\n' TOOL INSTALLED LATEST STATE
    for t in "$@"; do
        tool_exists "$t" || usage_die "未登録のツール: $t"
        inst="$(installed_version "$t")"; lat="$(latest_version "$t")"
        if [ -z "$inst" ]; then st=missing; rc=1
        elif [ -z "$lat" ]; then st="ok(latest unknown)"
        elif is_uptodate "$t" "$inst" "$lat"; then st=ok
        else st=outdated
        fi
        printf '%-14s %-14s %-14s %s\n' "$t" "${inst:--}" "${lat:--}" "$st"
    done
    return $rc
}

# 前提チェックをまとめて先に行い、sudo 依頼があれば何も変更せず止める
prereq_all() {
    local t ok=0
    for t in "$@"; do
        ( SUDO_REQUESTS=(); load_tool "$t"; declare -F tool_prereq >/dev/null || exit 0
          tool_prereq; r=$?
          [ "${#SUDO_REQUESTS[@]}" -gt 0 ] && printf '%s\n' "${SUDO_REQUESTS[@]}" > "$SUDO_TMP"
          exit $r ) || ok=1
        [ -s "$SUDO_TMP" ] && mapfile -t -O "${#SUDO_REQUESTS[@]}" SUDO_REQUESTS < "$SUDO_TMP"
        : > "$SUDO_TMP"
    done
    if [ "${#SUDO_REQUESTS[@]}" -gt 0 ]; then
        flush_sudo_requests; exit 3
    fi
    [ $ok -eq 0 ] || die "前提チェックに失敗"
}

POSTS=()
run_sync() {   # 版に関係ない設定の同期(導入済み・最新でも毎回)
    local t="$1"
    ( load_tool "$t"; declare -F tool_sync >/dev/null || exit 0; tool_sync ) || die "$t の設定同期に失敗"
}

apply_one() {   # $1=install|update $2=tool
    local mode="$1" t="$2" inst acted=0
    inst="$(installed_version "$t")"
    if [ "$mode" = install ] && [ -n "$inst" ]; then
        log "$t: 導入済み ($inst) — 版は変えない"
    elif [ "$mode" = update ] && [ -n "$inst" ]; then
        local lat; lat="$(latest_version "$t")"
        if is_uptodate "$t" "$inst" "$lat"; then
            log "$t: 最新(指定どおり) ($inst) — 版は変えない"
        else
            log "$t: 更新 $inst -> ${lat:-latest}"
            ( load_tool "$t"; if declare -F tool_update >/dev/null; then tool_update; else tool_install; fi ) \
                || die "$t の更新に失敗"
            acted=1; PLANNED[$t]="${lat:-latest}"
        fi
    else
        log "$t: 導入"
        ( load_tool "$t"; tool_install ) || die "$t の導入に失敗"
        acted=1; PLANNED[$t]="$(latest_version "$t")"; [ -n "${PLANNED[$t]}" ] || PLANNED[$t]=latest
    fi
    # 依存の導入予定を子プロセス(ツール関数)にも見せる
    export PLANNED_NODE="${PLANNED[node]:-}"
    run_sync "$t"
    if [ $acted = 1 ]; then
        local p; p="$( load_tool "$t"; declare -F tool_post >/dev/null && tool_post )"
        [ -n "$p" ] && POSTS+=("[$t] $p")
    fi
    if [ "$DRY_RUN" = 0 ]; then
        inst="$(installed_version "$t")"
        [ -n "$inst" ] || die "$t: 実行後も導入版が取れない(導入失敗)"
        [ $acted = 1 ] && log "$t: $inst"
    fi
    return 0
}

cmd_apply() {
    local mode="$1"; shift
    [ $# -eq 0 ] && usage_die "ツール名を指定してください(list で一覧)"
    local t; RESOLVED=()
    for t in "$@"; do resolve "$t"; done
    log "対象(依存順): ${RESOLVED[*]}"
    # update で版を入れ替える指定ツールに実行中エージェントの土台が含まれていないか
    if [ "$mode" = update ]; then
        local upd=()
        for t in "$@"; do [ -n "$(installed_version "$t")" ] && upd+=("$t"); done
        [ "${#upd[@]}" -gt 0 ] && block_self update "${upd[@]}"
    fi
    # 前提チェックは実際に手を入れるツールだけ(導入済みの依存は見ない)
    local acting=()
    for t in "${RESOLVED[@]}"; do
        if [ -z "$(installed_version "$t")" ]; then acting+=("$t")
        elif [ "$mode" = update ]; then case " $* " in *" $t "*) acting+=("$t") ;; esac
        fi
    done
    [ "${#acting[@]}" -gt 0 ] && prereq_all "${acting[@]}"
    for t in "${RESOLVED[@]}"; do
        # 依存として引き込んだものは install 扱い(勝手に更新しない)
        case " $* " in *" $t "*) apply_one "$mode" "$t" ;; *) apply_one install "$t" ;; esac
    done
    if [ "${#POSTS[@]}" -gt 0 ]; then
        echo "NEXT_STEPS:"; printf '  %s\n' "${POSTS[@]}"
    fi
}

cmd_remove() {
    [ $# -eq 0 ] && usage_die "ツール名を指定してください"
    local t u d blockers=()
    for t in "$@"; do tool_exists "$t" || usage_die "未登録のツール: $t"; done
    # 依存元(削除対象外で導入済み、または未導入でも設定が残っている)が残るなら拒否
    for u in $(all_tools); do
        case " $* " in *" $u "*) continue ;; esac
        for d in $(tool_field "$u" TOOL_DEPS); do
            case " $* " in *" $d "*)
                if [ -n "$(installed_version "$u")" ]; then blockers+=("$u -> $d")
                elif [ -n "$(residue_of "$u")" ]; then blockers+=("$u(未導入・残存設定: $(residue_of "$u")。先に remove $u) -> $d")
                fi ;;
            esac
        done
    done
    [ "${#blockers[@]}" -gt 0 ] && die "依存元が導入済み(または設定が残っている)ため削除しない: ${blockers[*]}"
    block_self remove "$@"
    if [ "$ASSUME_YES" != 1 ] && [ "$DRY_RUN" != 1 ]; then
        echo "CONFIRM_REQUIRED: 削除対象 = $* (ユーザー確認後に --yes を付けて再実行)"
        exit 4
    fi
    # 依存元から先に消す(依存の逆順)
    RESOLVED=(); for t in "$@"; do resolve "$t"; done
    local i
    for (( i=${#RESOLVED[@]}-1; i>=0; i-- )); do
        t="${RESOLVED[$i]}"
        case " $* " in *" $t "*) ;; *) continue ;; esac
        if [ -n "$(owned_version "$t")" ]; then
            log "$t: 削除"
            ( load_tool "$t"; tool_remove ) || die "$t の削除に失敗"
        elif [ -n "$(residue_of "$t")" ]; then
            # 実体は無いが設定だけ残っている(管理ブロック・別名など)→ 後始末だけ行う
            log "$t: 未導入だが設定が残っている($(residue_of "$t"))— 後始末"
            ( load_tool "$t"; declare -F tool_cleanup >/dev/null || exit 1; tool_cleanup ) \
                || die "$t の残存設定を消せない(tool_cleanup が無いか失敗)"
        elif [ -n "$(installed_version "$t")" ]; then
            log "$t: 管理外の導入物 ($(installed_version "$t")) は据え置き — 何もしない"; continue
        else
            log "$t: 未導入 — 何もしない"; continue
        fi
        if [ "$DRY_RUN" = 0 ]; then
            [ -z "$(owned_version "$t")" ] || die "$t: 削除後も導入版が残っている($(owned_version "$t"))"
            [ -z "$(residue_of "$t")" ] || die "$t: 削除後も設定が残っている($(residue_of "$t"))"
        fi
    done
}

cmd_lint() {
    local t fn bad=0
    for t in $(all_tools); do
        for fn in tool_version tool_latest tool_install tool_remove; do
            ( load_tool "$t"; declare -F "$fn" >/dev/null ) || { echo "NG $t: $fn が未定義"; bad=1; }
        done
        [ -n "$(tool_field "$t" TOOL_DESC)" ] || { echo "NG $t: TOOL_DESC が空"; bad=1; }
        for d in $(tool_field "$t" TOOL_DEPS); do
            tool_exists "$d" || { echo "NG $t: 依存 $d が未登録"; bad=1; }
        done
        # sudo の直書き禁止(need_sudo 経由のみ)。コメント行は除く
        if grep -nE '^[^#]*(^|[;&|(` ])sudo([[:space:]]|$)' "$TOOLS_DIR/$t.sh" | grep -v 'need_sudo' >/dev/null; then
            echo "NG $t: sudo を直接呼んでいる(need_sudo を使う)"; bad=1
        fi
        bash -n "$TOOLS_DIR/$t.sh" || { echo "NG $t: 構文エラー"; bad=1; }
    done
    RESOLVED=(); for t in $(all_tools); do resolve "$t"; done
    [ $bad -eq 0 ] && echo "lint OK: $(all_tools | wc -l) tools"
    return $bad
}

# ---------------------------------------------------------------- main
ARGS=()
for a in "$@"; do
    case "$a" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help) sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) usage_die "不明なオプション: $a" ;;
        *) ARGS+=("$a") ;;
    esac
done
set -- "${ARGS[@]}"
export DRY_RUN
SUDO_TMP="$(mktemp)"; trap 'rm -f "$SUDO_TMP"' EXIT

sub="${1:-}"; shift || true
case "$sub" in
    list)    cmd_list ;;
    status)  cmd_status "$@" ;;
    install) cmd_apply install "$@" ;;
    update)  cmd_apply update "$@" ;;
    remove)  cmd_remove "$@" ;;
    lint)    cmd_lint ;;
    *) echo "usage: $(basename "$0") {list|status|install|update|remove|lint} [tool...] [--dry-run] [--yes]" >&2; exit 2 ;;
esac
