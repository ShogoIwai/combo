# ctags-universal — Universal Ctags(OS パッケージ。導入・更新・削除は root 権限が要るので sudo 依頼になる)。
# 実績手順:
#   sudo apt-get install -y universal-ctags
#   ctags --version   # => Universal Ctags 5.9.0 ...
#   # /usr/bin/ctags-universal が生成される
#
# スキルは sudo を実行しないので、install / update / remove は SUDO_REQUEST(exit 3)を出して止まる。
# ユーザーが依頼行を実行したあと同じコマンドを再実行すると、導入済み・最新として完了する。
# 管理するのは OS パッケージだけ(tool_owned)。手で入れた Universal Ctags は導入済みとして据え置き、
# 更新・削除の対象にしない。最新版は apt の候補版(apt-cache policy。ローカルのパッケージ一覧を読むだけ)。
TOOL_DESC="Universal Ctags (OS package universal-ctags -> /usr/bin/ctags-universal; root 操作はユーザーへ依頼)"
TOOL_DEPS=""

_uctags_apt() { command -v apt-get >/dev/null 2>&1 && command -v dpkg-query >/dev/null 2>&1; }
_uctags_pkg() { if _uctags_apt; then echo universal-ctags; else echo ctags; fi; }

# Universal Ctags の実行ファイル(ctags-universal、無ければ Universal Ctags である ctags)
_uctags_bin() {
    local c
    for c in ctags-universal ctags; do
        command -v "$c" >/dev/null 2>&1 || continue
        "$c" --version 2>/dev/null | head -n1 | grep -q '^Universal Ctags' && { command -v "$c"; return 0; }
    done
    return 0
}

tool_version() {
    local b; b="$(_uctags_bin)"
    [ -n "$b" ] && "$b" --version 2>/dev/null | sed -n '1s/^Universal Ctags \([0-9][0-9.]*\).*/\1/p'
    return 0
}

# OS パッケージの導入版(無ければ空)。apt は universal-ctags、rpm 系は PATH 上の Universal Ctags を所有する ctags パッケージ
_uctags_pkg_installed() {
    if _uctags_apt; then
        dpkg-query -W -f '${Status} ${Version}\n' universal-ctags 2>/dev/null \
            | awk '$3=="installed" { print $4 }'   # want は install / hold どちらも導入済み
    elif command -v rpm >/dev/null 2>&1; then
        # PATH 上の Universal Ctags の実体を ctags パッケージが所有しているときだけ(Exuberant の ctags パッケージや
        # 手で入れたものと取り違えない)
        local b; b="$(_uctags_bin)"; [ -n "$b" ] || return 0
        rpm -qf --qf '%{NAME} %{VERSION}-%{RELEASE}\n' "$(readlink -f -- "$b")" 2>/dev/null \
            | awk '$1=="ctags" { print $2; exit }'
    fi
    return 0
}
# apt-mark hold 中か(hold 中は apt-get install/remove -y が拒否するので、依頼行を出しても完了しない)
_uctags_held() {
    _uctags_apt || return 1
    dpkg-query -W -f '${Status}\n' universal-ctags 2>/dev/null | awk '$1=="hold" { f=1 } END { exit !f }'
}
_uctags_hold_msg() {   # $1=操作
    echo "universal-ctags は apt-mark hold 中のため $1 できない。解除するかはユーザーが判断する:" \
         "解除するなら 'sudo apt-mark unhold universal-ctags' を実行してから同じコマンドを再実行(何も変えていない)" >&2
}

_uctags_pkg_candidate() {
    _uctags_apt || return 0
    LC_ALL=C apt-cache policy universal-ctags 2>/dev/null | awk '/^  Candidate:/ { if ($2 != "(none)") print $2; exit }'
}

# このスキルが管理する分 = OS パッケージ(remove の対象はこれだけ)
tool_owned() { _uctags_pkg_installed; return 0; }

# 最新版はパッケージ管理下のときだけ出す(手で入れたものは比べられないので latest unknown)
tool_latest() { [ -n "$(_uctags_pkg_installed)" ] && _uctags_pkg_candidate; return 0; }

# 実行ファイルの版(<major>.<minor>.<patch>)と apt の版(<upstream>-<revision>)は書式が違うので、パッケージ同士で比べる。
# 候補版以上なら最新扱い(別リポジトリの新しい版を古い候補版へ戻させない)。
# パッケージ管理外・apt 以外・候補版が取れないときは判定できないので更新対象にしない
tool_uptodate() {
    local i c; i="$(_uctags_pkg_installed)"; c="$(_uctags_pkg_candidate)"
    [ -n "$i" ] && [ -n "$c" ] && _uctags_apt || return 0
    dpkg --compare-versions "$i" ge "$c"
}

# 未導入(install)または候補版より古いパッケージ(update)のときだけ sudo 依頼を積む
tool_prereq() {
    if [ -n "$(tool_version)" ] && ! tool_uptodate && _uctags_held; then
        _uctags_hold_msg 更新; return 1
    fi
    if [ -z "$(tool_version)" ] || ! tool_uptodate; then
        need_sudo "$(pkg_install_cmd "$(_uctags_pkg)")" "Universal Ctags は OS パッケージなので導入・更新に root 権限が要る"
        return 1
    fi
    return 0
}

# ここに来るのは prereq を満たした(=導入済み・最新)ときだけ。自分では何も入れない
tool_install() {
    [ -n "$(tool_version)" ] && return 0
    echo "Universal Ctags は root 権限で導入する: $(pkg_install_cmd "$(_uctags_pkg)")(SUDO_REQUEST を参照)" >&2
    return 1
}

# OS パッケージの削除を依頼する(cmd_remove は owned があるときだけ呼ぶ = 手で入れたものは対象外)
tool_remove() {
    local cmd
    _uctags_held && { _uctags_hold_msg 削除; return 1; }
    if _uctags_apt; then cmd="apt-get remove -y universal-ctags"
    elif command -v dnf >/dev/null 2>&1; then cmd="dnf remove -y ctags"
    elif command -v yum >/dev/null 2>&1; then cmd="yum remove -y ctags"
    elif command -v zypper >/dev/null 2>&1; then cmd="zypper remove -y ctags"
    else cmd="<package-manager> remove ctags"
    fi
    need_sudo "$cmd" "Universal Ctags は OS パッケージなので削除に root 権限が要る"
    return 0
}

tool_post() { echo "'ctags-universal --version' で確認(/usr/bin/ctags は alternatives 経由)"; }
