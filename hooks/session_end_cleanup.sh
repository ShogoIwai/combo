#!/bin/bash
# SessionEnd — このセッションが残した claude.exe（Claude Code 本体）を落とす。
#
# 対象は **自分と同じセッション ID (SID) に属し、自分の祖先ではなく、かつ次の
# どちらか** に当たるものだけ:
#   (a) 終了しようとしている自セッションの claude.exe の子孫
#       （サブエージェント・claude -p fork など、このセッションが起動したもの）
#   (b) 親が init / systemd に付け替わった孤児（親が先に死んで残ったもの）
# 同じ端末から並行起動した別の claude.exe（親はシェル）や、その子孫の fork は
# (a)(b) のどちらにも当たらないので触らない。別ターミナルは SID が違うので対象外。
cat >/dev/null 2>&1

MY_SID=$(ps -p $$ -o sid= 2>/dev/null | tr -d ' ')
[ -z "$MY_SID" ] && exit 0

# 祖先 PID の一覧と、最も近い claude.exe の祖先（= 終了中の自セッション）
anc=" "; me=""; p=$PPID
while [ -n "$p" ] && [ "$p" != "1" ] && [ "$p" != "0" ]; do
  anc="$anc$p "
  [ -z "$me" ] && [ "$(ps -p "$p" -o comm= 2>/dev/null)" = "claude.exe" ] && me=$p
  p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
done

is_leftover() {
  local q ppid
  ppid=$(ps -o ppid= -p "$1" 2>/dev/null | tr -d ' ')
  [ -z "$ppid" ] && return 1
  # (b) 孤児
  [ "$ppid" = "1" ] && return 0
  case "$(ps -p "$ppid" -o comm= 2>/dev/null)" in systemd|init) return 0 ;; esac
  # (a) 自セッションの子孫
  [ -z "$me" ] && return 1
  q=$ppid
  while [ -n "$q" ] && [ "$q" != "1" ] && [ "$q" != "0" ]; do
    [ "$q" = "$me" ] && return 0
    q=$(ps -o ppid= -p "$q" 2>/dev/null | tr -d ' ')
  done
  return 1
}

for pid in $(pgrep -u "$(id -un)" -x 'claude\.exe' 2>/dev/null); do
  case "$anc" in *" $pid "*) continue ;; esac
  [ "$(ps -p "$pid" -o sid= 2>/dev/null | tr -d ' ')" = "$MY_SID" ] || continue
  is_leftover "$pid" || continue
  kill "$pid" 2>/dev/null
done
true
