#!/usr/bin/env python3
"""ComfyUI をコマンドラインから使う画像生成 CLI（GUI 不要）。

ComfyUI をヘッドレスのサーバとして裏で起動し（未起動なら自動起動・起動後は常駐してモデルを
VRAM に保持）、プリセット（presets/*.yaml）からワークフローを組んで API に投げ、出力 PNG の
パスを表示する。

例:
  ./gen.py -p "1girl, solo, smile, upper body"            # 既定プリセット（$COMFYUI_PRESET → default → presets/ に 1 本だけならそれ）
  ./gen.py -p "..." --seed 123 -c 4                       # シード指定・4 枚
  ./gen.py -P sakura                                      # inputs/sakura.txt のプロンプトで生成
  ./gen.py -P sakura night -c 2                           # 複数ファイルを順に（各 2 枚）
  ./gen.py -P inputs/                                     # ディレクトリ内の *.txt を全部
  ./gen.py -p "..." --lora style=0.6 --lora detail=0       # プリセット LoRA の重み変更（0 で外す）
  ./gen.py -p "..." --lora-file other.safetensors=0.5      # 任意 LoRA を追加
  ./gen.py --server status | start | stop | log
"""
import argparse
import json
import math
import os
import random
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
COMFY = ROOT / "ComfyUI"
MODELS = ROOT / "models"
OUTPUTS = ROOT / "outputs"
LOGDIR = ROOT / "logs"
INPUTS = ROOT / "inputs"
PIDFILE = LOGDIR / "comfyui.pid"
HOST = os.environ.get("COMFYUI_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMFYUI_PORT", "8188"))
BASE = f"http://{HOST}:{PORT}"
SEED_MAX = 0xFFFFFFFFFFFFFFFF   # ComfyUI KSampler の seed 上限

def default_preset():
    """既定プリセット: $COMFYUI_PRESET → presets/default.yaml → presets/ に 1 本だけならそれ。"""
    env = os.environ.get("COMFYUI_PRESET")
    if env:
        return env
    pd = ROOT / "presets"
    if (pd / "default.yaml").exists():
        return "default"
    ys = sorted(pd.glob("*.yaml"))
    return ys[0].stem if len(ys) == 1 else "default"



# ---------------------------------------------------------------- server
def _get(path, timeout=5):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())


