#!/usr/bin/env python3
"""プリセットに書かれたモデルを models/ 以下へ取得し、sha256 を照合する。

  ./fetch_models.py                 # 既定プリセット（$COMFYUI_PRESET → default → presets/ に 1 本だけならそれ）
  ./fetch_models.py --check         # ダウンロードせず有無と sha256 だけ確認
  ./fetch_models.py --redownload    # sha256 が合わない既存ファイルを *.bad に退避して取り直す

Civitai は API キーが必要: 環境変数 CIVITAI_TOKEN か ~/.config/civitai/token に置く
（civitai.com → User Settings → API Keys で発行）。
source の無いモデル（TensorArt 専用 LoRA 等）は手動で置く。置き場所とファイル名を表示する。

取得は同じディレクトリの一時ファイルへ行い、sha256 が合ったときだけ正式名にする
（合わなければ一時ファイルは削除）。1 件の失敗で止めず、最後に失敗一覧を出して終了コード 1。
"""
import argparse
import hashlib
import http.client
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
CONNECT_TIMEOUT = 60          # 接続・1 回の読み取りの無応答上限（秒）
TOTAL_TIMEOUT = 3 * 3600      # 1 ファイルの取得全体の上限（秒）

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



class FetchError(Exception):
    pass


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def civitai_token():
    t = os.environ.get("CIVITAI_TOKEN")
    if t:
        return t.strip()
    p = Path.home() / ".config/civitai/token"
    return p.read_text().strip() if p.exists() else None


def safe_relpath(s, what):
    """相対パスで .. を含まないことを確認する（models/<kind>/ の外へ出さない）。"""
    pp = PurePosixPath(s)
    if not s or pp.is_absolute() or ".." in pp.parts:
        raise FetchError(f"{what} は models/ 配下の相対パスで指定してください: {s!r}")
    return pp


def dest_path(kind, file):
    """models/<kind>/<file>。相対・.. なしを字句で確認する。

    symlink の扱い: models/<kind> 自体やその途中のディレクトリが共有置き場への symlink なのは可。
    ファイル自体が symlink の場合は照合（読み取り）だけ行い、取得・取り直しでは触らない
    （リンクを通常ファイルで置き換えたり、共有元を書き換えたりしない）。
    """
    return MODELS / kind / safe_relpath(file, "file")


def redact(url):
    """表示用の URL。認証情報が入りうる userinfo・クエリ・フラグメントは丸ごと落とす。"""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    if u.port:
        host += f":{u.port}"
    return urllib.parse.urlunsplit((u.scheme, host, u.path, "", ""))


def safe_reason(e):
    """例外の表示用要約。URL を含みうる例外メッセージは出さず、種別と errno 程度に限る。"""
    r = getattr(e, "reason", e)
    name = type(r).__name__
    if isinstance(r, OSError) and r.strerror:
        return f"{name}: {r.strerror}"
    if isinstance(r, str) and "://" not in r and "token" not in r.lower():
        return r[:120]
    return name


def download_url(url, tmp):
    req = urllib.request.Request(url, headers={"User-Agent": "comfyui-fetch/1.0"})
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 22):
                f.write(chunk)
                done += len(chunk)
                if time.monotonic() - t0 > TOTAL_TIMEOUT:
                    raise FetchError(f"取得が {TOTAL_TIMEOUT} 秒を超えました")
                if total:
                    print(f"\r  {done / 2**20:8.0f} / {total / 2**20:.0f} MiB", end="", flush=True)
            print()
            if total and done != total:
                raise FetchError(f"途中で切れました ({done}/{total} bytes)")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code} ({redact(url)})") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError,
            http.client.HTTPException, OSError) as e:
        raise FetchError(f"通信エラー: {safe_reason(e)} ({redact(url)})") from None


def download_hf(src, tmp):
    hf = shutil.which("hf")
    if not hf:
        raise FetchError("hf コマンドがありません (pip install huggingface_hub)")
    hf_file = str(safe_relpath(src["hf_file"], "hf_file"))
    with tempfile.TemporaryDirectory(dir=tmp.parent, prefix=".hf-") as d:
        cmd = [hf, "download", src["hf_repo"], hf_file, "--local-dir", d]
        if src.get("revision"):
            cmd += ["--revision", str(src["revision"])]
        try:
            subprocess.run(cmd, check=True, timeout=TOTAL_TIMEOUT)
        except subprocess.CalledProcessError as e:
            raise FetchError(f"hf download が失敗 (exit {e.returncode})") from None
        except subprocess.TimeoutExpired:
            raise FetchError(f"hf download が {TOTAL_TIMEOUT} 秒を超えました") from None
        os.replace(Path(d) / hf_file, tmp)


