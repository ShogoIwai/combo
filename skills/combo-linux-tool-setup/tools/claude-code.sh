# claude-code — Anthropic Claude Code CLI(npm グローバル)
TOOL_DESC="Claude Code CLI (npm -g @anthropic-ai/claude-code)"
TOOL_DEPS="node"
# 実行中エージェント自身なら update/remove を止める(npm 置き換えで実行ファイルが消えるため)
TOOL_SELF_REGEX='@anthropic-ai/\.?claude-code'
PKG="@anthropic-ai/claude-code"

tool_version() { npm_pkg_version "$PKG"; }
tool_latest()  { npm_pkg_latest  "$PKG"; }
tool_install() { npm_pkg_install "$PKG"; }
tool_remove()  { npm_pkg_remove  "$PKG"; }
tool_post()    { echo "初回は 'claude' を起動して /login"; }