def _post(path, data):
    req = urllib.request.Request(BASE + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit(f"ComfyUI がワークフローを拒否しました ({e.code}):\n{body}")


def server_alive():
    try:
        _get("/system_stats", timeout=2)
        return True
    except Exception:
        return False


def _proc_start_time(pid):
    """/proc/<pid>/stat の starttime（起動からの clock tick）。無ければ None。"""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat[stat.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _proc_cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def _read_pidfile():
    try:
        rec = json.loads(PIDFILE.read_text())
        return rec if isinstance(rec, dict) and isinstance(rec.get("pid"), int) else None
    except (OSError, ValueError):
        return None


def _owned_server_pid():
    """pid ファイルの記録が今も同じ ComfyUI プロセスを指していれば pid を返す。

    pid の再利用で無関係なプロセスを止めないよう、起動時刻・コマンドライン・ポートまで照合する。
    """
    rec = _read_pidfile()
    if not rec:
        return None
    pid = rec["pid"]
    cmd = _proc_cmdline(pid)
    try:
        if (_proc_start_time(pid) == rec.get("start") and b"main.py" in cmd
                and str(rec.get("port")).encode() in cmd and os.getpgid(pid) == pid):
            return pid
    except ProcessLookupError:
        pass
    return None


def _kill_group(pid, wait=15):
    try:
        os.killpg(pid, signal.SIGTERM)
        t = time.monotonic()
        while time.monotonic() - t < wait:
            os.kill(pid, 0)
            time.sleep(0.5)
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _Lock:
    """サーバ起動・停止の排他（同時に 2 つ起動しないように）。"""

    def __enter__(self):
        import fcntl
        LOGDIR.mkdir(exist_ok=True)
        self.f = open(LOGDIR / "comfyui.lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        self.f.close()


def server_start(quiet=False, timeout=180):
    if server_alive():
        if not quiet:
            print(f"ComfyUI は起動済み: {BASE}")
        return
    with _Lock():
        if server_alive():          # ロック待ちの間に他の gen.py が起動した
            return
        OUTPUTS.mkdir(exist_ok=True)
        log = open(LOGDIR / "comfyui.log", "ab")
        cmd = [sys.executable, "main.py", "--listen", HOST, "--port", str(PORT),
               "--disable-auto-launch", "--use-pytorch-cross-attention",
               "--output-directory", str(OUTPUTS), "--preview-method", "none"]
        p = subprocess.Popen(cmd, cwd=COMFY, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        rec = {"pid": p.pid, "start": _proc_start_time(p.pid), "port": PORT}
        PIDFILE.write_text(json.dumps(rec))
        print(f"ComfyUI を起動中 (pid {p.pid}, log {LOGDIR / 'comfyui.log'}) ...", flush=True)

        def fail(msg):
            _kill_group(p.pid, wait=5)
            p.wait()
            if _read_pidfile() == rec:
                PIDFILE.unlink(missing_ok=True)
            sys.exit(f"{msg}。ログ: {LOGDIR / 'comfyui.log'}")

        t = time.monotonic()
        while time.monotonic() - t < timeout:
            if p.poll() is not None:
                fail("ComfyUI が起動に失敗しました")
            if server_alive():
                print("起動完了", flush=True)
                return
            time.sleep(1)
        fail(f"ComfyUI の起動待ちが {timeout} 秒でタイムアウトしました")


def server_stop():
    with _Lock():
        if not PIDFILE.exists():
            print("pid ファイルがありません（このスクリプト以外で起動したサーバは止めません）")
            return
        pid = _owned_server_pid()
        if pid is None:
            PIDFILE.unlink(missing_ok=True)
            print("pid ファイルの記録と一致する ComfyUI がいない（終了済みか pid 再利用）ので、記録だけ消しました（何も止めていません）")
            return
        _kill_group(pid)
        PIDFILE.unlink(missing_ok=True)
        print("ComfyUI を停止しました")


def server_status():
    if server_alive():
        s = _get("/system_stats")
        d = s["devices"][0]
        print(f"running {BASE}  {d['name']}  VRAM free {d['vram_free'] / 2**30:.1f}/"
              f"{d['vram_total'] / 2**30:.1f} GiB  comfyui {s['system'].get('comfyui_version')}")
    else:
        print("stopped")


# ---------------------------------------------------------------- workflow
def load_preset(name):
    p = Path(name)
    if not p.suffix:
        p = ROOT / "presets" / f"{name}.yaml"
    if not p.exists():
        sys.exit(f"プリセットがありません: {p}")
    text = p.read_text()
    if "CHANGE_ME" in text:
        sys.exit(f"{p} に CHANGE_ME が残っています。モデルを書くか --preset で別のプリセットを指定してください")
    pr = yaml.safe_load(text)
    if not isinstance(pr, dict) or not isinstance(pr.get("checkpoint"), dict) or not pr["checkpoint"].get("file"):
        sys.exit(f"{p}: checkpoint.file がありません")
    return pr


def validate_effective(preset, args, loras):
    """CLI 上書き後の実効値を検査する（プリセット YAML の値もサーバ起動前に弾く）。"""
    pr = preset.get("params") or {}

    def eff(name, default):
        v = getattr(args, name)
        return v if v is not None else pr.get(name, default)

    def num(name, v, lo, hi, integer=False):
        ok = isinstance(v, int) and not isinstance(v, bool) if integer else (
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v))
        if not ok or not lo <= v <= hi:
            sys.exit(f"{name} が不正です（{lo}〜{hi}{'の整数' if integer else ''}）: {v!r}")

    num("width", eff("width", 1024), 64, 8192, True)
    num("height", eff("height", 1024), 64, 8192, True)
    num("steps", eff("steps", 25), 1, 1000, True)
    num("cfg", eff("cfg", 5), 0, 100)
    num("clip_skip", eff("clip_skip", 1), 1, 12, True)
    for k in ("sampler", "scheduler"):
        v = eff(k, "x")
        if not isinstance(v, str) or not v:
            sys.exit(f"{k} が不正です: {v!r}")
    for l in loras:
        num(f"LoRA {l.get('name')} の weight", l.get("weight"), -10, 10)
        if not isinstance(l.get("file"), str):
            sys.exit(f"LoRA {l.get('name')} の file が不正です")


def parse_lora_arg(s):
    if "=" not in s:
        sys.exit(f"--lora/--lora-file は NAME=WEIGHT 形式で指定してください: {s}")
    k, v = s.rsplit("=", 1)
    try:
        w = float(v)
    except ValueError:
        w = math.nan
    if not math.isfinite(w):
        sys.exit(f"LoRA の重みが数値ではありません: {s}")
    return k, w


def resolve_loras(preset, args, check_files=True):
    """有効（重み≠0）な LoRA の一覧。check_files=False なら置いていないものも残す（dry-run 用）。"""
    raw = preset.get("loras") or []
    if not isinstance(raw, list) or not all(isinstance(l, dict) and isinstance(l.get("name"), str)
                                            and isinstance(l.get("file"), str) for l in raw):
        sys.exit("プリセットの loras は {name, file, weight} の並びで書いてください")
    loras = [dict(l) for l in raw]
    if args.no_loras:
        loras = []
    for s in args.lora or []:
        k, w = parse_lora_arg(s)
        hit = [l for l in loras if l["name"] == k]
        if not hit:
            sys.exit(f"プリセットに LoRA '{k}' はありません（候補: {[l['name'] for l in loras]}）")
        hit[0]["weight"] = w
    for s in args.lora_file or []:
        f, w = parse_lora_arg(s)
        loras.append({"name": Path(f).stem, "file": f, "weight": w})
    used = []
    for l in loras:
        if l.get("weight") == 0:
            continue
        if not (MODELS / "loras" / l["file"]).exists():
            msg = f"LoRA ファイルが無い: models/loras/{l['file']} ({l['name']})"
            if not check_files:
                print(f"[warn] {msg}（dry-run なので残す）", file=sys.stderr)
                used.append(l)
                continue
            if args.strict_loras:
                sys.exit(msg)
            print(f"[warn] {msg} → スキップ", file=sys.stderr)
            continue
        used.append(l)
    return used


def build_workflow(preset, args, job, seed, loras):
    pr = dict(preset.get("params") or {})
    W = args.width if args.width is not None else pr.get("width", 1024)
    H = args.height if args.height is not None else pr.get("height", 1024)
    W8, H8 = W // 8 * 8, H // 8 * 8
    if (W8, H8) != (W, H) and not job.get("_noted"):
        job["_noted"] = True
        print(f"[info] 解像度 {W}x{H} → {W8}x{H8}（8 の倍数に切り捨て）", file=sys.stderr)
    ckpt = args.ckpt or preset["checkpoint"]["file"]
    vae = args.vae or (preset.get("vae") or {}).get("file")
    clip_skip = args.clip_skip if args.clip_skip is not None else pr.get("clip_skip", 1)
    neg = next(n for n in (args.negative, job.get("negative"), preset.get("negative"), "") if n is not None)

    for kind, f in (("checkpoints", ckpt), ("vae", vae)):
        if f and not (MODELS / kind / f).exists():
            msg = f"モデルが無い: models/{kind}/{f}  （./fetch_models.py で取得）"
            if not args.dry_run:
                sys.exit(msg)
            if not job.get("_warned_" + kind):
                job["_warned_" + kind] = True
                print(f"[warn] {msg}", file=sys.stderr)

    wf = {"ckpt": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}}}
    model, clip = ["ckpt", 0], ["ckpt", 1]
    for i, l in enumerate(loras):
        nid = f"lora{i}"
        wf[nid] = {"class_type": "LoraLoader", "inputs": {
            "model": model, "clip": clip, "lora_name": l["file"],
            "strength_model": l["weight"], "strength_clip": l["weight"]}}
        model, clip = [nid, 0], [nid, 1]
    if clip_skip and clip_skip > 1:
        wf["clipskip"] = {"class_type": "CLIPSetLastLayer",
                          "inputs": {"clip": clip, "stop_at_clip_layer": -int(clip_skip)}}
        clip = ["clipskip", 0]
    if vae:
        wf["vae"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae}}
        vae_ref = ["vae", 0]
    else:
        vae_ref = ["ckpt", 2]
    wf["pos"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": job["prompt"]}}
    wf["neg"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip, "text": neg}}
    wf["latent"] = {"class_type": "EmptyLatentImage",
                    "inputs": {"width": W8, "height": H8, "batch_size": args.batch_size}}
    wf["sampler"] = {"class_type": "KSampler", "inputs": {
        "model": model, "positive": ["pos", 0], "negative": ["neg", 0],
        "latent_image": ["latent", 0], "seed": seed,
        "steps": args.steps if args.steps is not None else pr.get("steps", 25),
        "cfg": args.cfg if args.cfg is not None else pr.get("cfg", 5),
        "sampler_name": args.sampler or pr.get("sampler", "euler"),
        "scheduler": args.scheduler or pr.get("scheduler", "normal"),
        "denoise": 1.0}}
    wf["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sampler", 0], "vae": vae_ref}}
    prefix = f"{time.strftime('%Y%m%d')}/{job['name']}_{time.strftime('%H%M%S')}_s{seed}"
    wf["save"] = {"class_type": "SaveImage", "inputs": {"images": ["decode", 0], "filename_prefix": prefix}}
    return wf


