# slang — SystemVerilog コンパイラ/リンタ(GitHub リリースのビルド済みバイナリ)。
# 実績手順:
#   mkdir -p ~/.local/opt/slang ~/.local/bin
#   curl -fsSL -o /tmp/slang.tar.gz \
#     https://github.com/MikePopoloski/slang/releases/download/v11.0/slang-linux-x86_64.tar.gz
#   # このリリースの tar はトップ階層なしで実行ファイル slang を直接含むため strip しない
#   tar -xzf /tmp/slang.tar.gz -C ~/.local/opt/slang
#   chmod +x ~/.local/opt/slang/slang
#   ln -sf ~/.local/opt/slang/slang ~/.local/bin/slang
#   slang --version   # => slang version 11.0.0+...
#
# 版は既定で v11.0 に固定(SLANG_DEFAULT_VERSION)。別の版へ上げるのは任意で、SLANG_VERSION=<タグ>(例: 12.1)か
# SLANG_VERSION=latest(GitHub の最新リリース)を渡したときだけ。既定より新しい版が入っていても既定の update は 11.0 へ戻す。
# 導入先は SLANG_HOME(既定 ~/.local/opt/slang)、リンク置き場は LOCAL_BIN(既定 ~/.local/bin)。
# 管理するのは $SLANG_HOME/slang とそれを指す $LOCAL_BIN/slang だけ(PATH 上の別の slang には触れない)。
TOOL_DESC="slang SystemVerilog compiler (GitHub release binary, default v11.0 / SLANG_VERSION=<tag>|latest -> ~/.local/opt/slang, link ~/.local/bin/slang)"
SLANG_DEFAULT_VERSION="11.0"
TOOL_DEPS=""

SLANG_HOME="${SLANG_HOME:-$HOME/.local/opt/slang}"
LOCAL_BIN="${LOCAL_BIN:-$HOME/.local/bin}"
SLANG_HOME="${SLANG_HOME%/}"; LOCAL_BIN="${LOCAL_BIN%/}"
SLANG_REPO="https://github.com/MikePopoloski/slang"
SLANG_ASSET="slang-linux-x86_64.tar.gz"

_q() { printf '%q' "$1"; }   # run は文字列を eval するので、埋め込む値は必ずシェル用にエスケープする

# 導入先・リンク置き場の検査(run に渡す前に必ず通す)。絶対パスで、英数字と . _ / + @ , = - のみ
_slang_paths_ok() {
    local p r=0
    for p in "$SLANG_HOME" "$LOCAL_BIN"; do
        [[ "$p" =~ ^/[A-Za-z0-9._/+@,=-]*$ ]] \
            || { echo "SLANG_HOME / LOCAL_BIN は絶対パスで、使えるのは英数字と . _ / + @ , = - だけ: $p" >&2; r=1; }
    done
    # 同じ場所だと本体をリンクで上書きして実体を失う(シンボリックリンクや .. 経由も実体パスで比べる)
    if [ $r = 0 ] && [ "$(realpath -m -- "$SLANG_HOME")" = "$(realpath -m -- "$LOCAL_BIN")" ]; then
        echo "SLANG_HOME と LOCAL_BIN が同じ場所を指している: $SLANG_HOME / $LOCAL_BIN" >&2; r=1
    fi
    return $r
}

_slang_ver() {   # $1=実行ファイル → "11.0.0"(動かなければ空)
    [ -x "$1" ] || return 0
    "$1" --version 2>/dev/null | sed -n 's/^slang version \([0-9][0-9.]*\).*/\1/p' | head -n1
}
_slang_norm() {  # タグ "v11.0" → "11.0.0"
    local v="${1#v}"
    case "$v" in *.*.*) ;; *.*) v="$v.0" ;; *) v="$v.0.0" ;; esac
    printf '%s' "$v"
}
# 導入するタグ(SLANG_VERSION 指定があればそれ、無ければ既定の固定版)。latest は API を使わずリダイレクト先から取る
_slang_tag() {
    local v="${SLANG_VERSION:-$SLANG_DEFAULT_VERSION}"
    if [ "$v" != latest ]; then
        [[ "$v" =~ ^v?[0-9]+(\.[0-9]+){0,2}$ ]] || return 0
        case "$v" in v*) echo "$v" ;; *) echo "v$v" ;; esac
        return 0
    fi
    curl -sI -o /dev/null -w '%{redirect_url}' --max-time 15 "$SLANG_REPO/releases/latest" 2>/dev/null \
        | sed -n 's#.*/releases/tag/\(v[0-9][0-9.]*\)$#\1#p'
}

