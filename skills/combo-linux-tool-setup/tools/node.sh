# node — nvm 管理の Node.js LTS。tcsh 用に ~/.cshrc の PATH を管理ブロックで張る。
# 実績手順: nvm install --lts && nvm use --lts
#           .cshrc に setenv PATH "$HOME/.nvm/versions/node/<ver>/bin:${PATH}"
TOOL_DESC="Node.js LTS (nvm 管理・default は具体版に固定)。~/.cshrc の PATH を管理ブロックで更新"
TOOL_DEPS="nvm"
# 実行中エージェント(npm 版 claude / codex)は node の版ディレクトリ配下で動く。
# update は旧版を残すので安全、remove だけ止める。
TOOL_SELF_REGEX="$NVM_DIR/versions/node/"
TOOL_SELF_BLOCK="remove"

CSHRC="${CSHRC:-$HOME/.cshrc}"
# 手作業で足された旧行(バージョン直書き)を管理ブロックへ吸収するための正規表現
CSH_LEGACY='^[[:space:]]*setenv[[:space:]]+PATH[[:space:]]+"\$HOME/\.nvm/versions/node/v[0-9.]+/bin:\$\{PATH\}"[[:space:]]*$'

# nvm.sh は読むたびに default を use し直すので、同じシェルでは 1 回だけ読む
_nvm() { declare -F nvm >/dev/null || . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || return 1; nvm "$@"; }

# 導入済みの版(新しい順)
_installed_all() {
    [ -d "$NVM_DIR/versions/node" ] || return 0
    ls -1 "$NVM_DIR/versions/node" 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -rV
}

# default は必ず具体版(<ver>)に固定する。'lts/*' のような動く別名だと、lts 別名が
# 進んだ時点で default が未導入版を指して N/A になり、導入済みなのに未導入と誤判定する。
tool_version() {
    # nvm 本体(nvm.sh)が失われていても版ディレクトリが残っていれば導入済みとして返す
    # (未導入と誤判定して remove が実体を残したまま成功するのを防ぐ)
    [ -s "$NVM_DIR/nvm.sh" ] || { _installed_all | head -n1; return 0; }
    local v; v="$( _nvm version default 2>/dev/null )"
    # default が壊れている(N/A)ときは導入済みの最新版を導入版とみなす(tool_sync で修復)
    { [ -z "$v" ] || [ "$v" = "N/A" ]; } && v="$(_installed_all | head -n1)"
    [ -n "$v" ] && echo "$v"
}

# 最新 LTS は nodejs.org の公開インデックスから直接取る。
# nvm version-remote --lts は ~/.nvm/alias/lts/* を書き換えるので使わない(status/dry-run を無変更に保つ)。
tool_latest() {
    curl -fsSL --max-time 20 https://nodejs.org/dist/index.json 2>/dev/null \
        | tr '\n' ' ' | sed 's/}, *{/}\n{/g' \
        | grep -v '"lts":false' | grep -o '"version":"v[0-9.]*"' | head -n1 | cut -d'"' -f4
}

tool_prereq() { require_cmd curl curl "Node.js の版情報取得に curl が要る"; }

_csh_line() {   # $1=版
    # 既定の ~/.nvm なら実績どおり $HOME 表記、NVM_DIR を変えている場合は絶対パス
    local base="$NVM_DIR"; [ "$NVM_DIR" = "$HOME/.nvm" ] && base='$HOME/.nvm'
    printf 'setenv PATH "%s/versions/node/%s/bin:${PATH}"' "$base" "$1"
}

