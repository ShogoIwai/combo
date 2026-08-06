#!/usr/bin/env python3
"""fetch_sources.py — 台帳に書かれた出典 URL を**実際に取得**し、機械的な証跡を残す。

出典欄は書こうと思えばいくらでも作文できる。URL の形だけを見るゲートは
「実在ドメイン＋架空パス＋それらしいタイトル」を通してしまうので、
ここで実物を取りに行き、取れたものだけを裏取り済みとして扱えるようにする。

出力:
  work/evidence.json        {url: {final_url, status, fetched, sha256, text_sha256, text_file, ...}}
  work/evidence/<sha>.txt   取得ページの抽出テキスト (quote 照合の対象)

usage:
  python3 fetch_sources.py --work <work> [--timeout 20] [--retry 1] [--refresh]
                           [--max-bytes 8000000] [--max-age-days 30]

取得できなかった URL は evidence に status を残す(200 以外)。その出典は
check_report.py の G4 で不合格になるので、台帳の出典を差し替えるか、
その行を UNVERIFIED / PRIVATE_PRIMARY へ裁定し直すこと。
**証跡を手で書き足して通すのは goal 未達の隠蔽。** (G4 は text_sha256 も照合する)

安全側の制限: loopback / private / link-local / reserved アドレスへは行かない
(クラウドのメタデータエンドポイント等への SSRF を防ぐ)。リダイレクトは各段で
同じ検査を通し、レスポンスは --max-bytes で打ち切る。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import http.client
import ipaddress
import json
import pathlib
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import urllib.parse

UA = "Mozilla/5.0 (compatible; combo-conference-report/1.0)"
TAG_RE = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
STRIP_RE = re.compile(r"(?s)<[^>]+>")
WS_RE = re.compile(r"[ \t　]+")
MAX_REDIRECT = 5
TEXT_CTYPES = ("text/html", "text/plain", "application/xhtml", "application/xml", "text/xml")


def die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ SSRF guard
def resolve_public(url: str) -> tuple[str, str]:
    """解決先 IP を検査し (検証済み IP, 理由) を返す。理由が非空なら取得しない。

    **検証した IP をそのまま接続先に使う** (呼び出し側が connect する)。
    「検査してから urllib に再解決させる」と、その間に別の IP を返す
    DNS rebinding で内部アドレスへ回されるので、検査と接続を同じ IP に固定する。
    """
    sp = urllib.parse.urlsplit(url)
    if sp.scheme not in ("http", "https"):
        return "", f"scheme が http(s) でない: {sp.scheme}"
    if sp.username or sp.password:
        return "", "URL に userinfo が含まれる"
    host = sp.hostname
    if not host:
        return "", "host が無い"
    port = sp.port or (443 if sp.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as e:
        return "", f"名前解決できない: {e}"
    picked = ""
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return "", f"公開アドレスでない宛先 ({ip}) — SSRF 防止のため取得しない"
        picked = picked or str(ip)
    if not picked:
        return "", "解決結果が空"
    return picked, ""


class _PinnedHTTP(http.client.HTTPConnection):
    """接続先 IP を固定した平文接続 (Host ヘッダは元のホスト名)。"""

    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):  # noqa: D102
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    """接続先 IP を固定した TLS 接続。証明書検証と SNI は元のホスト名で行う。

    こうしないと「検査した IP」と「実際に繋ぐ IP」がずれ (DNS rebinding)、
    公開 IP を見せて接続時に内部 IP へ回す経路が残る。
    """

    def __init__(self, host, ip, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):  # noqa: D102
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def open_pinned(url: str, ip: str, timeout: int):
    """検証済み IP へ直接つなぎ、Host / SNI / 証明書検証は元のホスト名で行う。

    リダイレクトは追わない (呼び出し側が 1 段ずつ検査してから辿る)。
    """
    sp = urllib.parse.urlsplit(url)
    host = sp.hostname
    port = sp.port or (443 if sp.scheme == "https" else 80)
    path = urllib.parse.urlunsplit(("", "", sp.path or "/", sp.query, "")) or "/"
    if sp.scheme == "https":
        conn = _PinnedHTTPS(host, ip, port, timeout, ssl.create_default_context())
    else:
        conn = _PinnedHTTP(host, ip, port, timeout)
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Connection": "close"}
    conn.request("GET", path, headers=hdrs)
    return conn.getresponse(), conn


def to_text(body: bytes, ctype: str) -> tuple[str, str]:
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    if m:
        charset = m.group(1)
    try:
        raw = body.decode(charset, errors="replace")
    except LookupError:
        raw = body.decode("utf-8", errors="replace")
    title = ""
    mt = TITLE_RE.search(raw)
    if mt:
        title = html.unescape(STRIP_RE.sub("", mt.group(1))).strip()
    text = html.unescape(STRIP_RE.sub(" ", TAG_RE.sub(" ", raw)))
    text = WS_RE.sub(" ", text)
    text = "\n".join(l.strip() for l in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip(), title


def pdf_to_text(body: bytes) -> tuple[str, str]:
    if not shutil.which("pdftotext"):
        return "", ""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as f:
        f.write(body)
        f.flush()
        try:
            out = subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", f.name, "-"],
                                 capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return "", ""
    return out.stdout.decode("utf-8", errors="replace").strip(), ""


def fetch(url: str, timeout: int, retry: int, max_bytes: int) -> dict:
    last = ""
    for _ in range(retry + 1):
        cur = url
        try:
            for hop in range(MAX_REDIRECT + 1):
                ip, why = resolve_public(cur)
                if why:
                    return {"status": 0, "error": why, "final_url": cur}
                r, conn = open_pinned(cur, ip, timeout)
                try:
                    if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
                        if hop >= MAX_REDIRECT:
                            return {"status": r.status, "error": "リダイレクト過多",
                                    "final_url": cur}
                        cur = urllib.parse.urljoin(cur, r.getheader("Location"))
                        continue
                    if r.status != 200:
                        return {"status": r.status, "error": f"HTTP {r.status}", "final_url": cur}
                    ctype = (r.getheader("Content-Type") or "").lower()
                    body = r.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        return {"status": r.status, "error": f"{max_bytes} byte を超える",
                                "final_url": cur}
                    if "application/pdf" in ctype or body[:5] == b"%PDF-":
                        text, title = pdf_to_text(body)
                        if not text:
                            return {"status": r.status, "final_url": cur,
                                    "error": "PDF からテキストを取れない "
                                             "(pdftotext 不在 or 画像 PDF)"}
                    elif any(c in ctype for c in TEXT_CTYPES) or not ctype:
                        text, title = to_text(body, ctype)
                    else:
                        return {"status": r.status, "final_url": cur,
                                "error": f"テキスト化できない Content-Type: {ctype}"}
                    return {"final_url": cur, "status": r.status, "ctype": ctype,
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "title": title, "text": text}
                finally:
                    conn.close()
            return {"status": 0, "error": "リダイレクト過多", "final_url": cur}
        except Exception as e:  # TLS / timeout / socket / HTTP パース
            last = f"{type(e).__name__}: {e}"
    return {"status": 0, "error": last, "final_url": url}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--retry", type=int, default=1)
    ap.add_argument("--max-bytes", type=int, default=8_000_000)
    ap.add_argument("--max-age-days", type=int, default=30,
                    help="これより古い取得済み証跡は取り直す")
    ap.add_argument("--refresh", action="store_true", help="取得済みでも全件取り直す")
    args = ap.parse_args()

    work = pathlib.Path(args.work)
    lp = work / "ledger.json"
    if not lp.is_file():
        die(f"{lp} not found — init_ledger.py を先に回すこと")
    try:
        rows = json.loads(lp.read_text(encoding="utf-8"))["rows"]
    except Exception as e:
        die(f"ledger.json を読めない: {e}")

    urls: list[str] = []
    for r in rows:
        for s in r.get("sources") or []:
            if isinstance(s, dict) and s.get("url") and s["url"] not in urls:
                urls.append(s["url"])
    if not urls:
        die("台帳に出典 URL が 1 つも無い — 先に裁定と裏取りを済ませること")

    ev_dir = work / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    ev_p = work / "evidence.json"
    ev = json.loads(ev_p.read_text(encoding="utf-8")) if ev_p.is_file() else {}

    ok = 0
    for u in urls:
        old = ev.get(u, {})
        fresh = False
        if not args.refresh and old.get("status") == 200:
            f = ev_dir / str(old.get("text_file", ""))
            try:
                age = (_dt.datetime.now().astimezone()
                       - _dt.datetime.fromisoformat(old.get("fetched", ""))).days
            except ValueError:
                age = 10**6
            body_ok = (f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()
                       == old.get("text_sha256"))
            fresh = body_ok and age <= args.max_age_days
            if body_ok and not fresh:
                print(f"  再取得 (証跡が {age} 日前) {u}")
            elif not body_ok:
                print(f"  再取得 (証跡テキストが欠落/改変) {u}")
        if fresh:
            ok += 1
            print(f"  skip (取得済み・検証済み) {u}")
            continue

        res = fetch(u, args.timeout, args.retry, args.max_bytes)
        rec = {
            "final_url": res.get("final_url", u),
            "status": res.get("status", 0),
            "fetched": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "sha256": res.get("sha256", ""),
            "ctype": res.get("ctype", ""),
            "title": res.get("title", ""),
        }
        if res.get("status") == 200 and res.get("text"):
            name = f"{hashlib.sha256(u.encode('utf-8')).hexdigest()[:16]}.txt"
            (ev_dir / name).write_text(res["text"], encoding="utf-8")
            rec["text_file"] = name
            rec["text_sha256"] = hashlib.sha256(
                (ev_dir / name).read_bytes()).hexdigest()
            ok += 1
        if res.get("error"):
            rec["error"] = res["error"]
        ev[u] = rec
        print(f"  [{rec['status']}] {u}" + (f" — {rec.get('error','')}" if "error" in rec else ""))

    ev_p.write_text(json.dumps(ev, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK: {ok}/{len(urls)} 件の証跡が有効 -> {ev_p}")
    if ok < len(urls):
        print("Warning: 取得できなかった URL がある。台帳の出典を差し替えるか、"
              "UNVERIFIED / PRIVATE_PRIMARY へ裁定し直すこと", file=sys.stderr)


if __name__ == "__main__":
    main()
