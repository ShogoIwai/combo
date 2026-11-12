#!/usr/bin/env bash
# ComfyUI をコマンドライン専用（GUI 不使用）の画像生成環境として <install-dir> に構築する。
#
#   setup_comfyui.sh <install-dir> [--tag vX.Y.Z] [--python <python3>] [--allow-torch-change]
#   setup_comfyui.sh <install-dir> --update-scripts [--python <python3>]
#                                         # gen.py / fetch_models.py だけ差し替え
#
# - Python は venv を作らず、指定（既定は PATH 上の python3）の環境へ直接 pip で入れる。
#   使った Python の絶対パスは <install-dir>/.comfyui_python に記録し、gen.py / fetch_models.py の
#   shebang にも書き込む（生成時のサーバも同じ Python で動く）。
# - CUDA 版 torch が入っている前提。requirements.txt で torch が入れ替わる場合は止まる
#   （--allow-torch-change で続行）。pip の試し実行自体が失敗した場合も止まる。
# - 既存の ComfyUI clone にローカル変更・未追跡ファイルがあれば止まる（勝手に捨てない）。
# - 何度実行してもよい（既存のプリセット・inputs・README は上書きしない。extra_model_paths.yaml は
#   内容が違えば logs/extra_model_paths.yaml.bak.<日時> に退避してから書き直す）。
# - sudo は使わない。
set -euo pipefail

SKILL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TAG=v0.45.0
PY=""
ALLOW_TORCH=0
ONLY_SCRIPTS=0
DEST=""

die() { echo "error: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --tag) [ $# -ge 2 ] || die "--tag に値が必要"; TAG=$2; shift 2 ;;
    --python) [ $# -ge 2 ] || die "--python に値が必要"; PY=$2; shift 2 ;;
    --allow-torch-change) ALLOW_TORCH=1; shift ;;
    --update-scripts) ONLY_SCRIPTS=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) [ -z "$DEST" ] || die "install-dir は 1 つだけ"; DEST=$1; shift ;;
  esac
done
[ -n "$DEST" ] || die "install-dir を指定してください"
mkdir -p "$DEST"
DEST=$(cd "$DEST" && pwd)

# 使う Python: --python > 記録済み > PATH 上の python3。絶対パスにして記録する
if [ -z "$PY" ] && [ -s "$DEST/.comfyui_python" ]; then
  PY=$(cat "$DEST/.comfyui_python")
fi
PY=${PY:-python3}
PY_ABS=$(command -v "$PY" || true)
[ -n "$PY_ABS" ] || die "python が見つかりません: $PY"
case "$PY_ABS" in /*) ;; *) die "python の絶対パスを解決できません: $PY" ;; esac

copy_scripts() {
  local f
  for f in gen.py fetch_models.py; do
    { printf '#!%s\n' "$PY_ABS"; tail -n +2 "$SKILL_DIR/scripts/$f"; } > "$DEST/.$f.tmp"
    chmod 755 "$DEST/.$f.tmp"
    mv -f "$DEST/.$f.tmp" "$DEST/$f"
  done
  printf '%s\n' "$PY_ABS" > "$DEST/.comfyui_python"
  echo "scripts: gen.py, fetch_models.py（python: $PY_ABS）"
}

if [ "$ONLY_SCRIPTS" = 1 ]; then
  copy_scripts
  exit 0
fi

# ---- 前提確認
command -v git >/dev/null || die "git がありません"
command -v nvidia-smi >/dev/null || die "nvidia-smi がありません（NVIDIA ドライバ未導入？）"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
"$PY_ABS" - <<'EOF' || die "CUDA が使える torch が指定の python に入っていません（PyTorch 公式手順で CUDA 版を導入）"
import sys, torch
print(f"python {sys.version.split()[0]}  torch {torch.__version__}  cuda {torch.cuda.is_available()}")
sys.exit(0 if torch.cuda.is_available() else 1)
EOF

# ---- ComfyUI 本体（タグ固定）
C="$DEST/ComfyUI"
if [ ! -d "$C/.git" ]; then
  [ ! -e "$C" ] || die "$C が git clone ではありません"
  git clone -q https://github.com/comfyanonymous/ComfyUI.git "$C"
fi
dirty=$(git -C "$C" status --porcelain --untracked-files=normal -- . ':!extra_model_paths.yaml')
if [ -n "$dirty" ]; then
  echo "$dirty" | head -20 >&2
  die "$C にローカル変更・未追跡ファイルがあります（退避するか消してから再実行）"
fi
git -C "$C" fetch -q --tags
git -C "$C" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || die "タグがありません: $TAG"
git -C "$C" checkout -q "$TAG"
echo "ComfyUI: $(git -C "$C" describe --tags)"

# ---- 依存（torch を入れ替えないか先に確認。試し実行の失敗も止める）
if ! plan=$("$PY_ABS" -m pip install --dry-run -r "$C/requirements.txt" 2>&1); then
  echo "$plan" | tail -20 >&2
  die "pip install --dry-run が失敗しました"
fi
would=$(printf '%s\n' "$plan" | grep '^Would install' || true)
if printf '%s\n' "$would" | grep -q -E '(^| )(torch|torchvision|torchaudio)-[0-9]'; then
  echo "$would"
  [ "$ALLOW_TORCH" = 1 ] || die "requirements.txt が torch を入れ替えようとしています（--allow-torch-change で続行）"
fi
"$PY_ABS" -m pip install -q -r "$C/requirements.txt"
"$PY_ABS" -c "import torch; assert torch.cuda.is_available(); print('torch after install:', torch.__version__)"

# ---- 配置
mkdir -p "$DEST"/models/{checkpoints,loras,vae,embeddings,upscale_models} \
         "$DEST"/{outputs,logs,inputs,presets}
emp="$C/extra_model_paths.yaml"
new=$(cat <<EOF
shared:
  base_path: $DEST/models
  checkpoints: checkpoints
  loras: loras
  vae: vae
  embeddings: embeddings
  upscale_models: upscale_models
EOF
)
if [ -e "$emp" ] && [ "$(cat "$emp")" != "$new" ]; then
  bak="$DEST/logs/extra_model_paths.yaml.bak.$(date +%Y%m%d%H%M%S)"   # clone の外に置く（dirty 判定を汚さない）
  mkdir -p "$DEST/logs"
  cp -p "$emp" "$bak"
  echo "extra_model_paths.yaml の既存内容を $bak に退避しました（差分は diff で確認）"
fi
printf '%s\n' "$new" > "$emp"
copy_scripts
[ -e "$DEST/presets/default.yaml" ] || cp "$SKILL_DIR/templates/presets/default.yaml" "$DEST/presets/default.yaml"
[ -e "$DEST/inputs/sample.txt" ] || cp "$SKILL_DIR/templates/inputs/sample.txt" "$DEST/inputs/sample.txt"
[ -e "$DEST/README.md" ] || cp "$SKILL_DIR/templates/README.md" "$DEST/README.md"

cat <<EOF

完了: $DEST
次:
  1. presets/default.yaml の CHANGE_ME を埋める（別名で作るなら default.yaml は消すか、
     --preset <名前> / COMFYUI_PRESET=<名前> で指定する。default.yaml があるとそれが既定になる）
  2. Civitai から取るなら API キーを ~/.config/civitai/token に置く（README の「準備」参照）
  3. ./fetch_models.py               # モデル取得・sha256 照合
  4. ./gen.py -P sample              # 生成（初回はサーバ自動起動）
EOF
