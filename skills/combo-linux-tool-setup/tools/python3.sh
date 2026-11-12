# python3 — pyenv の python-build でソースからビルドした Python。tcsh 用に ~/.cshrc の PATH を管理ブロックで張る。
# 実績手順:
#   cd <path>
#   git clone https://github.com/pyenv/pyenv
#   env PYTHON_CONFIGURE_OPTS="--enable-shared --enable-optimizations --with-lto" \
#     pyenv/plugins/python-build/bin/python-build <ver> <path>/python-<ver>
#   .cshrc に setenv PATH "<path>/python-<ver>/bin:${PATH}"
#
# 導入先と版はユーザーが指定する(環境変数で渡す):
#   PYTHON_PREFIX=<path>(絶対パス)  PYTHON_VERSION=<ver>(python-build --definitions にある版)
# 既に PATH(または ~/.cshrc の管理ブロック・手書きの行)に python3 があれば install は据え置く。
# 別の版へ入れ替えたいときだけ update を使う(旧版は消さない)。
TOOL_DESC="Python 3 (pyenv python-build でビルド。導入先/版は PYTHON_PREFIX/PYTHON_VERSION で指定)。~/.cshrc の PATH を管理ブロックで更新"
TOOL_DEPS=""

CSHRC="${CSHRC:-$HOME/.cshrc}"
PY_CONF_OPTS="--enable-shared --enable-optimizations --with-lto"
PYENV_URL="https://github.com/pyenv/pyenv"
# 手作業で足された行(<path>/python-<ver>/bin を PATH の先頭へ)。コメント行は対象外
CSH_LEGACY='^[[:space:]]*setenv[[:space:]]+PATH[[:space:]]+"[^"$]*/python-[0-9][^"/]*/bin:\$\{PATH\}"[[:space:]]*$'

_py_ver() {   # $1=python3 実行ファイル → 版(実行できなければ空)
    [ -x "$1" ] || return 0
    "$1" -c 'import platform; print(platform.python_version())' 2>/dev/null
}

# 管理ブロックが指す bin(無ければ空)
_py_block_bin() {
    [ -e "$CSHRC" ] || return 0
    B="# >>> $MARK python3 >>>" E="# <<< $MARK python3 <<<" awk '
        $0 == ENVIRON["B"] { inb=1; next }
        $0 == ENVIRON["E"] { inb=0; next }
        inb && /^[[:space:]]*setenv[[:space:]]+PATH[[:space:]]+"/ { s=$0; sub(/^[^"]*"/, "", s); sub(/:\$\{PATH\}".*$/, "", s); print s; exit }' "$CSHRC"
}

