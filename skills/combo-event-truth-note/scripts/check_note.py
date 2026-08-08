#!/usr/bin/env python3
"""「ほんとうのこと」ノートの生成物と検査。

  python3 check_note.py report --work <work>
  python3 check_note.py check  --note <note.md> --work <work> [--memos <memo.md> ...]

パイプライン: 分割 → 層の裁定 → ファクトチェック → 深掘り → エッセンス抽出 → 肉付け

検査は**文字列がどこかにある**ではなく、**生成物が項目単位で逐語一致している**かを見る。
§1〜§8 の中身は `report` が作るもので、人が手で書き足す場所ではない。

exit 0 = 全ゲート PASS / 1 = FAIL / 2 = 入力不備（JSON の型不正を含む）
"""
import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import sys
import unicodedata
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from init_ledger import segment, sha256_text  # noqa: E402

LAYERS = ["事実", "感情", "思考", "欲求", "借り物"]
STATUSES = ["CONFIRMED", "CORRECTED", "PARTIAL", "UNVERIFIED", "PRIVATE_PRIMARY"]
PUBLIC_SOURCED = ("CONFIRMED", "CORRECTED", "PARTIAL")

SECTIONS = [
    "§0 この記録について",
    "§1 起きたこと（裏取り済みの事実）",
    "§2 そのとき感じたこと（感情・からだ）",
    "§3 考えたこと（思考）",
    "§4 求めていること（欲求）",
    "§5 まだ言葉にならないこと",
    "§6 手短に済ませた言葉と、書き足したこと",
    "§7 メモの外で調べたこと（深掘り）",
    "§8 エッセンス（肉付け）",
    "§9 核の一文と次の問い",
    "§10 出典",
]
LAYER_SECTION = {"事実": 1, "感情": 2, "思考": 3, "欲求": 4, "借り物": 6}
# 表題を改めた章の旧表記。過去に書いたノートを再検査したときに G10 で黙って
# 落ちないよう別名として受ける（新規は SECTIONS 側の表題で書くこと）。
LEGACY_TITLES = {"§6 借り物の言葉と言い換え": "§6 手短に済ませた言葉と、書き足したこと"}
PASTED = [1, 2, 3, 4, 5, 6, 10]          # 生成された箇条書きを逐語で貼る章
BLOCKED = {7: "D", 8: "E"}               # 生成されたブロックを逐語で貼る章

ESSENCE_MIN, ESSENCE_MAX = 3, 5
FLESH_MIN = 120
QUOTE_MIN = 10
CORRECTED_MAX_RATIO = 0.9               # これ以上メモに似た「更新」は中身が無い

CLICHES = [
    "勉強になった", "勉強になりました", "有意義", "気づきが多", "学びが多",
    "今後の業務に活かし", "業務に活かしたい", "参考になった", "再認識",
    "刺激を受け", "モチベーションが上が", "視野が広がった", "学びが深ま",
    "引き続き頑張", "興味深かった", "非常に良かった", "とても良かった",
    "改めて実感", "改めて感じた", "多くの学びを得",
]
# 自責の語彙。ノートを反省文に変えるのはこのスキルの目的ではないので、
# §9 とエッセンスの地の文からは締め出す（「」で名指すのは可）。
# 自責の語彙。**感情としての自責は §2/§3 に書いてよい**（それが本当なら消さない）。
# ここで弾くのは「ノートの結語を自己採点で閉じる」構図だけなので、検査対象は
# エッセンスの statement と §9 の核の一文の行に限る。
# 単漢字・広い部分文字列（怠・未熟・至らな 等）は「倦怠」「未熟児」「反省会」まで
# 巻き込むので採らない。自己言及が明らかな句だけを並べる。
SELF_BLAME = [
    "反省すべき", "反省した", "反省している", "べきだった", "恥ずかしい",
    "情けない", "浅はかだった", "自分が悪い", "自戒", "私の落ち度",
    "自分の落ち度", "怠っていた", "怠慢だった", "ダメだった", "駄目だった",
]
FIRST_PERSON = re.compile(r"(私|わたし|自分|僕|ぼく|俺|おれ|うち)")
PLACEHOLDER = re.compile(r"(<[^>\n]{1,60}>|＜[^＞\n]{1,60}＞|TODO|TBD|FIXME|xxx|XXX)")
EID_RE = re.compile(r"\bE\d+\b")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
BAD_HOSTS = {"example.com", "example.org", "example.net", "localhost", "test.com"}
QUOTED = re.compile(r"「[^」\n]*」|『[^』\n]*』|\"[^\"\n]*\"")
FENCE = re.compile(r"^\s*(```+|~~~+)")
KANA_ONLY = re.compile(r"^[ぁ-んー、。]+$")


