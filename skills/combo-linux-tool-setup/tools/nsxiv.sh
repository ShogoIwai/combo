# nsxiv — 画像ビューア nsxiv(無ければ sxiv)の OS パッケージと、削除用 key-handler。
# 実績手順:
#   sudo apt-get install -y nsxiv
#   ~/.config/nsxiv/exec/key-handler と ~/.config/sxiv/exec/key-handler を置いて chmod +x
#   # nsxiv / sxiv の中で Ctrl-x → Ctrl-d で、マーク済み(無ければ表示中)のファイルを削除する
#
# パッケージの導入・更新・削除は root 権限が要るので SUDO_REQUEST(exit 3)を出して止まる(ctags-universal と同じ)。
# 管理するパッケージは、PATH 上で使われている nsxiv(無ければ sxiv)の実体を所有する OS パッケージだけ(tool_owned)。
# key-handler は導入済みなら install / update のたびに tool_sync で置く(nsxiv と sxiv の両方)。
# 置き場所は依頼どおり ~/.config 固定(XDG_CONFIG_HOME は見ない。設定した環境では nsxiv はそこを読まない)。
# 管理するのは管理行(_NSXIV_MARK)を含む key-handler だけ。管理行の無い既存ファイル(リンク切れの symlink を含む)は
# 上書きせず失敗する。動作確認は HOME=<scratch> で隔離する。
TOOL_DESC="nsxiv / sxiv image viewer (OS package; root 操作はユーザーへ依頼) + ~/.config/{nsxiv,sxiv}/exec/key-handler (C-x C-d で削除)"
TOOL_DEPS=""

_NSXIV_MARK="# managed-by: combo-linux-tool-setup nsxiv"
_nsxiv_handlers() { printf '%s\n' "$HOME/.config/nsxiv/exec/key-handler" "$HOME/.config/sxiv/exec/key-handler"; }

# 対象ファイルは stdin で 1 件ずつ(通常は改行区切り、-0 起動時は NUL 区切り)。NUL を改行に直してから読む
_nsxiv_body() {
    cat <<EOF
#!/bin/sh
$_NSXIV_MARK
# (手で書き換えるならこの管理行を消す。消すと管理外になり、スキルは上書きも削除もしない)
# nsxiv / sxiv の中で Ctrl-x → Ctrl-d: 対象ファイル(マーク済み、無ければ表示中)を削除する(復元不可)
case "\$1" in
    "C-d") tr '\\000' '\\n' | while IFS= read -r f; do [ -n "\$f" ] && rm -f -- "\$f"; done ;;
esac
EOF
}

_nsxiv_apt() { command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1; }

# -v で版が取れる実行ファイルの版(「nsxiv <版>」「sxiv <版>」で始まる出力だけ。失敗やエラー文の数字を版にしない)
_nsxiv_ver_of() { local out; out="$("$1" -v 2>&1)" || return 0; printf '%s\n' "$out" | sed -n '1s/^n\{0,1\}sxiv \([0-9][0-9.]*\).*/\1/p'; }

# 使っている実行ファイル(nsxiv を優先。-v で版が取れないものは飛ばす)
_nsxiv_bin() {
    local c b
    for c in nsxiv sxiv; do
        b="$(command -v "$c" 2>/dev/null)" || continue
        [ -n "$(_nsxiv_ver_of "$b")" ] && { echo "$b"; return 0; }
    done
    return 0
}

tool_version() { local b; b="$(_nsxiv_bin)"; [ -n "$b" ] && _nsxiv_ver_of "$b"; return 0; }

# 使っている実行ファイルの実体を所有する OS パッケージ("<pkg> <ver>"。nsxiv / sxiv 以外・管理外なら空)
_nsxiv_pkg_installed() {
    local b r; b="$(_nsxiv_bin)"; [ -n "$b" ] || return 0
    r="$(readlink -f -- "$b")"
    if _nsxiv_apt; then
        local p
        p="$(dpkg-query -S -- "$r" 2>/dev/null | awk -F': ' 'NR==1 { print $1 }')"
        case "$p" in nsxiv|sxiv) ;; *) return 0 ;; esac
        dpkg-query -W -f '${Status} ${Version}\n' "$p" 2>/dev/null | awk -v p="$p" '$3=="installed" { print p, $4 }'
    elif command -v rpm >/dev/null 2>&1; then
        rpm -qf --qf '%{NAME} %{VERSION}-%{RELEASE}\n' -- "$r" 2>/dev/null | awk '$1=="nsxiv" || $1=="sxiv" { print; exit }'
    fi
    return 0
}
_nsxiv_candidate() {   # $1=pkg
    _nsxiv_apt || return 0
    LC_ALL=C apt-cache policy "$1" 2>/dev/null | awk '/^  Candidate:/ { if ($2 != "(none)") print $2; exit }'
}
# 新規導入するパッケージ(nsxiv に候補が無い古い apt 配布なら sxiv)
_nsxiv_new_pkg() {
    if _nsxiv_apt && [ -z "$(_nsxiv_candidate nsxiv)" ] && [ -n "$(_nsxiv_candidate sxiv)" ]; then echo sxiv; else echo nsxiv; fi
}
# apt-mark hold 中か(hold 中は apt-get install/remove -y が拒否するので、依頼行を出しても完了しない)
_nsxiv_held() {   # $1=pkg
    _nsxiv_apt || return 1
    dpkg-query -W -f '${Status}\n' "$1" 2>/dev/null | awk '$1=="hold" { f=1 } END { exit !f }'
}
_nsxiv_hold_msg() {   # $1=pkg $2=操作
    echo "$1 は apt-mark hold 中のため $2 できない。解除するかはユーザーが判断する:" \
         "解除するなら 'sudo apt-mark unhold $1' を実行してから同じコマンドを再実行(何も変えていない)" >&2
}

