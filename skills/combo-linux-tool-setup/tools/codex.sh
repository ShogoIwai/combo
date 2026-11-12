# codex — OpenAI Codex CLI(npm グローバル)
TOOL_DESC="OpenAI Codex CLI (npm -g @openai/codex)"
TOOL_DEPS="node"
# 実行中エージェント自身なら update/remove を止める(npm 置き換えで実行ファイルが消えるため)
TOOL_SELF_REGEX='@openai/\.?codex'
PKG="@openai/codex"

tool_version() { npm_pkg_version "$PKG"; }
tool_latest()  { npm_pkg_latest  "$PKG"; }
tool_install() { npm_pkg_install "$PKG"; }
tool_remove()  { npm_pkg_remove  "$PKG"; }
tool_post()    { echo "初回は 'codex login'"; }