def grams(s, n=4):
    """内容語らしい文字 n-gram の集合。

    日本語は語境界が無いので、文字クラスを並べた正規表現では「節まるごと」が
    1 トークンになり、語彙の接触を判定できない。ひらがなだけの gram（機能語・
    語尾）は落として、固有名詞・数字・漢語だけが残るようにする。
    """
    t = norm(s)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))
            if not KANA_ONLY.match(t[i:i + n])}


def die2(msg):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(2)


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s)))


# ---------------------------------------------------------------- 入力の読み込み
def load_json(path, kind, required=True):
    """JSON を型ごと検証して読む。不正は例外でなく exit 2（入力不備）。"""
    if not os.path.exists(path):
        if required:
            die2(f"{path} が無い。init_ledger.py を先に回すこと。")
        return kind()
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        die2(f"{path} を読めない: {e}")
    if not isinstance(obj, kind):
        die2(f"{path} の最上位が {kind.__name__} でない（{type(obj).__name__}）")
    return obj


def need_dicts(seq, path, idpat):
    for i, x in enumerate(seq):
        if not isinstance(x, dict):
            die2(f"{path}[{i}] が object でない（{type(x).__name__}）")
        if not re.fullmatch(idpat, str(x.get("id", ""))):
            die2(f"{path}[{i}].id が {idpat} の形式でない: {x.get('id')!r}")
    return seq


def need_strlist(x, where):
    if x is None:
        return []
    if not isinstance(x, list) or any(not isinstance(v, str) for v in x):
        die2(f"{where} は文字列の配列でなければならない")
    return x


def need_srclist(x, where):
    if x is None:
        return []
    if not isinstance(x, list) or any(not isinstance(v, dict) for v in x):
        die2(f"{where} は object の配列でなければならない")
    return x


def load(work):
    led = load_json(os.path.join(work, "ledger.json"), dict)
    rows = led.get("rows")
    if not isinstance(rows, list) or not rows:
        die2("ledger.json の rows が空、または配列でない")
    need_dicts(rows, "ledger.json rows", r"O\d+")
    for r in rows:
        for k in ("text", "context", "doc"):
            if not isinstance(r.get(k), str):
                die2(f"ledger.json {r['id']}.{k} が文字列でない")
        need_srclist(r.get("sources"), f"{r['id']}.sources")
        need_srclist(r.get("private_sources"), f"{r['id']}.private_sources")
        if r.get("searched") not in (None, {}) and not isinstance(r.get("searched"), dict):
            die2(f"{r['id']}.searched が object でない")

    units = load_json(os.path.join(work, "units.json"), list)
    manifest = load_json(os.path.join(work, "manifest.json"), dict)
    if not isinstance(manifest.get("docs"), list) or not manifest.get("units_sha256"):
        die2("manifest.json の docs / units_sha256 が不正")

    digs = need_dicts(load_json(os.path.join(work, "digs.json"), list, False),
                      "digs.json", r"D\d+")
    for d in digs:
        need_srclist(d.get("sources"), f"{d['id']}.sources")
    essence = need_dicts(load_json(os.path.join(work, "essence.json"), list, False),
                         "essence.json", r"E\d+")
    for e in essence:
        need_strlist(e.get("from"), f"{e['id']}.from")
        need_strlist(e.get("grounds"), f"{e['id']}.grounds")
    ev = load_json(os.path.join(work, "evidence.json"), dict, False)
    return units, rows, manifest, digs, essence, ev


class Result:
    def __init__(self):
        self.rows = []

    def add(self, gate, ok, msg=""):
        self.rows.append((gate, bool(ok), msg))

    def report(self):
        fails = [r for r in self.rows if not r[1]]
        for g, ok, m in self.rows:
            print(f"[{'PASS' if ok else 'FAIL'}] {g}" + (f" — {m}" if m else ""))
        print(f"\n{'PASS' if not fails else 'FAIL'}: {len(self.rows) - len(fails)}/{len(self.rows)} gates")
        return 0 if not fails else 1