# 版に関係ない設定の同期(導入済み・最新でも毎回呼ばれる)
#   1. default 別名を具体版に固定する(lts/* 等なら現在の導入版へ)
#   2. ~/.cshrc の管理ブロックを default の版へ(手書きの旧行はその位置でブロックへ置換)
tool_sync() {
    local v alias_file="$NVM_DIR/alias/default" cur=""
    v="$(tool_version)"
    [ "$DRY_RUN" = 1 ] && [ -n "${PLANNED_NODE:-}" ] && v="$PLANNED_NODE"
    [ -n "$v" ] || { echo "node の導入版が無い" >&2; return 1; }
    [ -f "$alias_file" ] && cur="$(cat "$alias_file")"
    if [ "$cur" != "$v" ]; then
        run "_nvm alias default '$v' >/dev/null" || return 1
    fi
    rc_block "$CSHRC" node "$(_csh_line "$v")" "$CSH_LEGACY"
}

tool_install() {
    local v; v="$(tool_latest)"
    [ -n "$v" ] || { echo "最新 LTS が取れない" >&2; return 1; }
    # default 別名と ~/.cshrc は直後に driver が呼ぶ tool_sync で合わせる
    run "_nvm install '$v'"
}

# 新 LTS を入れ、旧版のグローバル npm パッケージ(claude-code / codex 等)を引き継ぐ。
# 旧版は消さない(実行中のエージェントがその node で動いているため)。
tool_update() {
    local old v; old="$(tool_version)"; v="$(tool_latest)"
    [ -n "$v" ] || { echo "最新 LTS が取れない" >&2; return 1; }
    run "_nvm install '$v' ${old:+--reinstall-packages-from=$old}" || return 1
    # 新版を default に固定(既存の default を tool_sync の「導入版」判定より先に動かす)
    run "_nvm alias default '$v' >/dev/null" || return 1
    [ -n "$old" ] && echo "旧 node $old は残しています。エージェント再起動後に不要なら: bash -c '. \$NVM_DIR/nvm.sh && nvm uninstall $old'" >&2
    return 0
}

# remove = nvm 管理下の node を全版削除し、default 別名と ~/.cshrc の管理ブロックを外す
tool_remove() {
    local v
    if [ ! -s "$NVM_DIR/nvm.sh" ]; then
        echo "nvm 本体($NVM_DIR/nvm.sh)が無いため node($(_installed_all | paste -sd' ' -))を nvm で削除できない。" >&2
        echo "復旧: 'toolsetup.sh install nvm' で nvm を入れ直してから remove node を再実行する(または $NVM_DIR/versions/node を手で整理する)" >&2
        return 1
    fi
    run "_nvm deactivate >/dev/null" || return 1
    for v in $(_installed_all); do
        run "_nvm uninstall '$v'" || return 1
    done
    tool_cleanup
}

# node の実体が無くても残り得る設定(remove の判定と後始末に使う)
tool_residue() {
    if [ -e "$CSHRC" ] && grep -qxF -e "# >>> $MARK node >>>" -e "# <<< $MARK node <<<" "$CSHRC"; then
        echo "$CSHRC の管理ブロック"
    fi
    # 手書きの旧行は管理ブロックの外にあるものだけ数える(ブロック内の行も同じ形のため)
    if [ -e "$CSHRC" ] && B="# >>> $MARK node >>>" E="# <<< $MARK node <<<" L="$CSH_LEGACY" awk '
            $0 == ENVIRON["B"] { inb=1; next }
            $0 == ENVIRON["E"] { inb=0; next }
            !inb && $0 ~ ENVIRON["L"] { f=1 }
            END { exit !f }' "$CSHRC"; then
        echo "$CSHRC の手書き PATH 行"
    fi
    [ -e "$NVM_DIR/alias/default" ] && echo "nvm の default 別名"
    return 0
}

# default 別名と ~/.cshrc の管理ブロック(手書きの旧行も)を外す。冪等
tool_cleanup() {
    if [ -e "$NVM_DIR/alias/default" ]; then
        run "rm -f '$NVM_DIR/alias/default'" || return 1
    fi
    rc_block "$CSHRC" node "" "$CSH_LEGACY"
}

tool_post() { echo "tcsh では新しいシェルを開くか 'source ~/.cshrc' で PATH を反映"; }