def _in_queue(pid):
    q = _get("/queue")
    return any(len(e) > 1 and e[1] == pid for k in ("queue_running", "queue_pending") for e in q.get(k, []))


def run(wf, timeout):
    """ワークフローを投げて完了を待つ。失敗・中断・タイムアウトは終了コード 1 で抜ける。"""
    cid = str(uuid.uuid4())
    pid = _post("/prompt", {"prompt": wf, "client_id": cid})["prompt_id"]
    t0 = time.monotonic()
    net_err = 0
    missing = 0
    while True:
        if time.monotonic() - t0 > timeout:
            sys.exit(f"生成が {timeout} 秒で終わりませんでした（prompt_id {pid}）。"
                     "サーバ側では処理が続いている可能性があります（--server log で確認）")
        try:
            h = _get(f"/history/{pid}")
            net_err = 0
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            net_err += 1
            if net_err >= 10:
                sys.exit(f"ComfyUI と通信できません: {e}（prompt_id {pid}）")
            time.sleep(1)
            continue
        if pid in h:
            st = h[pid].get("status", {})
            msgs = st.get("messages", [])
            for kind, body in msgs:
                if kind == "execution_error":
                    sys.exit(f"生成エラー: {body.get('node_type')}: {body.get('exception_message')}")
                if kind == "execution_interrupted":
                    sys.exit(f"生成が中断されました（prompt_id {pid}）")
            if st.get("status_str") == "error":
                sys.exit(f"生成エラー: {st}")
            if st.get("completed"):
                break
        else:
            # 履歴にもキューにも無い状態が続けば、キューから削除されたとみなす
            try:
                missing = 0 if _in_queue(pid) else missing + 1
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            if missing >= 6:
                sys.exit(f"ジョブがキューから消えました（prompt_id {pid}）")
        time.sleep(0.5)
    files = []
    for node in h[pid]["outputs"].values():
        for im in node.get("images", []):
            files.append(OUTPUTS / im.get("subfolder", "") / im["filename"])
    return files, time.monotonic() - t0