# ---------------------------------------------------------------- Markdown
def strip_opaque(md: str) -> str:
    """コードフェンスと HTML コメントを落とす（検査対象の本文だけを残す）。

    これをしないと「本文には嘘を書き、コードブロックの中に検査用の正解を置く」
    という迂回が通る。
    """
    md = re.sub(r"(?s)<!--.*?-->", "", md)
    out, in_fence = [], False
    for line in md.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def split_sections(note: str):
    """'## <SECTIONS の表題そのもの>' で章に割る。戻り値: ({index: 本文}, 問題)"""
    body, order, bad, cur = {}, [], [], None
    for line in note.splitlines():
        m = re.match(r"^##[ \t　]+(.+?)\s*$", line)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            title = LEGACY_TITLES.get(title, title)
            idx = next((i for i, s in enumerate(SECTIONS) if s == title), None)
            if idx is None:
                tag = title.split()[0] if title.split() else title
                known = next((i for i, s in enumerate(SECTIONS) if s.split()[0] == tag), None)
                bad.append(f"表題不一致: '{title}'"
                           + (f"（期待 '{SECTIONS[known]}'）" if known is not None else ""))
                cur = None
                continue
            if idx in body:
                bad.append(f"§{idx} が重複している")
            body[idx] = []
            order.append(idx)
            cur = idx
            continue
        if cur is not None:
            body[cur].append(line)
    if order != sorted(order):
        bad.append(f"章の順序が不正: {order}")
    return {k: "\n".join(v) for k, v in body.items()}, bad


def bullets(text):
    return [re.sub(r"\s+", " ", l.strip()) for l in text.splitlines()
            if l.strip().startswith("- ")]


def gen(work, name):
    p = os.path.join(work, name)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def fact_line(r):
    s = f"- [{r['id']}] {r.get('own_words', '')} 〔{r.get('status', '')}〕"
    if r.get("status") == "CORRECTED":
        s += f" ／わかったこと: {r.get('corrected_to', '')}"
    return s


# ---------------------------------------------------------------- report
def cmd_report(work):
    units, rows, manifest, digs, essence, ev = load(work)
    counts = {l: sum(1 for r in rows if r.get("layer") == l) for l in LAYERS}
    st = {s: sum(1 for r in rows if r.get("status") == s) for s in STATUSES}
    unknown = [r for r in rows if r.get("unknown")]

    lines = [f"- 単位: {len(rows)} 件（メモ {len(manifest['docs'])} 本）"]
    lines += [f"- {l}: {counts[l]} 件" for l in LAYERS]
    lines += [f"- 裏取り {s}: {st[s]} 件" for s in STATUSES if st[s]]
    lines += [f"- 深掘り: {len(digs)} 件", f"- エッセンス: {len(essence)} 件",
              f"- まだ言葉にならない: {len(unknown)} 件"]
    W = lambda n, s: open(os.path.join(work, n), "w", encoding="utf-8").write(s)  # noqa: E731

    W("summary.md", "## §0 この記録について\n\n- 宛先: わたし（誰にも見せない）\n"
                    + "\n".join(lines) + "\n")
    for layer, idx in LAYER_SECTION.items():
        sel = [r for r in rows if r.get("layer") == layer]
        if layer == "事実":
            out = [fact_line(r) for r in sel]
        elif layer == "借り物":
            out = [f"- [{r['id']}] 原文「{r.get('borrowed_phrase', '')}」 → {r.get('own_words', '')}"
                   for r in sel]
        else:
            out = [f"- [{r['id']}] {r.get('own_words', '')}"
                   + (f"（からだ: {r['body_sense']}）" if r.get("body_sense") else "")
                   for r in sel]
        W(f"section{idx}.md", "\n".join(out) + ("\n" if out else ""))

    W("section5.md", "\n".join(f"- [{r['id']}] わからないこと: {r.get('unknown_note', '')}"
                               for r in unknown) + ("\n" if unknown else ""))
    W("section7.md", "\n\n".join(
        f"### {d['id']} {d.get('question', '')}\n"
        f"- なぜ私が調べたか: {d.get('why', '')}\n"
        f"- わかったこと: {d.get('finding', '')}\n"
        f"- 出典: " + " / ".join(s.get("url", "") for s in d.get("sources") or [])
        + (f"\n- 残った不明: {d['unresolved']}" if d.get("unresolved") else "")
        for d in digs) + ("\n" if digs else ""))
    W("section8.md", "\n\n".join(
        f"### {e['id']} {e.get('statement', '')}\n"
        f"- 由来: {', '.join(e.get('from') or [])}\n"
        f"- 根拠: {', '.join(e.get('grounds') or [])}\n"
        f"- 落としたもの: {e.get('cut', '')}\n"
        f"- 次も続けたいこと: {e.get('keep', '')}\n\n{e.get('flesh', '')}"
        for e in essence) + ("\n" if essence else ""))

    seen, src = set(), []
    for r in list(rows) + list(digs):
        if r.get("id", "").startswith("O") and r.get("status") not in PUBLIC_SOURCED:
            continue          # 裏取りに使っていない出典は一覧に出さない
        for s in r.get("sources") or []:
            u = s.get("url", "")
            if u and u not in seen:
                seen.add(u)
                src.append(f"- {s.get('title', '')} — {s.get('publisher', '')}"
                           f"（{s.get('date', '')}、参照 {s.get('accessed', '')}）{u}")
    W("section10.md", "\n".join(src) + ("\n" if src else ""))
    print(f"[OK] summary.md / section*.md を {work} に生成した（件数はここでしか作らない）")
    return 0