# 管理ブロック外の手書きの行が指す bin(最後の有効行。無ければ空)
_py_legacy_bin() {
    [ -e "$CSHRC" ] || return 0
    B="# >>> $MARK python3 >>>" E="# <<< $MARK python3 <<<" L="$CSH_LEGACY" awk '
        $0 == ENVIRON["B"] { inb=1; next }
        $0 == ENVIRON["E"] { inb=0; next }
        !inb && $0 ~ ENVIRON["L"] { s=$0; sub(/^[^"]*"/, "", s); sub(/:\$\{PATH\}".*$/, "", s); last=s }
        END { if (last != "") print last }' "$CSHRC"
}

# このスキルが入れて管理している版(管理ブロックの python3。remove の対象はこれだけ)
# python3 が起動できなくなっていても、導入ディレクトリが残っていれば "broken" を返す(remove で消せるように)
tool_owned() {
    local b v; b="$(_py_block_bin)"
    [ -n "$b" ] || return 0
    v="$(_py_ver "$b/python3")"
    if [ -n "$v" ]; then echo "$v"; elif [ -d "${b%/bin}" ]; then echo "broken"; fi
    return 0
}

# 使える python3 の版。管理ブロック → 手書きの行 → PATH の順(どれかあれば据え置き対象)
_py_active_bin() {   # tool_version が見ている python3 の bin
    local b
    for b in "$(_py_block_bin)" "$(_py_legacy_bin)"; do
        [ -n "$b" ] && [ -n "$(_py_ver "$b/python3")" ] && { echo "$b"; return 0; }
    done
    command -v python3 >/dev/null 2>&1 && dirname "$(command -v python3)"
    return 0
}
tool_version() {
    local b; b="$(_py_active_bin)"
    [ -n "$b" ] && _py_ver "$b/python3"
    return 0
}

# 指定どおりか(版だけでなく導入先も見る)。同じ版でも別の PYTHON_PREFIX を指定されたら更新対象
tool_uptodate() {
    local b v; b="$(_py_active_bin)"; v="$(tool_version)"
    [ -n "$v" ] && [ -n "${PYTHON_VERSION:-}" ] && [ "$v" = "$PYTHON_VERSION" ] || return 1
    [ -z "${PYTHON_PREFIX:-}" ] || [ "$b" = "${PYTHON_PREFIX%/}/python-$PYTHON_VERSION/bin" ]
}

# 最新版は追わない。ユーザーが指定した版(PYTHON_VERSION)を目標版とする
tool_latest() { [ -n "${PYTHON_VERSION:-}" ] && echo "$PYTHON_VERSION"; return 0; }

# 導入先と版の指定を検査する(無ければ何も変えずに止める)
_py_params() {
    local r=0
    if [ -z "${PYTHON_PREFIX:-}" ] || [ -z "${PYTHON_VERSION:-}" ]; then
        echo "python3 の導入先と版を指定してください: PYTHON_PREFIX=<絶対パス> PYTHON_VERSION=<ver> toolsetup.sh install python3" >&2
        return 1
    fi
    case "$PYTHON_PREFIX" in /*) ;; *) echo "PYTHON_PREFIX は絶対パスで指定する: $PYTHON_PREFIX" >&2; r=1 ;; esac
    # .cshrc の二重引用符内に書くので、tcsh が展開する文字(` $ ! ~ 等)や空白・引用符は通さない(許可リスト)
    # grep は行単位で改行を素通しするので、文字列全体を [[ =~ ]] で検査する
    [[ "$PYTHON_PREFIX" =~ ^/[A-Za-z0-9._/+@,=-]*$ ]] \
        || { echo "PYTHON_PREFIX に使えるのは英数字と . _ / + @ , = - だけ: $PYTHON_PREFIX" >&2; r=1; }
    [[ "$PYTHON_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]] \
        || { echo "PYTHON_VERSION は 3.12.4(または 3.14.0rc1 等)のような具体版で指定する: $PYTHON_VERSION" >&2; r=1; }
    return $r
}

# 版が実在するか(python.org のソース tarball の有無)を、何も変えないうちに確かめる(dry-run でも行う)
_py_ver_exists() {
    local ver="$PYTHON_VERSION" base="${PYTHON_VERSION%%[abr]*}" code
    code="$(curl -sIL -o /dev/null -w '%{http_code}' --max-time 20 \
        "https://www.python.org/ftp/python/$base/Python-$ver.tar.xz" 2>/dev/null)"
    case "$code" in
        200) return 0 ;;
        404) echo "python.org に Python $ver のソースが無い(版を確認してください)" >&2; return 1 ;;
        *)   echo "python.org に接続できず Python $ver の実在を確認できない(HTTP ${code:-なし})" >&2; return 1 ;;
    esac
}

_has_header() { printf '#include <%s>\n' "$1" | gcc -E -x c - >/dev/null 2>&1; }

tool_prereq() {
    local r=0 miss=() pkgs=()
    if _py_params; then _py_ver_exists || r=1; else r=1; fi
    require_cmd curl curl "版の確認に curl が要る" || r=1
    require_cmd git  git  "pyenv の取得に git が要る" || r=1
    require_cmd make make "Python のビルドに make が要る" || r=1
    if ! require_cmd gcc gcc "Python のビルドに C コンパイラが要る"; then
        return 1   # ヘッダ検査は gcc が入ってから
    fi
    # 拡張モジュール(ssl/ctypes/zlib/bz2/readline/sqlite3/lzma/curses/uuid)のヘッダ
    local h
    if command -v apt-get >/dev/null 2>&1; then
        for h in zlib.h:zlib1g-dev openssl/ssl.h:libssl-dev ffi.h:libffi-dev bzlib.h:libbz2-dev \
                 readline/readline.h:libreadline-dev sqlite3.h:libsqlite3-dev lzma.h:liblzma-dev \
                 ncurses.h:libncurses-dev uuid/uuid.h:uuid-dev; do
            _has_header "${h%%:*}" || { miss+=("${h%%:*}"); pkgs+=("${h##*:}"); }
        done
    else
        for h in zlib.h:zlib-devel openssl/ssl.h:openssl-devel ffi.h:libffi-devel bzlib.h:bzip2-devel \
                 readline/readline.h:readline-devel sqlite3.h:sqlite-devel lzma.h:xz-devel \
                 ncurses.h:ncurses-devel uuid/uuid.h:libuuid-devel; do
            _has_header "${h%%:*}" || { miss+=("${h%%:*}"); pkgs+=("${h##*:}"); }
        done
    fi
    if [ "${#pkgs[@]}" -gt 0 ]; then
        need_sudo "$(pkg_install_cmd "${pkgs[@]}")" "Python 拡張モジュールのビルドに開発ヘッダが要る(無い: ${miss[*]})"
        r=1
    fi
    return $r
}

_csh_line() { printf 'setenv PATH "%s/bin:${PATH}"' "$1"; }   # $1=<path>/python-<ver>

# pyenv を clone(既にあれば python-build の定義を最新にするため pull)→ python-build → 管理ブロック
_py_build() {
    _py_params || return 1
    local root="$PYTHON_PREFIX" ver="$PYTHON_VERSION"
    local dest="$root/python-$ver" pb="$root/pyenv/plugins/python-build/bin/python-build"
    run "mkdir -p '$root'" || return 1
    if [ -d "$root/pyenv/.git" ]; then
        run "git -C '$root/pyenv' pull --ff-only" || echo "pyenv の pull に失敗(既存の定義のまま続ける)" >&2
    elif [ -e "$root/pyenv" ]; then
        echo "$root/pyenv が git の clone ではない。退避してから再実行してください" >&2; return 1
    else
        run "git clone '$PYENV_URL' '$root/pyenv'" || return 1
    fi
    if [ "$DRY_RUN" != 1 ] && ! "$pb" --definitions | grep -qxF -- "$ver"; then
        echo "python-build に版 $ver の定義が無い('$pb --definitions' で一覧)" >&2; return 1
    fi
    if [ -n "$(_py_ver "$dest/bin/python3")" ]; then
        [ "$(_py_ver "$dest/bin/python3")" = "$ver" ] \
            || { echo "$dest に別の版($(_py_ver "$dest/bin/python3"))がある" >&2; return 1; }
        echo "$dest は導入済み。ビルドせず PATH だけ張る" >&2
    elif [ -e "$dest" ]; then
        echo "$dest が既にある(python3 が動かない)。退避してから再実行してください" >&2; return 1
    else
        run "env PYTHON_CONFIGURE_OPTS='$PY_CONF_OPTS' '$pb' '$ver' '$dest'" || return 1
    fi
    rc_block "$CSHRC" python3 "$(_csh_line "$dest")" "$CSH_LEGACY"
}

tool_install() { _py_build; }

# 指定版へ入れ替える。旧版のディレクトリは消さない(手書きの行は同じ位置で管理ブロックへ置き換わる)
tool_update() {
    local old; old="$(tool_version)"
    _py_build || return 1
    [ -n "$old" ] && echo "旧 python $old は残しています(PATH から外れただけ)。不要なら手で削除してください" >&2
    return 0
}

# remove = 管理ブロックが指す <path>/python-<ver> を消し、管理ブロックを外す。
# 管理外(手書きの行・PATH 上の python3)は据え置きなので触らない。pyenv の clone も残す。
# 管理ブロックのマーカーが正しいか(開始・終了が 1 つずつ、開始が先)。本体を消す前に確かめる
_py_block_ok() {
    local b="# >>> $MARK python3 >>>" e="# <<< $MARK python3 <<<" nb ne lb le
    nb="$(grep -cxF -- "$b" "$CSHRC")"; ne="$(grep -cxF -- "$e" "$CSHRC")"
    [ "$nb" = 1 ] && [ "$ne" = 1 ] || return 1
    lb="$(grep -nxF -- "$b" "$CSHRC" | cut -d: -f1)"; le="$(grep -nxF -- "$e" "$CSHRC" | cut -d: -f1)"
    [ "$lb" -lt "$le" ]
}

tool_remove() {
    local b dir; b="$(_py_block_bin)"; dir="${b%/bin}"
    _py_block_ok || { echo "$CSHRC の python3 管理ブロックのマーカーが不正。手で直してから再実行してください(何も消していない)" >&2; return 1; }
    case "$dir" in
        /*/python-[0-9]*) ;;
        *) echo "管理ブロックの指す先が <path>/python-<ver> の形ではない($dir)。削除しない" >&2; return 1 ;;
    esac
    [ -d "$dir" ] && { run "rm -rf '$dir'" || return 1; }
    echo "pyenv の clone($(dirname "$dir")/pyenv)は残しています。不要なら手で削除してください" >&2
    tool_cleanup
}

tool_residue() {
    if [ -e "$CSHRC" ] && grep -qxF -e "# >>> $MARK python3 >>>" -e "# <<< $MARK python3 <<<" "$CSHRC"; then
        echo "$CSHRC の管理ブロック"
    fi
    return 0
}

tool_cleanup() { rc_block "$CSHRC" python3 ""; }

tool_post() { echo "tcsh では新しいシェルを開くか 'source ~/.cshrc' で PATH を反映"; }