# ---------------------------------------------------------------- prompt files
def resolve_prompt_files(items):
    out = []
    for it in items:
        cands = [Path(it), INPUTS / it, INPUTS / f"{it}.txt", Path(f"{it}.txt")]
        p = next((c for c in cands if c.exists()), None)
        if p is None:
            sys.exit(f"プロンプトファイルが見つからない: {it}（{INPUTS}/ も探しました）")
        if p.is_dir():
            fs = sorted(p.glob("*.txt"))
            if not fs:
                sys.exit(f"{p} に *.txt がありません")
            out += fs
        else:
            out.append(p)
    return out


LORA_TAG = re.compile(r"<\s*(lora|lyco|hypernet)\s*:[^>]*>", re.I)


def check_lora_tags(text, where):
    tags = [m.group(0) for m in LORA_TAG.finditer(text)]
    if tags:
        sys.exit(f"{where}: A1111 の LoRA タグ {tags} は使えません。プロンプトから消して "
                 "--lora NAME=W / --lora-file FILE=W かプリセットの loras で指定してください")


def read_prompt_file(path):
    """プロンプトファイルを読む。

    - 1 行目以降がポジティブ（改行は ", " ではなく空白でつなぐ。タグ区切りはカンマで書く）
    - 「Negative prompt:」で始まる行以降はネガティブ
    - 「#」で始まる行はコメント、「Steps: 」で始まる A1111 のパラメータ行は無視
    - A1111 の <lora:名前:重み> タグは解釈しない（エラーにする。LoRA は --lora/--lora-file かプリセットで）
    """
    pos, neg = [], None
    cur = pos
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        t = line.strip()
        if not t or t.startswith("#") or t.startswith("Steps: "):
            continue
        if t.lower().startswith("negative prompt:"):
            neg = []
            cur = neg
            t = t[len("negative prompt:"):].strip()
            if not t:
                continue
        cur.append(t)
    if not pos:
        sys.exit(f"ポジティブプロンプトが空: {path}")
    check_lora_tags(" ".join(pos + (neg or [])), path)
    return {"prompt": " ".join(pos), "negative": None if neg is None else " ".join(neg)}