def used_urls(rows, digs):
    """§10 と G7 が対象にする URL（裏取りに実際に使ったものだけ）。"""
    out = []
    for r in list(rows) + list(digs):
        if r.get("id", "").startswith("O") and r.get("status") not in PUBLIC_SOURCED:
            continue
        for s in r.get("sources") or []:
            if s.get("url") and s["url"] not in out:
                out.append(s["url"])
    return out


# ---------------------------------------------------------------- source check
def check_sources(owner, srcs, ev, ev_dir, bad):
    if not srcs:
        bad.append(f"{owner}:出典なし")
        return
    for x in srcs:
        u = str(x.get("url", ""))
        sp = urllib.parse.urlsplit(u)
        host = (sp.hostname or "").lower()
        if sp.scheme not in ("http", "https") or not host:
            bad.append(f"{owner}:url={u!r}")
            continue
        if host in BAD_HOSTS or any(host.endswith("." + h) for h in BAD_HOSTS):
            bad.append(f"{owner}:placeholder host {host}")
        for k in ("title", "publisher"):
            if not str(x.get(k, "")).strip():
                bad.append(f"{owner}:{k} 空")
        if not DATE_RE.match(str(x.get("date", ""))):
            bad.append(f"{owner}:date={x.get('date')!r}")
        try:
            _dt.date.fromisoformat(str(x.get("accessed", "")))
        except ValueError:
            bad.append(f"{owner}:accessed={x.get('accessed')!r}")
        rec = ev.get(u)
        if not isinstance(rec, dict) or rec.get("status") != 200:
            bad.append(f"{owner}:未取得/取得失敗 ({(rec or {}).get('status', 'なし')}) {u}")
            continue
        for k in ("final_url", "fetched", "sha256"):
            if not str(rec.get(k, "")).strip():
                bad.append(f"{owner}:evidence の {k} が無い（取得記録の偽装）")
        name = str(rec.get("text_file", ""))
        want = f"{hashlib.sha256(u.encode('utf-8')).hexdigest()[:16]}.txt"
        if name != want:
            bad.append(f"{owner}:text_file がこの URL の証跡名でない")
            continue
        tf = os.path.join(ev_dir, name)
        if not os.path.isfile(tf):
            bad.append(f"{owner}:証跡テキストが無い {u}")
            continue
        blob = open(tf, "rb").read()
        if hashlib.sha256(blob).hexdigest() != rec.get("text_sha256"):
            bad.append(f"{owner}:証跡テキストが text_sha256 と不一致（手編集/差し替え）")
            continue
        q = str(x.get("quote", "")).strip()
        if len(q) < QUOTE_MIN:
            bad.append(f"{owner}:quote が短すぎる/無い")
            continue
        if norm(q) not in norm(blob.decode("utf-8", "replace")):
            bad.append(f"{owner}:quote が取得ページに無い（捏造/転記ミス）")


