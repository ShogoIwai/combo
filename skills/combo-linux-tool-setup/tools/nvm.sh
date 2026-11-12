# nvm — Node Version Manager(~/.nvm)。node の土台。
# 実績手順: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/<ver>/install.sh | bash
TOOL_DESC="Node Version Manager (\$NVM_DIR, 既定 ~/.nvm)。sudo 不要"
TOOL_DEPS=""
# nvm の update は ~/.nvm のスクリプトを入れ替えるだけで node 本体には触れない。remove だけ止める。
TOOL_SELF_REGEX="$NVM_DIR/versions/node/"
TOOL_SELF_BLOCK="remove"

tool_version() {
    [ -s "$NVM_DIR/nvm.sh" ] || return 0
    ( . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 && nvm --version 2>/dev/null | sed 's/^/v/' )
}

# 最新タグ。NVM_VERSION=v0.40.3 のように固定もできる
tool_latest() {
    [ -n "${NVM_VERSION:-}" ] && { echo "$NVM_VERSION"; return; }
    curl -fsSL --max-time 15 https://api.github.com/repos/nvm-sh/nvm/releases/latest 2>/dev/null \
        | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1
}

tool_prereq() {
    local r=0
    require_cmd curl curl "nvm の install.sh 取得に curl が要る" || r=1
    require_cmd git  git  "nvm の install.sh は git で ~/.nvm を取得する" || r=1
    return $r
}

tool_install() {
    local ver; ver="$(tool_latest)"
    [ -n "$ver" ] || { echo "nvm の最新タグが取れない(NVM_VERSION=<ver> で指定可)" >&2; return 1; }
    # install.sh は既存 ~/.nvm なら git fetch + checkout で更新になる(install/update 共通)
    # NVM_DIR を明示した場合、install.sh はディレクトリが無いと失敗する
    run "mkdir -p '$NVM_DIR'" &&
    # 既に導入済み(=更新)なら PROFILE=/dev/null でシェル初期化ファイルに追記させない。
    # install.sh は SHELL に応じて ~/.bashrc 等へ重複追記することがある(既に ~/.profile にあっても)。
    local prof=""; [ -s "$NVM_DIR/nvm.sh" ] && prof="PROFILE=/dev/null "
    run "curl -fsSL -o- 'https://raw.githubusercontent.com/nvm-sh/nvm/$ver/install.sh' | ${prof}bash"
}

tool_remove() {
    run "rm -rf '$NVM_DIR'"
    echo "nvm の初期化行(~/.profile / ~/.bashrc の NVM_DIR 行)は残しています。不要なら手で削除してください" >&2
}