tool_version() { _slang_ver "$SLANG_HOME/slang"; return 0; }
# remove の対象。起動できなくても実体か差し替え途中の残骸(slang.new / slang.old)があれば "broken"(消せるように)
tool_owned() {
    local v f; v="$(_slang_ver "$SLANG_HOME/slang")"
    if [ -n "$v" ]; then echo "$v"; return 0; fi
    for f in slang slang.new slang.old; do
        if [ -e "$SLANG_HOME/$f" ] || [ -L "$SLANG_HOME/$f" ]; then echo broken; return 0; fi
    done
    return 0
}
tool_latest()  { local t; t="$(_slang_tag)"; [ -n "$t" ] && _slang_norm "$t"; return 0; }

tool_prereq() {
    local r=0
    _slang_paths_ok || r=1
    case "$(uname -m)" in x86_64) ;; *) echo "slang のビルド済みバイナリは x86_64 のみ($(uname -m))" >&2; r=1 ;; esac
    require_cmd curl curl "slang の取得に curl が要る" || r=1
    require_cmd tar  tar  "slang の展開に tar が要る" || r=1
    if [ -n "${SLANG_VERSION:-}" ] && [ "$SLANG_VERSION" != latest ] && ! [[ "$SLANG_VERSION" =~ ^v?[0-9]+(\.[0-9]+){0,2}$ ]]; then
        echo "SLANG_VERSION はリリースタグ(例: 12.1 / v12.1)か latest で指定する: $SLANG_VERSION" >&2; r=1
    fi
    return $r
}

# リンク $LOCAL_BIN/slang が管理下(=$SLANG_HOME/slang を指すリンク)か、まだ無いか
_slang_link_free() {
    local l="$LOCAL_BIN/slang"
    [ ! -e "$l" ] && [ ! -L "$l" ] && return 0
    [ -L "$l" ] && [ "$(readlink "$l")" = "$SLANG_HOME/slang" ]
}

# 差し替え。旧版は slang.old に退避し、本体の置き換えかリンクの作成が失敗したら旧版へ戻す
_slang_put() {   # $1=検証済みの新しい実行ファイル
    local H L new old cur
    H="$(_q "$SLANG_HOME")"; L="$(_q "$LOCAL_BIN")"
    new="$(_q "$SLANG_HOME/slang.new")"; old="$(_q "$SLANG_HOME/slang.old")"; cur="$(_q "$SLANG_HOME/slang")"
    run "mkdir -p $H $L" || return 1
    if [ "$DRY_RUN" != 1 ] && { [ ! -w "$SLANG_HOME" ] || [ ! -w "$LOCAL_BIN" ]; }; then
        echo "$SLANG_HOME か $LOCAL_BIN に書き込めない(何も変えていない)" >&2; return 1
    fi
    run "mv -f $(_q "$1") $new" || return 1
    local had=0
    if [ -e "$SLANG_HOME/slang" ] || [ -L "$SLANG_HOME/slang" ]; then
        had=1; run "mv -f $cur $old" || { run "rm -f $new"; return 1; }
    fi
    if run "mv -f $new $cur" && run "ln -sfn $cur $L/slang"; then
        [ $had = 1 ] && run "rm -f $old"
        return 0
    fi
    echo "slang の差し替えに失敗。旧版へ戻す" >&2
    run "rm -f $new"
    if [ $had = 1 ]; then run "mv -f $old $cur"; else run "rm -f $cur"; fi
    return 1
}