# ---------------------------------------------------------------- check
def cmd_check(note_path, work, memos):
    units, rows, manifest, digs, essence, ev = load(work)
    if not os.path.exists(note_path):
        die2(f"{note_path} が無い。")
    raw = open(note_path, encoding="utf-8").read()
    note = strip_opaque(raw)
    sec, sec_bad = split_sections(note)
    ev_dir = os.path.join(work, "evidence")
    R = Result()

    # ---- G0 入力不変性（分割の再現 + doc/長さの照合）
    paths = memos or [d.get("path") for d in manifest["docs"]]
    bad = list()
    if len(paths) != len(manifest["docs"]):
        bad.append("メモ本数が manifest と違う")
    else:
        re_units = []
        for i, p in enumerate(paths, 1):
            if not p or not os.path.exists(p):
                bad.append(f"{p} が無い")
                continue
            text = open(p, encoding="utf-8").read()
            if sha256_text(text) != manifest["docs"][i - 1].get("sha256"):
                bad.append(f"{os.path.basename(p)} がメモ登録時から変わっている")
            for u in segment(text):
                re_units.append({"id": f"O{len(re_units) + 1}", "doc": f"N{i}", **u})
        if sha256_text(json.dumps(re_units, ensure_ascii=False, sort_keys=True)) != manifest["units_sha256"]:
            bad.append("再分割結果が manifest と一致しない")
        if len(re_units) != len(rows):
            bad.append(f"単位数が違う（再分割 {len(re_units)} / 台帳 {len(rows)}）")
        if json.dumps(units, ensure_ascii=False, sort_keys=True) != \
                json.dumps(re_units, ensure_ascii=False, sort_keys=True):
            bad.append("units.json が再分割結果と一致しない")
        for u, r in zip(re_units, rows):
            if (u["id"], u["text"], u["context"], u["doc"]) != \
                    (r["id"], r["text"], r.get("context"), r.get("doc")):
                bad.append(f"{u['id']} の text/context/doc が台帳で書き換わっている")
                break
    R.add("G0 分割の不変性（メモ→units→rows を再分割して id/doc/context/text を照合）",
          not bad, "; ".join(bad[:3]))

    # ---- G1 台帳網羅
    ids = [r["id"] for r in rows]
    R.add("G1 台帳網羅（1 単位 = 1 行、ID 重複なし）",
          len(ids) == len(set(ids)) == len(units), f"ledger={len(ids)} uniq={len(set(ids))}")

    # ---- G2 未裁定 0
    miss = []
    for r in rows:
        if r.get("layer") not in LAYERS or not str(r.get("layer_rationale", "")).strip() \
                or not str(r.get("own_words", "")).strip():
            miss.append(f"{r['id']}:層")
        if r.get("layer") == "借り物" and not str(r.get("borrowed_phrase", "")).strip():
            miss.append(f"{r['id']}:原文")
        if r.get("unknown") and not str(r.get("unknown_note", "")).strip():
            miss.append(f"{r['id']}:わからないこと")
        if r.get("layer") == "事実":
            if r.get("status") not in STATUSES or len(str(r.get("status_rationale", ""))) < 30:
                miss.append(f"{r['id']}:status/理由 30 字")
        elif r.get("status"):
            miss.append(f"{r['id']}:事実でないのに status")
    R.add("G2 未裁定 0（層の裁定＋事実のファクトチェック）", not miss, f"{miss[:6]}")

    # ---- G3 own_words 非写経
    copies = [r["id"] for r in rows
              if norm(r.get("own_words", "")) == norm(r["text"])
              or len(str(r.get("own_words", "")).strip()) < 15]
    R.add("G3 own_words が自分の言葉（写経・15 字未満でない）", not copies, f"{copies[:5]}")

    # ---- G4 一人称
    nofp = [r["id"] for r in rows if r.get("layer") in ("感情", "思考", "欲求")
            and not FIRST_PERSON.search(str(r.get("own_words", "")))]
    R.add("G4 感情・思考・欲求が一人称", not nofp, f"主語が消えている: {nofp[:5]}")

    # ---- G5 定型句（地の文のみ）
    hits = []
    for r in rows:
        s = QUOTED.sub("", str(r.get("own_words", "")))
        hits += [f"{r['id']}:{c}" for c in CLICHES if c in s]
    for e in essence:
        s = QUOTED.sub("", f"{e.get('statement', '')}\n{e.get('flesh', '')}")
        hits += [f"{e['id']}:{c}" for c in CLICHES if c in s]
    s9 = QUOTED.sub("", sec.get(9, ""))
    hits += [f"§9:{c}" for c in CLICHES if c in s9]
    R.add("G5 定型句を own_words・エッセンス・§9 の地の文に置かない", not hits, f"{hits[:5]}")

    # ---- G6 裁定理由が固有・30 字以上
    seen, short = {}, []
    for r in rows:
        t = re.sub(r"[OD\d\s。、,.]", "", str(r.get("layer_rationale", "")))
        if len(str(r.get("layer_rationale", ""))) < 30:
            short.append(r["id"])
        seen.setdefault(t, []).append(r["id"])
    dup = [v for v in seen.values() if len(v) > 1]
    R.add("G6 層の裁定理由が固有かつ 30 字以上", not dup and not short,
          f"複製={dup[:2]} 短すぎ={short[:5]}")

    # ---- G7 出典の実体
    bad = []
    for r in rows:
        if r.get("status") in PUBLIC_SOURCED:
            check_sources(r["id"], r.get("sources"), ev, ev_dir, bad)
    for d in digs:
        check_sources(d["id"], d.get("sources"), ev, ev_dir, bad)
    R.add("G7 出典の実体（実取得 status=200・取得記録の必須欄・証跡ハッシュ一致・ページ内に実在する引用）",
          not bad, f"{bad[:5]}")

    # ---- G8 CORRECTED / PRIVATE_PRIMARY の実体
    bad = []
    for r in rows:
        if r.get("status") == "CORRECTED":
            ct = str(r.get("corrected_to", "")).strip()
            if not ct:
                bad.append(f"{r['id']}:corrected_to 空")
            else:
                ratio = difflib.SequenceMatcher(None, norm(r["text"]), norm(ct)).ratio()
                if ratio > CORRECTED_MAX_RATIO:
                    bad.append(f"{r['id']}:訂正がメモとほぼ同じ（類似度 {ratio:.2f}）")
                # 訂正後に固有の語が、理由か言い直しのどちらかに現れるはず
                # （「違っていた」とだけ書いて何が正しいのかを書かない訂正を弾く）
                new_g = grams(ct) - grams(r["text"])
                where = grams(r.get("status_rationale", "")) | grams(r.get("own_words", ""))
                if new_g and not (new_g & where):
                    bad.append(f"{r['id']}:訂正後の内容が status_rationale にも own_words にも出てこない")
        if r.get("status") == "PRIVATE_PRIMARY":
            ps = r.get("private_sources") or []
            if not ps:
                bad.append(f"{r['id']}:private_sources なし")
            if r.get("sources"):
                bad.append(f"{r['id']}:非公開一次資料なのに公開 sources がある")
            for p in ps:
                for k in ("document", "page", "classification", "holder", "quote"):
                    if not str(p.get(k, "")).strip():
                        bad.append(f"{r['id']}:private_sources.{k}")
                if p.get("url"):
                    bad.append(f"{r['id']}:非公開資料に url")
    R.add("G8 訂正の実体（メモとの差分・理由に訂正後の内容）／非公開一次資料の証跡",
          not bad, f"{bad[:5]}")

    # ---- G9 UNVERIFIED の探索証跡
    bad = []
    for r in rows:
        if r.get("status") == "UNVERIFIED":
            s = r.get("searched") or {}
            if not (need_strlist(s.get("queries"), f"{r['id']}.searched.queries")
                    and need_strlist(s.get("domains"), f"{r['id']}.searched.domains")):
                bad.append(f"{r['id']}:queries/domains")
    R.add("G9 UNVERIFIED に探索証跡（queries / domains）", not bad, f"{bad[:5]}")

    # ---- G10 章立て（表題完全一致・重複なし・順序）
    missing = [s for i, s in enumerate(SECTIONS) if i not in sec]
    R.add(f"G10 章立て（{len(SECTIONS)} 章・表題完全一致・重複なし・順序）",
          not missing and not sec_bad, f"欠落={missing} {sec_bad[:3]}")

    # ---- G11〜G13 生成された箇条書きが章ごとに逐語で並んでいる
    bad = []
    for idx in PASTED:
        want = bullets(gen(work, f"section{idx}.md"))
        got = bullets(sec.get(idx, ""))
        if want != got:
            miss_ = [b for b in want if b not in got]
            extra = [b for b in got if b not in want]
            bad.append(f"§{idx}: 欠落{len(miss_)}件 余分{len(extra)}件"
                       + (f" 例:{(miss_ or extra)[0][:40]}" if (miss_ or extra) else "（順序違い）"))
    R.add("G11 §1〜§6・§10 が生成された箇条書きと**行単位で完全一致**"
          "（1 行 1 件・取りこぼし・連結・並べ替え・手書き追加を許さない）", not bad, f"{bad[:4]}")

    unk = {r["id"] for r in rows if r.get("unknown")}
    s5ids = set(re.findall(r"\[(O\d+)\]", sec.get(5, "")))
    R.add("G12 §5 にわからないことを全件・それだけ", unk == s5ids,
          f"欠落={sorted(unk - s5ids)[:5]} 余剰={sorted(s5ids - unk)[:5]}")

    b_bad = [r["id"] for r in rows if r.get("layer") == "借り物"
             and str(r.get("borrowed_phrase", "")) not in sec.get(6, "")]
    R.add("G13 §6 に手短に済ませた原文を残す", not b_bad, f"{b_bad[:5]}")

    # ---- G14 深掘り（ID 一意・連番・本文逐語）
    bad = []
    if not digs:
        bad.append("深掘りが 0 件（メモの外を 1 つも調べていない）")
    dids = [d["id"] for d in digs]
    if len(set(dids)) != len(dids):
        bad.append(f"ID 重複 {sorted({i for i in dids if dids.count(i) > 1})}")
    if dids and sorted(dids, key=lambda s: int(s[1:])) != [f"D{i}" for i in range(1, len(dids) + 1)]:
        bad.append(f"ID が D1..D{len(dids)} の連番でない: {dids}")
    fseen = {}
    for d in digs:
        for k, n in (("question", 10), ("finding", 40), ("why", 20)):
            if len(str(d.get(k, "")).strip()) < n:
                bad.append(f"{d['id']}:{k} が {n} 字未満")
        if not FIRST_PERSON.search(str(d.get("why", ""))):
            bad.append(f"{d['id']}:why が一人称でない")
        if not re.search(r"[?？]", str(d.get("question", ""))):
            bad.append(f"{d['id']}:question が問いの形でない")
        fseen.setdefault(norm(d.get("finding", "")), []).append(d["id"])
    bad += [f"finding 複製 {v}" for v in fseen.values() if len(v) > 1]

    # ---- G15 エッセンス（ID 一意・連番・根拠・肉付け）
    ebad = []
    if not (ESSENCE_MIN <= len(essence) <= ESSENCE_MAX):
        ebad.append(f"件数 {len(essence)}（{ESSENCE_MIN}〜{ESSENCE_MAX} 件に絞る）")
    eids = [e["id"] for e in essence]
    if len(set(eids)) != len(eids):
        ebad.append(f"ID 重複 {sorted({i for i in eids if eids.count(i) > 1})}")
    if eids and sorted(eids, key=lambda s: int(s[1:])) != [f"E{i}" for i in range(1, len(eids) + 1)]:
        ebad.append(f"ID が E1..E{len(eids)} の連番でない: {eids}")
    ok_facts = {r["id"] for r in rows
                if r.get("layer") == "事実" and r.get("status") in PUBLIC_SOURCED}
    by_id = {r["id"]: r for r in rows}
    dig_by = {d["id"]: d for d in digs}
    row_ids = set(by_id)
    flseen = {}
    for e in essence:
        stm = str(e.get("statement", "")).strip()
        if len(stm) < 20 or not FIRST_PERSON.search(stm):
            ebad.append(f"{e['id']}:statement が 20 字未満 or 一人称でない")
        frm = e.get("from") or []
        if not frm or any(x not in row_ids for x in frm):
            ebad.append(f"{e['id']}:from が台帳 ID でない {frm}")
        gr = e.get("grounds") or []
        if not any(x in ok_facts or x in dig_by for x in gr):
            ebad.append(f"{e['id']}:grounds に裏取り済み事実も深掘りも無い {gr}")
        if any(x not in row_ids and x not in dig_by for x in gr):
            ebad.append(f"{e['id']}:grounds に実在しない ID")
        if len(str(e.get("cut", "")).strip()) < 20:
            ebad.append(f"{e['id']}:cut が 20 字未満")
        if len(str(e.get("keep", "")).strip()) < 20:
            ebad.append(f"{e['id']}:keep（次も続けたいこと）が 20 字未満")
        flesh = str(e.get("flesh", "")).strip()
        if len(flesh) < FLESH_MIN:
            ebad.append(f"{e['id']}:flesh が {FLESH_MIN} 字未満（肉付け不足）")
        # 肉付けが grounds に一度も触れていない（一般論の水増し）を弾く。
        # 言い換えの語彙一致で判定すると正当な paraphrase を落とすので、
        # **根拠 ID を本文に明示する**という形式的な紐づけを要求する。
        # これは「根拠が主張を支えている」ことの証明ではなく、どの根拠を指して
        # 書いたかを後から辿れるようにするための表記規則。
        for x in gr:
            if x not in flesh:
                ebad.append(f"{e['id']}:flesh に根拠 {x} が明示されていない")
        flseen.setdefault(norm(flesh), []).append(e["id"])
    ebad += [f"flesh 複製 {v}" for v in flseen.values() if len(v) > 1]

    # ---- §7 / §8 のブロック逐語一致（両ゲート共通）
    for idx, pre in BLOCKED.items():
        want = [b.strip() for b in gen(work, f"section{idx}.md").split("\n\n### ") if b.strip()]
        body = sec.get(idx, "")
        heads = re.findall(rf"^###\s+({pre}\d+)\b", body, re.M)
        target = bad if pre == "D" else ebad
        if len(heads) != len(set(heads)):
            target.append(f"§{idx}: 見出し ID が重複")
        expect = [d["id"] for d in (digs if pre == "D" else essence)]
        if heads != expect:
            target.append(f"§{idx}: 見出しが {expect} と一致しない（{heads}）")
        for blk in want:
            blk = blk if blk.startswith("###") else "### " + blk
            if norm(blk) not in norm(body):
                target.append(f"§{idx}: {blk.split()[1]} のブロックが逐語で無い")

    R.add("G14 深掘りの実体（1 件以上・ID 一意/連番・問い/一人称の動機/わかったこと・§7 に全ブロック逐語）",
          not bad, f"{bad[:4]}")
    R.add(f"G15 エッセンス {ESSENCE_MIN}〜{ESSENCE_MAX} 件・ID 一意/連番・根拠が実在し肉付けが根拠に触れる"
          f"・cut・flesh {FLESH_MIN} 字・§8 に全ブロック逐語", not ebad, f"{ebad[:4]}")

    # ---- G16 §9 核の一文と次の問い（項目単位）
    s9raw = sec.get(9, "")
    cores = [l.strip() for l in s9raw.splitlines()
             if re.match(r"^-\s*核の一文\s*[:：]", l.strip())]
    qs = [re.sub(r"^-\s*次の問い\s*[:：]\s*", "", l.strip()) for l in s9raw.splitlines()
          if re.match(r"^-\s*次の問い\s*[:：]", l.strip())]
    bad = []
    if len(cores) != 1:
        bad.append(f"核の一文が {len(cores)} 行（ちょうど 1 行）")
    else:
        txt = re.sub(r"^-\s*核の一文\s*[:：]\s*", "", cores[0])
        if len(txt) < 30 or not FIRST_PERSON.search(txt):
            bad.append(f"核が 30 字未満 or 一人称でない（{len(txt)}字）")
        eids_in = set(EID_RE.findall(cores[0])) & set(eids)
        if not eids_in:
            bad.append("核の一文と**同じ行**に実在する E# が無い")
    todos = [re.sub(r"^-\s*次にやること\s*[:：]\s*", "", l.strip())
             for l in s9raw.splitlines()
             if re.match(r"^-\s*次にやること\s*[:：]", l.strip())]
    if not any(len(t) >= 10 for t in todos):
        bad.append("「- 次にやること:」が 10 字以上で 1 件も無い")
    qs = [q for q in qs if re.search(r"[?？]\s*$", q)]
    if len(set(norm(q) for q in qs)) < 2:
        bad.append(f"「?」で終わる次の問いが {len(set(norm(q) for q in qs))} 件（2 件以上・別内容）")
    R.add("G16 §9 核の一文（1 行・一人称 30 字以上・同じ行に E#）＋次の問い 2 件以上"
          "＋次にやること 1 件以上", not bad,
          f"{bad[:3]}")

    # ---- G17 §10 は生成物の逐語（G11 で一致検査済み）＋ 実取得 URL のみ
    listed = set(re.findall(r"https?://[^\s\)\]｝＞>、。」』]+", sec.get(10, "")))
    used = set(used_urls(rows, digs))
    unfetched = {u for u in used if (ev.get(u) or {}).get("status") != 200}
    R.add("G17 §10 の URL が裏取りに使った実取得済み URL と一致", listed == used and not unfetched,
          f"欠落={sorted(used - listed)[:2]} 余剰={sorted(listed - used)[:2]} 未取得={sorted(unfetched)[:2]}")

    # ---- G18〜G21
    R.add("G18 §0 が「誰にも見せない」宛先を宣言", "誰にも見せない" in sec.get(0, ""))
    ph = PLACEHOLDER.findall(note)
    R.add("G19 placeholder・山括弧ゼロ", not ph, f"{ph[:5]}")
    gen0 = gen(work, "summary.md")
    gen0 = gen0.split("\n", 1)[1].strip() if "\n" in gen0 else ""
    R.add("G20 §0 は summary.md の逐語（件数を手で書かない）",
          bool(gen0) and norm(gen0) in norm(note),
          "check_note.py report を回して §0 に貼ること")

    blame = []
    for e in essence:
        t = QUOTED.sub("", str(e.get("statement", "")))
        blame += [f"{e['id']}:{w}" for w in SELF_BLAME if w in t]
    core_line = QUOTED.sub("", cores[0]) if len(cores) == 1 else ""
    blame += [f"§9核:{w}" for w in SELF_BLAME if w in core_line]
    R.add("G21 結語（エッセンスの statement・§9 の核の一文）を自己採点で閉じない"
          "（自責の感情そのものは §2/§3 に書いてよい）", not blame, f"{blame[:5]}")

    return R.report()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report"); r.add_argument("--work", required=True)
    c = sub.add_parser("check")
    c.add_argument("--note", required=True); c.add_argument("--work", required=True)
    c.add_argument("--memos", nargs="*", default=[])
    a = ap.parse_args()
    sys.exit(cmd_report(a.work) if a.cmd == "report" else cmd_check(a.note, a.work, a.memos))


if __name__ == "__main__":
    main()