def acquire(kind, ent, dst, want):
    src = ent["source"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmpname = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmpname)
    try:
        if "hf_repo" in src:
            download_hf(src, tmp)
        elif "civitai_version" in src:
            tok = civitai_token()
            if not tok:
                raise FetchError("Civitai API キーがありません（CIVITAI_TOKEN か ~/.config/civitai/token）")
            # Authorization ヘッダはリダイレクト先（署名付き URL）にも付いて 400 になるのでクエリで渡す
            q = urllib.parse.urlencode({"token": tok})
            download_url(f"https://civitai.com/api/download/models/{int(src['civitai_version'])}?{q}", tmp)
        elif "url" in src:
            download_url(src["url"], tmp)
        else:
            raise FetchError(f"source の形式が不明: {sorted(src)}")
        if want:
            got = sha256(tmp)
            if got != want:
                raise FetchError(f"sha256 不一致 {got} != {want}（取得物は削除）")
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def fetch(kind, ent, check_only, redownload):
    """戻り値: True=揃っている / False=無い・不一致（理由は表示済み）。"""
    label = f"{kind}/{ent['file']}"
    dst = dest_path(kind, ent["file"])
    want = (ent.get("sha256") or "").lower()
    if dst.exists():
        if not want:
            print(f"[OK] {label} (sha256 指定なし)")
            return True
        got = sha256(dst)
        if got == want:
            print(f"[OK] {label}")
            return True
        print(f"[NG] {label}  sha256 {got} != {want}")
        if check_only or not redownload or not ent.get("source"):
            return False
        if dst.is_symlink():
            print(f"  symlink なので取り直さない（リンク先 {os.readlink(dst)} を手で更新）")
            return False
        bad = dst.with_name(dst.name + ".bad")
        os.replace(dst, bad)
        print(f"  → {bad.name} に退避して取り直します")
    if dst.is_symlink():   # 壊れたリンク（exists() は False）
        print(f"[NG] {label} はリンク先の無い symlink（{os.readlink(dst)}）。手で直してください")
        return False
    src = ent.get("source")
    if check_only or not src:
        print(f"[--] {label} が無い" + ("" if src else "（入手元なし: 手動で配置）"))
        return False
    print(f"[DL] {label}")
    acquire(kind, ent, dst, want)
    print(f"[OK] {label}" + ("" if want else " (sha256 指定なし)"))
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", default=None)
    ap.add_argument("--check", action="store_true", help="取得せず有無と sha256 だけ確認")
    ap.add_argument("--redownload", action="store_true", help="sha256 不一致の既存ファイルを取り直す")
    a = ap.parse_args()
    a.preset = a.preset or default_preset()
    p = Path(a.preset)
    if not p.suffix:
        p = ROOT / "presets" / f"{a.preset}.yaml"
    if not p.exists():
        sys.exit(f"プリセットがありません: {p}")
    pr = yaml.safe_load(p.read_text())
    if "CHANGE_ME" in p.read_text():
        sys.exit(f"{p} に CHANGE_ME が残っています。モデルを書いてから実行してください")

    items = [("checkpoints", pr["checkpoint"], True)]
    if pr.get("vae"):
        items.append(("vae", pr["vae"], True))
    for l in pr.get("loras") or []:
        items.append(("loras", l, l.get("weight", 1) != 0))   # 重み 0（無効）の LoRA は無くても可

    failed = []
    for kind, ent, required in items:
        try:
            ok = fetch(kind, ent, a.check, a.redownload)
        except FetchError as e:
            print(f"[NG] {kind}/{ent.get('file')}: {e}")
            ok = False
        except OSError as e:   # 権限不足・ディレクトリ衝突など。パスは自分の models 配下なので出してよい
            print(f"[NG] {kind}/{ent.get('file')}: {type(e).__name__}: {e.strerror or e}")
            ok = False
        if not ok and required:
            failed.append(f"{kind}/{ent.get('file')}")
    if failed:
        print("\n揃っていないモデル: " + ", ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
