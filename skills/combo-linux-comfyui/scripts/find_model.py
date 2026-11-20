#!/usr/bin/env python3
"""モデル（checkpoint / LoRA / VAE）の入手先を探す。プリセットに書く ID・ファイル名・sha256 を得るための補助。

指定されたモデルそのものを探し、無ければ近いもの（同じ作者・同じ画風名・同じ baseModel）を探す。

  find_model.py civitai "<検索語>" [--type LORA|Checkpoint|VAE] [--base "Illustrious"] [--user <作者>] [-n 10]
      Civitai のモデル検索。バージョン ID・baseModel・ファイル名・sha256・トリガー語を出す。
  find_model.py civitai-version <versionId>
      1 バージョンの詳細（プリセットの source: {civitai_version: ...} に書く ID）。
  find_model.py sha256 <sha256>
      同じハッシュのファイルを探す（ファイル名が違っても同一物か確かめる）。Civitai の公開中のバージョンと、
      CivArchive に記録された Civitai のページ・Hugging Face のミラーを出す。
  find_model.py hf "<検索語>" [-n 10]
      Hugging Face のモデルリポジトリ検索（ダウンロード数順）。
  find_model.py hf-files <repo>
      Hugging Face のリポジトリ内の .safetensors とその sha256（source: {hf_repo, hf_file} に書く値）。
  find_model.py civarchive-search "<検索語>" [-n 15]
      Civitai で見つからないときの予備。CivArchive（Civitai で削除・非公開になったモデルも記録している）を検索する。
  find_model.py civarchive <ページ URL か /パス>
      CivArchive の 1 ページから、ファイル名・sha256・baseModel・ダウンロード可否・ミラーを出す。

CivArchive の結果は、fetch_models.py が取得できる配布元（ALLOWED_PLATFORMS = Civitai・Hugging Face）の
ものだけを出す。ミラーが見つかっても、作者が配布を許可していない再アップロードは使わない。
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
ALLOWED_PLATFORMS = {"civitai", "huggingface"}


def get(url, allow_404=False, as_json=True):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
            b = r.read()
    except urllib.error.HTTPError as e:
        if allow_404 and e.code == 404:
            return None
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
    if a.base:
        q["baseModels"] = a.base
    if a.user:
        q["username"] = a.user
    d = get("https://civitai.com/api/v1/models?" + urllib.parse.urlencode(q))
    items = d.get("items", [])
    for m in items:
        print(f"{m['id']}  {m['type']}  {m['name']!r}  by {m.get('creator', {}).get('username')}"
              f"  https://civitai.com/models/{m['id']}")
        for v in m.get("modelVersions", [])[:a.versions]:
            show_version(v)
    if not items:
        print("見つかりません（語を短くする・--base / --type を外す・作者名で --user を試す）")


def cmd_civitai_version(a):
    v = get(f"https://civitai.com/api/v1/model-versions/{int(a.id)}")
    print(f"model {v.get('modelId')}  {v.get('model', {}).get('name')!r}")
    show_version(v, indent="  ")


def cmd_sha256(a):
    h = a.hash.lower()
    v = get(f"https://civitai.com/api/v1/model-versions/by-hash/{h}", allow_404=True)
    if v:
        print(f"civitai: model {v.get('modelId')}  {v.get('model', {}).get('name')!r}"
              f"  https://civitai.com/models/{v.get('modelId')}")
        show_version(v, indent="  ")
    else:
        print("civitai: 公開中のバージョンには同じハッシュのファイルがありません")
    s = get(f"https://civarchive.com/sha256/{h}", allow_404=True, as_json=False) or ""
    urls = sorted(u for u in set(re.findall(r'https://huggingface\.co/[^"\\\s]+', s))
                  if not u.startswith("https://huggingface.co/spaces/"))
    pages = sorted(set(re.findall(r'href="(/(?:models|civitai)/[^"]+)"', s)))
    for u in urls:
        print("civarchive: hf mirror:", u)
    for p in pages:
        print("civarchive: page:", abs_ca(p), " （配布元は civarchive <ページ> で確かめる）")
    if not v and not urls and not pages:
        print("見つかりません")


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


def allowed(platform):
    return str(platform or "").lower() in ALLOWED_PLATFORMS


def cmd_civarchive(a):
    pp, _ = next_data(abs_ca(a.url))
    m = pp.get("model")
    if not isinstance(m, dict):
        sys.exit("モデル情報がありません（バージョンページの URL か、サイトの仕様変更）")
    if not allowed(m.get("platform")):
        sys.exit(f"対象外の配布元です（platform={m.get('platform')}。"
                 f"扱うのは {', '.join(sorted(ALLOWED_PLATFORMS))} だけ）")
    v = m.get("version") or {}
    print(f"{m.get('name')!r}  platform={m.get('platform')}  base={v.get('base_model')}  "
          f"allow_download={v.get('allow_download')}  triggers={v.get('trigger_words') or '-'}")
    print(f"  source page: {v.get('platform_url') or v.get('download_url')}")
    for f in v.get("files", []):
        print(f"  {f['name']}  {f.get('size_kb', 0) / 1024:.0f}MB  sha256={f.get('sha256')}")
        for mi in f.get("mirrors", []):
            if allowed(mi.get("source")):
                print(f"    mirror {mi.get('source')}: {abs_ca(mi.get('url', ''))}")


def cmd_civarchive_search(a):
    pp, _ = next_data("https://civarchive.com/search?" + urllib.parse.urlencode({"q": a.query}))
    data = pp.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        sys.exit("検索結果の構造が想定と違います（data.results なし。サイトの仕様変更？）")
    rs = [r for r in data["results"] if allowed(r.get("platform"))]
    for r in rs[:a.n]:
        print(f"{r.get('platform'):12} {r.get('type'):10} base={r.get('base_model')}  {r.get('name')!r}"
              f"  https://civarchive.com{r.get('url')}")
    if not rs:
        print("見つかりません（Civitai・Hugging Face の記録に無い）")


def cmd_hf(a):
    q = {"search": a.query, "limit": a.n, "sort": "downloads", "direction": "-1"}
    rs = get("https://huggingface.co/api/models?" + urllib.parse.urlencode(q))
    for r in rs:
        print(f"{r.get('id')}  downloads={r.get('downloads')}  likes={r.get('likes')}"
              f"  https://huggingface.co/{r.get('id')}")
    if not rs:
        print("見つかりません")


def cmd_hf_files(a):
    repo = urllib.parse.quote(a.repo, safe="/")
    d = get(f"https://huggingface.co/api/models/{repo}?blobs=true")
    files = [s for s in d.get("siblings", []) if s.get("rfilename", "").endswith(".safetensors")]
    for s in files:
        lfs = s.get("lfs") or {}
        print(f"{s['rfilename']}  {(lfs.get('size') or s.get('size') or 0) / 2**20:.0f}MB"
              f"  sha256={lfs.get('sha256', '-')}")
    if not files:
        print(".safetensors がありません")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("civitai")
    p.add_argument("query")
    p.add_argument("--type", help="LORA / Checkpoint / VAE など")
    p.add_argument("--base", help="baseModel で絞る（Illustrious / NoobAI / Pony / SDXL 1.0 など）")
    p.add_argument("--user", help="作者（Civitai のユーザー名）で絞る")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--versions", type=int, default=3)
    p.set_defaults(f=cmd_civitai)
    p = sp.add_parser("civitai-version")
    p.add_argument("id")
    p.set_defaults(f=cmd_civitai_version)
    p = sp.add_parser("sha256")
    p.add_argument("hash")
    p.set_defaults(f=cmd_sha256)
    p = sp.add_parser("hf")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=10)
    p.set_defaults(f=cmd_hf)
    p = sp.add_parser("hf-files")
    p.add_argument("repo")
    p.set_defaults(f=cmd_hf_files)
    p = sp.add_parser("civarchive-search")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=15)
    p.set_defaults(f=cmd_civarchive_search)
    p = sp.add_parser("civarchive")
    p.add_argument("url")
    p.set_defaults(f=cmd_civarchive)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