# ダウンロード → 作業ディレクトリへ展開 → 実行ファイルを確認してから差し替え(失敗時は旧版が残る)
tool_install() {
    _slang_paths_ok || return 1
    local tag url tmp r
    tag="$(_slang_tag)"
    [ -n "$tag" ] || { echo "slang のリリースタグを決められない(最新の取得失敗か SLANG_VERSION が不正)" >&2; return 1; }
    url="$SLANG_REPO/releases/download/$tag/$SLANG_ASSET"
    _slang_link_free || { echo "$LOCAL_BIN/slang が管理外のファイル/リンク。退避してから再実行してください" >&2; return 1; }
    if [ "$DRY_RUN" = 1 ]; then tmp="/tmp/tmp.XXXXXXXXXX"; else tmp="$(mktemp -d)" || return 1; fi
    local T; T="$(_q "$tmp")"
    # リリースの tar はトップ階層なしで slang を直接含む。万一階層があっても拾えるよう作業ディレクトリへ展開
    if run "curl -fsSL -o $T/slang.tar.gz $(_q "$url")" && run "mkdir -p $T/x && tar -xzf $T/slang.tar.gz -C $T/x"; then
        local src="$tmp/x/slang"
        if [ "$DRY_RUN" != 1 ]; then
            [ -f "$src" ] || src="$(find "$tmp/x" -type f -name slang | head -n1)"
            if [ -z "$src" ] || [ ! -f "$src" ]; then echo "展開物に slang が無い($url)" >&2; src=""
            else
                chmod +x "$src"
                [ -n "$(_slang_ver "$src")" ] || { echo "展開した slang が起動しない($url)" >&2; src=""; }
            fi
        fi
        if [ -n "$src" ]; then _slang_put "$src"; r=$?; else r=1; fi
    else
        r=1
    fi
    [ "$DRY_RUN" = 1 ] || rm -rf "$tmp"
    return $r
}

# リンクの張り直し(冪等)。管理外のファイル/リンクがあれば上書きせず失敗
tool_sync() {
    [ -x "$SLANG_HOME/slang" ] || [ "$DRY_RUN" = 1 ] || return 0
    _slang_paths_ok || return 1
    _slang_link_free || { echo "$LOCAL_BIN/slang が管理外のファイル/リンク。退避してから再実行してください" >&2; return 1; }
    [ -L "$LOCAL_BIN/slang" ] || run "mkdir -p $(_q "$LOCAL_BIN") && ln -sfn $(_q "$SLANG_HOME/slang") $(_q "$LOCAL_BIN/slang")"
}

tool_remove() {
    _slang_paths_ok || return 1
    run "rm -f $(_q "$SLANG_HOME/slang") $(_q "$SLANG_HOME/slang.new") $(_q "$SLANG_HOME/slang.old")" || return 1
    tool_cleanup
}

# 実体が無くても残るもの: $SLANG_HOME/slang を指すリンク
tool_residue() {
    [ -L "$LOCAL_BIN/slang" ] && [ "$(readlink "$LOCAL_BIN/slang")" = "$SLANG_HOME/slang" ] \
        && echo "$LOCAL_BIN/slang (リンク)"
    return 0
}
tool_cleanup() {
    _slang_paths_ok || return 1
    if [ -L "$LOCAL_BIN/slang" ] && [ "$(readlink "$LOCAL_BIN/slang")" = "$SLANG_HOME/slang" ]; then
        run "rm -f $(_q "$LOCAL_BIN/slang")" || return 1
    fi
    [ -d "$SLANG_HOME" ] && run "rmdir $(_q "$SLANG_HOME") 2>/dev/null || true"
    return 0
}

tool_post() {
    case ":$PATH:" in *":$LOCAL_BIN:"*) ;; *) echo "$LOCAL_BIN が PATH に無い。シェルの PATH に追加してください" ;; esac
}
