#!/bin/bash
# create_bare_repo.sh — オンプレミスの共有リモートリポジトリ(bare)を作る
#
#   create_bare_repo.sh [--dry-run] <rep>     作成
#   create_bare_repo.sh --check <rep>         検査のみ(何も変更しない)
#
# 作成手順(この順で実行する):
#   mkdir <rep>
#   chmod 770 <rep>
#   chmod g+s <rep>
#   git init --bare --shared=true <rep>
#
# 終了コード: 0 = 作成/検査 OK, 1 = 検査 NG, 2 = 引数・前提エラー(何も変更していない)
set -u

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

MODE=create
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check)   MODE=check; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage ;;
    --) shift; break ;;
    -*) echo "ERROR: unknown option: $1" >&2; usage ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || usage
REP=$1
if [ -z "$REP" ]; then echo "ERROR: パスが空" >&2; exit 2; fi
# 先頭 '-' のパスを stat/find/git がオプションと読まないよう ./ を付ける
case "$REP" in -*) REP=./$REP ;; esac

# 作成先・検査先を <rep> 以外へ向け得る git の環境変数を外す
for v in GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
         GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE GIT_TEMPLATE_DIR \
         GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT GIT_NAMESPACE GIT_CEILING_DIRECTORIES; do
  if [ -n "${!v+x}" ]; then echo "NOTE: 環境変数 $v を外して実行する" >&2; unset "$v"; fi
done

# 検査: 最上位 2770・bare・<rep>/config の core.sharedRepository=1(group)、
#       配下の全ディレクトリが同じグループで g+rwx・setgid、全ファイルが同じグループで g+r
check() {
  local rep=$1 ng=0 mode bare shared grp head n d
  if [ ! -d "$rep" ] || [ -L "$rep" ]; then echo "NG: $rep がディレクトリでない(または symlink)"; return 1; fi
  mode=$(stat -c '%a' -- "$rep")
  if [ "$mode" = "2770" ]; then echo "OK: mode $mode (drwxrws---)"; else echo "NG: mode $mode (期待 2770)"; ng=1; fi
  bare=$(git --git-dir="$rep" rev-parse --is-bare-repository 2>/dev/null)
  if [ "$bare" = "true" ]; then echo "OK: bare repository"; else echo "NG: bare repository ではない ($bare)"; ng=1; fi
  # global/system の設定を拾わないよう、対象の config ファイルだけを読む
  shared=$(git config --file "$rep/config" --get core.sharedRepository 2>/dev/null)
  case "$shared" in
    1|group|true) echo "OK: core.sharedRepository=$shared ($rep/config)" ;;
    *) echo "NG: $rep/config の core.sharedRepository='$shared' (期待 1)"; ng=1 ;;
  esac
  grp=$(stat -c '%G' -- "$rep")
  echo "group: $grp"
  n=$(find "$rep" -mindepth 1 \( ! -group "$grp" -o \( -type d ! -perm -2070 \) -o \( -type f ! -perm -040 \) \) -print 2>/dev/null | wc -l)
  if [ "$n" -eq 0 ]; then echo "OK: 配下は全て group $grp・ディレクトリ g+rwxs・ファイル g+r"
  else
    echo "NG: 配下に group 違い・グループ権限不足のものが $n 件:"; ng=1
    find "$rep" -mindepth 1 \( ! -group "$grp" -o \( -type d ! -perm -2070 \) -o \( -type f ! -perm -040 \) \) -printf '  %M %g %p\n' 2>/dev/null | head -20
  fi
  # 親をたどれるか(グループ利用者が辿れない祖先は WARN。所属状況は見えないので NG にはしない)
  d=$(cd -- "$rep/.." && pwd -P)
  while [ -n "$d" ]; do
    local a; a=$(stat -c '%A' -- "$d")
    case "${a:6:1}${a:9:1}" in   # グループ x(x/s)・その他 x(x/t)
      [xs]?|?[xt]) ;;
      *) echo "WARN: 祖先 $d はグループ・その他に x が無い(所有者以外は辿れない可能性: $(stat -c '%A %U:%G' -- "$d"))" ;;
    esac
    [ "$d" = / ] && break
    d=$(dirname -- "$d")
  done
  head=$(git --git-dir="$rep" symbolic-ref HEAD 2>/dev/null)
  echo "HEAD -> ${head:-?} (初回 push するブランチをこれに合わせる)"
  return $ng
}

if [ "$MODE" = check ]; then
  check "$REP"; exit $?
fi

# 既存のものは上書きしない(空ディレクトリも含めて止める)
if [ -e "$REP" ] || [ -L "$REP" ]; then
  echo "ERROR: $REP は既に存在する。上書きしない(検査は --check)。" >&2
  exit 2
fi
PARENT=$(dirname -- "$REP")
if [ ! -d "$PARENT" ]; then
  echo "ERROR: 親ディレクトリ $PARENT が無い(作らない。パスを確認)。" >&2
  exit 2
fi
if [ ! -w "$PARENT" ] || [ ! -x "$PARENT" ]; then
  echo "ERROR: 親ディレクトリ $PARENT に書き込み・探索(wx)権が無い。" >&2
  exit 2
fi

run() {
  echo "+ $*"
  [ "$DRY" -eq 1 ] && return 0
  "$@" || { echo "ERROR: 失敗: $*" >&2; exit 1; }
}

run mkdir -- "$REP"
run chmod 770 -- "$REP"
run chmod g+s -- "$REP"
run git init --bare --shared=true -- "$REP"

[ "$DRY" -eq 1 ] && { echo "(dry-run: 何も変更していない)"; exit 0; }
echo "--- check"
check "$REP"