tool_owned() { _nsxiv_pkg_installed | awk '{ print $2 }'; return 0; }
tool_latest() { local p; p="$(_nsxiv_pkg_installed)"; [ -n "$p" ] && _nsxiv_candidate "${p%% *}"; return 0; }

# パッケージ同士で比べる(候補版以上なら最新扱い)。パッケージ管理外・apt 以外・候補版不明は判定しない
tool_uptodate() {
    local p c; p="$(_nsxiv_pkg_installed)"; [ -n "$p" ] && _nsxiv_apt || return 0
    c="$(_nsxiv_candidate "${p%% *}")"; [ -n "$c" ] || return 0
    dpkg --compare-versions "${p#* }" ge "$c"
}

tool_prereq() {
    if [ -z "$(tool_version)" ]; then
        need_sudo "$(pkg_install_cmd "$(_nsxiv_new_pkg)")" "nsxiv は OS パッケージなので導入に root 権限が要る"
        return 1
    fi
    if ! tool_uptodate; then
        local p; p="$(_nsxiv_pkg_installed)"; p="${p%% *}"
        _nsxiv_held "$p" && { _nsxiv_hold_msg "$p" 更新; return 1; }
        need_sudo "$(pkg_install_cmd "$p")" "$p を apt の候補版へ更新するのに root 権限が要る"
        return 1
    fi
    return 0
}

# ここに来るのは prereq を満たした(=導入済み・最新)ときだけ。key-handler は tool_sync で置く
tool_install() {
    [ -n "$(tool_version)" ] && return 0
    echo "nsxiv は root 権限で導入する: $(pkg_install_cmd "$(_nsxiv_new_pkg)")(SUDO_REQUEST を参照)" >&2
    return 1
}

_nsxiv_write() {   # $1=dest。一時ファイルに書いて +x してから置き換える
    local d="$1" tmp
    mkdir -p -- "$(dirname -- "$d")" || return 1
    tmp="$(mktemp "$d.new.XXXXXX")" || return 1
    _nsxiv_body > "$tmp" && chmod 755 -- "$tmp" && mv -f -- "$tmp" "$d" || { rm -f -- "$tmp"; return 1; }
}
_nsxiv_managed() { [ -f "$1" ] && [ ! -L "$1" ] && grep -qxF "$_NSXIV_MARK" "$1" 2>/dev/null; }

# key-handler を nsxiv / sxiv の両方に置く(冪等)。管理外の既存ファイルがあれば何も変えずに失敗
tool_sync() {
    local h q bad=0
    while IFS= read -r h; do
        if { [ -e "$h" ] || [ -L "$h" ]; } && ! _nsxiv_managed "$h"; then
            echo "$h は管理外の key-handler なので上書きしない(残すなら手で C-d を足す。任せるなら退避してから再実行)" >&2
            bad=1
        fi
    done < <(_nsxiv_handlers)
    [ $bad = 0 ] || return 1
    while IFS= read -r h; do
        if [ -x "$h" ] && [ "$(cat -- "$h")" = "$(_nsxiv_body)" ]; then continue; fi
        printf -v q '%q' "$h"
        run "_nsxiv_write $q" || return 1
    done < <(_nsxiv_handlers)
}

# 管理行つきの key-handler(実体が無くても残る設定)
tool_residue() {
    local h
    while IFS= read -r h; do _nsxiv_managed "$h" && echo "$h"; done < <(_nsxiv_handlers)
    return 0
}
tool_cleanup() {
    local h q
    while IFS= read -r h; do printf -v q '%q' "$h"; run "rm -f -- $q" || return 1; done < <(tool_residue)
}

# パッケージ削除の依頼だけを積む。hold 中は何も変えない
tool_remove() {
    local p cmd; p="$(_nsxiv_pkg_installed)"; p="${p%% *}"
    _nsxiv_held "$p" && { _nsxiv_hold_msg "$p" 削除; return 1; }
    if _nsxiv_apt; then cmd="apt-get remove -y $p"
    elif command -v dnf >/dev/null 2>&1; then cmd="dnf remove -y $p"
    elif command -v yum >/dev/null 2>&1; then cmd="yum remove -y $p"
    elif command -v zypper >/dev/null 2>&1; then cmd="zypper remove -y $p"
    else cmd="<package-manager> remove $p"
    fi
    # key-handler はパッケージ削除後の再実行で後始末する(依頼が実行されないうちは C-d を残す)
    need_sudo "$cmd" "$p は OS パッケージなので削除に root 権限が要る"
    return 0
}

tool_post() { echo "nsxiv(sxiv)の中で Ctrl-x → Ctrl-d でマーク済み(無ければ表示中)のファイルを削除できる(復元不可)"; }
