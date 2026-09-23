#!/bin/bash
# Stop — このセッションが残した rg を落とす（保険）。
#
# 対象は **自分と同じセッション ID (SID) に属する** rg だけ。SID は fork で
# 継承され init への付け替え後も残るので、孤児化した rg も拾える。
# 別ターミナルで動いている他のセッションは SID が違うので触らない。
cat >/dev/null 2>&1

MY_SID=$(ps -p $$ -o sid= 2>/dev/null | tr -d ' ')
[ -z "$MY_SID" ] && exit 0

for pid in $(pgrep -u "$(id -un)" -x rg 2>/dev/null); do
  [ "$(ps -p "$pid" -o sid= 2>/dev/null | tr -d ' ')" = "$MY_SID" ] || continue
  kill "$pid" 2>/dev/null
done
true