# ---------------------------------------------------------------- main
def validate_args(ap, a):
    """サーバを起動する前に数値を検査する（黙って既定値や丸めに置き換えない）。"""
    def finite(x):
        return x is None or math.isfinite(x)
    if a.count < 1:
        ap.error("--count は 1 以上")
    if not 1 <= a.batch_size <= 64:
        ap.error("--batch-size は 1〜64")
    if a.seed is not None and not 0 <= a.seed <= SEED_MAX:
        ap.error(f"--seed は 0〜{SEED_MAX}")
    for k in ("width", "height"):
        v = getattr(a, k)
        if v is not None and not 64 <= v <= 8192:
            ap.error(f"--{k} は 64〜8192")
    if a.steps is not None and not 1 <= a.steps <= 1000:
        ap.error("--steps は 1〜1000")
    if not finite(a.cfg) or (a.cfg is not None and not 0 <= a.cfg <= 100):
        ap.error("--cfg は 0〜100 の数値")
    if a.clip_skip is not None and not 1 <= a.clip_skip <= 12:
        ap.error("--clip-skip は 1〜12")
    if not (math.isfinite(a.timeout) and a.timeout > 0):
        ap.error("--timeout は正の数値")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-p", "--prompt", help="ポジティブプロンプト")
    ap.add_argument("-P", "--prompt-file", nargs="+", action="extend", metavar="FILE",
                    help="プロンプトファイル（パス／inputs/ 内の名前・.txt 省略可／ディレクトリなら *.txt 全部）")
    ap.add_argument("-n", "--negative", help="ネガティブ（省略時はプリセットの negative）")
    ap.add_argument("--preset", default=None, help="presets/<名前>.yaml またはパス（既定は上記の規則）")
    ap.add_argument("--seed", type=int, help=f"シード 0〜{SEED_MAX}（省略時ランダム）。-c 2 以上なら +1 ずつ")
    ap.add_argument("-c", "--count", type=int, default=1, help="生成回数（シードを +1 しながら繰り返す）")
    ap.add_argument("-b", "--batch-size", type=int, default=1,
                    help="1 回のバッチ枚数（同じシードの 1 回分。各画像の絵は seed+i 単独生成とは一致しない）")
    ap.add_argument("--steps", type=int)
    ap.add_argument("--cfg", type=float)
    ap.add_argument("--sampler")
    ap.add_argument("--scheduler")
    ap.add_argument("-W", "--width", type=int)
    ap.add_argument("-H", "--height", type=int)
    ap.add_argument("--clip-skip", type=int)
    ap.add_argument("--ckpt", help="checkpoints/ 内のファイル名で差し替え")
    ap.add_argument("--vae", help="vae/ 内のファイル名で差し替え")
    ap.add_argument("--lora", action="append", metavar="NAME=W", help="プリセット LoRA の重み変更（0 で外す）")
    ap.add_argument("--lora-file", action="append", metavar="FILE=W", help="loras/ 内の任意 LoRA を追加")
    ap.add_argument("--no-loras", action="store_true", help="プリセットの LoRA を全部外す")
    ap.add_argument("--strict-loras", action="store_true", help="LoRA ファイルが無ければエラー")
    ap.add_argument("--name", default=None, help="出力ファイル名の接頭辞（既定: プロンプトファイル名かプリセット名）")
    ap.add_argument("--timeout", type=float, default=1800, help="1 回の生成を待つ上限（秒、既定 1800）")
    ap.add_argument("--dry-run", action="store_true", help="ワークフロー JSON を表示して終了")
    ap.add_argument("--server", choices=["start", "stop", "status", "log"], help="サーバ操作だけ行う")
    args = ap.parse_args()

    if args.server:
        if args.server == "start":
            server_start()
        elif args.server == "stop":
            server_stop()
        elif args.server == "status":
            server_status()
        else:
            os.execvp("tail", ["tail", "-n", "50", "-f", str(LOGDIR / "comfyui.log")])
        return

    validate_args(ap, args)
    args.preset = args.preset or default_preset()
    jobs = []
    check_lora_tags((args.prompt or "") + " " + (args.negative or ""), "-p/-n")
    if args.prompt:
        jobs.append({"prompt": args.prompt, "name": args.name or Path(args.preset).stem})
    for f in resolve_prompt_files(args.prompt_file or []):
        j = read_prompt_file(f)
        j["name"] = args.name or f.stem
        j["file"] = f
        jobs.append(j)
    if not jobs:
        ap.error("-p/--prompt か -P/--prompt-file が必要です")

    preset = load_preset(args.preset)
    loras = resolve_loras(preset, args, check_files=not args.dry_run)
    seed0 = args.seed if args.seed is not None else random.randint(0, 2**32 - 1)
    if seed0 + args.count - 1 > SEED_MAX:
        ap.error(f"--seed + --count - 1 が上限 {SEED_MAX} を超えます")

    validate_effective(preset, args, loras)
    # サーバを起動する前に全ジョブのワークフローを組む（モデル未配置などはここで止まる）
    planned = [build_workflow(preset, args, j, seed0, loras) for j in jobs]
    if args.dry_run:
        for wf in planned:
            print(json.dumps(wf, indent=2, ensure_ascii=False))
        return

    server_start(quiet=True)
    print("LoRA: " + (", ".join(f"{l['name']}={l['weight']}" for l in loras) or "なし"))
    for j in jobs:
        if "file" in j:
            print(f"== {j['file']}")
        for i in range(args.count):
            seed = seed0 + i
            files, dt = run(build_workflow(preset, args, j, seed, loras), args.timeout)
            for f in files:
                print(f"[{dt:5.1f}s] seed={seed} {f}")


if __name__ == "__main__":
    main()
