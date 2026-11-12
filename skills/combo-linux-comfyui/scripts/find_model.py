#!/usr/bin/env python3
"""モデル（checkpoint / LoRA）の入手先を探す。プリセットに書く ID・ファイル名・sha256 を得るための補助。

  find_model.py civitai "<検索語>" [--type LORA|Checkpoint] [-n 10]
      Civitai のモデル検索。バージョン ID・baseModel・ファイル名・sha256・トリガー語を出す。
  find_model.py civitai-version <versionId>
      1 バージョンの詳細（プリセットの source: {civitai_version: ...} に書く ID）。
  find_model.py civarchive-search "<検索語>"
      CivArchive（Civitai / TensorArt 等のアーカイブ）の検索結果から、モデル名とページを出す。
  find_model.py civarchive <ページ URL か /パス>
      CivArchive の 1 ページから、ファイル名・sha256・ダウンロード可否・ミラー（HF 等）を出す。
  find_model.py sha256 <sha256>
      CivArchive で同じハッシュのファイルを持つミラー URL を出す。

TensorArt のページは Cloudflare で直接は読めない。TensorArt 専用モデルの情報は CivArchive 経由で見る。
CivArchive はページ構造（__NEXT_DATA__）の解析なので、サイト側の変更で壊れることがある。
API キーは使わない（検索・メタデータは無認証で取れる。ダウンロードは fetch_models.py）。
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (model-finder)"}


def get(url, as_json=True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        sys.exit(f"通信エラー: {type(getattr(e, 'reason', e)).__name__}: {url}")
    if not as_json:
        return b.decode("utf-8", "replace")
    try:
        return json.loads(b)
    except ValueError:
        sys.exit(f"JSON ではない応答です（API の仕様変更？）: {url}")


def show_version(v, indent="   "):
    print(f"{indent}version {v['id']}  {v.get('name')!r}  base={v.get('baseModel')}  "
          f"triggers={v.get('trainedWords') or '-'}")
    for f in v.get("files", []):
        print(f"{indent}  {f['name']}  {f.get('sizeKB', 0) / 1024:.0f}MB  "
              f"sha256={f.get('hashes', {}).get('SHA256', '-')}")


def cmd_civitai(a):
    q = {"query": a.query, "limit": a.n, "nsfw": "true"}
    if a.type:
        q["types"] = a.type
    d = get("https://civitai.com/api/v1/models?" + urllib.parse.urlencode(q))
    for m in d.get("items", []):
        print(f"{m['id']}  {m['type']}  {m['name']!r}  by {m.get('creator', {}).get('username')}"
              f"  https://civitai.com/models/{m['id']}")
        for v in m.get("modelVersions", [])[:a.versions]:
            show_version(v)


def cmd_civitai_version(a):
    v = get(f"https://civitai.com/api/v1/model-versions/{int(a.id)}")
    print(f"model {v.get('modelId')}  {v.get('model', {}).get('name')!r}")
    show_version(v, indent="  ")


def next_data(url):
    s = get(url, as_json=False)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S)
    if not m:
        sys.exit(f"ページ構造を読めません（__NEXT_DATA__ なし。サイトの仕様変更か、ブロックされた？）: {url}")
    try:
        pp = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError, TypeError):
        sys.exit(f"ページ構造が想定と違います（props.pageProps なし。サイトの仕様変更？）: {url}")
    if not isinstance(pp, dict):
        sys.exit(f"ページ構造が想定と違います: {url}")
    return pp, s


def abs_ca(u):
    return u if u.startswith("http") else "https://civarchive.com" + u


def cmd_civarchive(a):
    pp, _ = next_data(abs_ca(a.url))
    m = pp.get("model")
    if not isinstance(m, dict):
        sys.exit("モデル情報がありません（バージョンページの URL か、サイトの仕様変更）")
    v = m.get("version") or {}
    print(f"{m.get('name')!r}  platform={m.get('platform')}  base={v.get('base_model')}  "
          f"allow_download={v.get('allow_download')}  triggers={v.get('trigger_words') or '-'}")
    print(f"  source page: {v.get('platform_url') or v.get('download_url')}")
    for f in v.get("files", []):
        print(f"  {f['name']}  {f.get('size_kb', 0) / 1024:.0f}MB  sha256={f.get('sha256')}")
        for mi in f.get("mirrors", []):
            print(f"    mirror {mi.get('source')}: {abs_ca(mi.get('url', ''))}")


def cmd_civarchive_search(a):
    pp, _ = next_data("https://civarchive.com/search?" + urllib.parse.urlencode({"q": a.query}))
    data = pp.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        sys.exit("検索結果の構造が想定と違います（data.results なし。サイトの仕様変更？）")
    rs = data["results"]
    for r in rs[:a.n]:
        print(f"{r.get('platform'):10} {r.get('type'):10} base={r.get('base_model')}  {r.get('name')!r}"
              f"  https://civarchive.com{r.get('url')}")
    if not rs:
        print("見つかりません")


def cmd_sha256(a):
    s = get(f"https://civarchive.com/sha256/{a.hash.lower()}", as_json=False)
    urls = sorted(set(re.findall(r'https://huggingface\.co/[^"\\\s]+', s)))
    pages = sorted(set(re.findall(r'href="(/(?:models|civitai|tensorart|tensorhub)/[^"]+)"', s)))
    for u in urls:
        print("hf mirror:", u)
    for p in pages:
        print("page:", "https://civarchive.com" + p)
    if not urls and not pages:
        print("見つかりません")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("civitai")
    p.add_argument("query")
    p.add_argument("--type", help="LORA / Checkpoint など")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--versions", type=int, default=3)
    p.set_defaults(f=cmd_civitai)
    p = sp.add_parser("civitai-version")
    p.add_argument("id")
    p.set_defaults(f=cmd_civitai_version)
    p = sp.add_parser("civarchive")
    p.add_argument("url")
    p.set_defaults(f=cmd_civarchive)
    p = sp.add_parser("civarchive-search")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=15)
    p.set_defaults(f=cmd_civarchive_search)
    p = sp.add_parser("sha256")
    p.add_argument("hash")
    p.set_defaults(f=cmd_sha256)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
