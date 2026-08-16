#!/usr/bin/env python3
"""combo-session-handoff のゲート。

引き継ぎ md が「次のセッションが再調査ゼロで再開できる」水準にあるかを
決定的に検査する。薄い引き継ぎ（数字が無い / 失敗案が無い / 次の一手が空文句）を落とす。

  python3 check_handoff.py --file HANDOFF_x_y_2026-08-16.md --repo <launch-root>/<repo>

exit 0 = 全 PASS / exit 1 = FAIL あり / exit 2 = 入力不備
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REQUIRED_SECTIONS = [
    "The Goal",
    "Where We Are",
    "What We Tried (Chronological)",
    "Key Decisions",
    "Evidence & Data",
    "Files Changed",
    "User Feedback & Preferences",
    "Where We're Going",
    "Risks & Blockers",
    "Open Questions",
    "Quick Start for Next Session",
]

REQUIRED_HEADERS = ["Date", "Status", "Repo", "Chain", "Parent"]
STATUS_VALUES = {"COMPLETED", "IN PROGRESS", "BLOCKED"}

# handoff を置いてよいディレクトリ（repo 相対）。G9 の親探索範囲でもある。
HANDOFF_DIRS = [
    "plans/handoffs",
    ".claude/handoffs",
    ".handoff",
    "plans/handoffs/archive",
    ".claude/handoffs/archive",
    ".handoff/archive",
]

# 次の一手として無意味な決まり文句
VAGUE_NEXT = [
    "continue working",
    "continue the work",
    "keep going",
    "作業を続ける",
    "続きをやる",
    "tbd",
    "todo",
]

# 「次の一手」アンカー（コメント行 / 見出し / 太字のいずれでも拾う）
NEXT_ANCHOR = re.compile(r"次の一手|next action", re.I)

# 却下案の言い回し。bullet 単位で見る。
REJECTED = re.compile(
    r"却下|不採用|見送|採らな|採用しな|ではなく|代わりに|rejected|not chosen|"
    r"ruled out|discarded|instead of|rather than",
    re.I,
)

# 符号・桁区切り・小数・指数を扱う数値。日付 (2026-08-16) と時刻は除外済みの本文に当てる。
NUMBER = re.compile(r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.])"
                    r"|(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?![\w.])")
DATEISH = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?")


def split_sections(text: str) -> dict[str, str]:
    """'## 見出し' 単位で本文を切る。

    コードフェンスは CommonMark に合わせて扱う: ``` と ~~~ の両方、開始 fence と
    同じ文字・同じ長さ以上の行でのみ閉じる。
    """
    out: dict[str, str] = {}
    cur: str | None = None
    buf: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        m = re.match(r"(`{3,}|~{3,})", stripped)
        if m:
            marker = m.group(1)
            char, length = marker[0], len(marker)
            if fence is None:
                fence = (char, length)
            elif char == fence[0] and length >= fence[1] and not stripped[length:].strip():
                fence = None
        if fence is None and line.startswith("## "):
            if cur is not None:
                out[cur] = "\n".join(buf)
            cur = line[3:].strip()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf)
    return out


def strip_fences(body: str) -> str:
    """コードフェンスの中身を落とす（``` / ~~~ 両対応）。"""
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        m = re.match(r"(`{3,}|~{3,})", stripped)
        if m:
            marker = m.group(1)
            char, length = marker[0], len(marker)
            if fence is None:
                fence = (char, length)
                continue
            if char == fence[0] and length >= fence[1] and not stripped[length:].strip():
                fence = None
                continue
        if fence is None:
            out.append(line)
    return "\n".join(out)


def fenced_blocks(body: str) -> list[list[str]]:
    """コードフェンスの中身だけを塊で返す。"""
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    fence: tuple[str, int] | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        m = re.match(r"(`{3,}|~{3,})", stripped)
        if m:
            marker = m.group(1)
            char, length = marker[0], len(marker)
            if fence is None:
                fence, cur = (char, length), []
                continue
            if char == fence[0] and length >= fence[1] and not stripped[length:].strip():
                blocks.append(cur or [])
                fence, cur = None, None
                continue
        if cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    return blocks


BULLET = re.compile(r"^(\s*)(?:[-*]|\d+\.)\s+(\S.*)$")


def bullets(body: str, top_level_only: bool = False) -> list[str]:
    """箇条書きを返す。top_level_only=True ならインデント 0 のものだけ。"""
    out = []
    for line in strip_fences(body).splitlines():
        m = BULLET.match(line)
        if not m:
            continue
        if top_level_only and len(m.group(1)) > 0:
            continue
        out.append(m.group(2).strip())
    return out


def tried_entries(body: str) -> list[str]:
    """試行エントリを数える。トップレベル箇条書き、無ければ '### ' 見出し、
    無ければ表の本文行（区切り行とヘッダを除く）を 1 件と数える。"""
    top = bullets(body, top_level_only=True)
    if top:
        return top
    plain = strip_fences(body)
    heads = [l for l in plain.splitlines() if l.startswith("### ")]
    if heads:
        return heads
    rows = [l for l in plain.splitlines()
            if l.strip().startswith("|") and not re.match(r"^\s*\|[\s:|-]+\|\s*$", l)]
    return rows[1:] if len(rows) > 1 else []


def parse_headers(text: str) -> dict[str, str]:
    """フェンス外の '**Key:** value' を拾う。"""
    out: dict[str, str] = {}
    for line in strip_fences(text).splitlines():
        m = re.match(r"^\*\*([A-Za-z ]+):\*\*\s*(.*)$", line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2).strip()
    return out


def parse_chain(value: str) -> tuple[str, int] | None:
    m = re.match(r"`?([^`\s]+)`?\s*seq\s*`?(\d+)`?", value.strip())
    return (m.group(1), int(m.group(2))) if m else None


def find_parent(repo: str, file_dir: str, name: str) -> str | None:
    """親 handoff を、許可した handoff ディレクトリの中だけで探す。"""
    base = os.path.basename(name)
    cands = [os.path.join(file_dir, base)]
    cands += [os.path.join(repo, d, base) for d in HANDOFF_DIRS]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--min-lines", type=int, default=150,
                    help="行数下限（拡張 context なら 250、軽いセッションなら 80）")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        print(f"INPUT ERROR: no such file: {args.file}", file=sys.stderr)
        return 2
    if not os.path.isdir(args.repo):
        print(f"INPUT ERROR: no such repo dir: {args.repo}", file=sys.stderr)
        return 2

    # --repo は git working tree のトップでなければならない（launch root 誤指定を弾く）
    try:
        top = subprocess.run(["git", "-C", args.repo, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"INPUT ERROR: not a git working tree: {args.repo}", file=sys.stderr)
        return 2
    repo = os.path.realpath(top)
    if os.path.realpath(args.repo) != repo:
        print(f"INPUT ERROR: --repo is not the repo root (toplevel is {repo})", file=sys.stderr)
        return 2

    handoff = os.path.realpath(args.file)
    if os.path.commonpath([handoff, repo]) != repo:
        print(f"INPUT ERROR: handoff file is outside --repo: {handoff}", file=sys.stderr)
        return 2

    text = open(handoff, encoding="utf-8").read()
    lines = text.splitlines()
    sec = split_sections(text)
    hdr = parse_headers(text)
    results: list[tuple[str, bool, str]] = []

    def gate(name: str, ok: bool, msg: str = "") -> None:
        results.append((name, ok, msg))

    # G1 存在 / 非空
    gate("G1 file non-empty", len(text.strip()) > 0)

    # G2 必須ヘッダ（フェンス外・値つき。Date 形式と Status 値も見る）
    missing_hdr = [h for h in REQUIRED_HEADERS if not hdr.get(h)]
    prob = []
    if missing_hdr:
        prob.append("missing: " + ", ".join(missing_hdr))
    if hdr.get("Date") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", hdr["Date"]):
        prob.append(f"Date not YYYY-MM-DD: {hdr['Date']!r}")
    if hdr.get("Status") and hdr["Status"].upper() not in STATUS_VALUES:
        prob.append(f"Status not in {sorted(STATUS_VALUES)}: {hdr['Status']!r}")
    gate("G2 required headers", not prob, "; ".join(prob))

    # G3 必須節
    missing_sec = [s for s in REQUIRED_SECTIONS if s not in sec]
    gate("G3 required sections", not missing_sec, "missing: " + ", ".join(missing_sec))

    # G4 行数下限
    gate("G4 line count", len(lines) >= args.min_lines, f"{len(lines)} < {args.min_lines}")

    # G5 試行 3 件以上（トップレベル項目 / ### 見出し / 表の行で数える）
    tried = tried_entries(sec.get("What We Tried (Chronological)", ""))
    gate("G5 tried >= 3 entries", len(tried) >= 3, f"{len(tried)} entries")

    # G6 Evidence に実数（日付・時刻を除外、2 行以上に散っていること）
    ev = strip_fences(sec.get("Evidence & Data", ""))
    ev_lines = [DATEISH.sub(" ", l) for l in ev.splitlines()]
    nums, lines_with_nums = 0, 0
    for l in ev_lines:
        found = NUMBER.findall(l)
        if found:
            nums += len(found)
            lines_with_nums += 1
    ok6 = nums >= 3 and lines_with_nums >= 2
    gate("G6 evidence has numbers", ok6,
         f"{nums} numbers on {lines_with_nums} lines (need >=3 on >=2 lines; "
         f"write 'X -> Y', not 'improved')")

    # G7 却下案 — 同一 bullet 内に却下の言い回しがあること
    dec_bullets = bullets(sec.get("Key Decisions", ""))
    rejected = [b for b in dec_bullets if REJECTED.search(b)]
    gate("G7 decisions name a rejected alternative", bool(rejected),
         f"{len(dec_bullets)} decision bullets, none states a rejected alternative")

    # G8 ユーザ指示が非空
    fb = bullets(sec.get("User Feedback & Preferences", ""))
    gate("G8 user feedback non-empty", len(fb) >= 1, f"{len(fb)} items")

    # G9 chain 整合（親は handoff ディレクトリ内にのみ探し、tag と seq を照合）
    chain = parse_chain(hdr.get("Chain", ""))
    par = hdr.get("Parent", "").strip().strip("`")
    ok9, msg9 = False, "Chain/Parent header unparsable"
    if chain and par:
        tag, seq = chain
        if seq == 1:
            ok9 = par.lower().startswith("none")
            msg9 = "" if ok9 else f"seq 1 but Parent={par!r}"
        elif par.lower().startswith("none"):
            msg9 = f"seq {seq} but Parent=none"
        else:
            cand = par.split("—")[0].split(" - ")[0].strip().strip("`")
            path = find_parent(repo, os.path.dirname(handoff), cand)
            if not path:
                msg9 = f"parent not found in handoff dirs: {cand}"
            elif os.path.realpath(path) == handoff:
                msg9 = "Parent points at this file"
            else:
                ptext = open(path, encoding="utf-8").read()
                pchain = parse_chain(parse_headers(ptext).get("Chain", ""))
                if not pchain:
                    msg9 = f"parent has no parsable Chain header: {path}"
                elif pchain[0] != tag:
                    msg9 = f"parent chain {pchain[0]!r} != {tag!r}"
                elif pchain[1] != seq - 1:
                    msg9 = f"parent seq {pchain[1]} != {seq - 1}"
                else:
                    ok9 = True
                    msg9 = ""
    gate("G9 chain consistency", ok9, msg9)

    # G10 次の一手が具体的 — アンカー直後に実行可能な行があること
    qs = sec.get("Quick Start for Next Session", "")
    next_action = ""
    for block in fenced_blocks(qs) or [strip_fences(qs).splitlines()]:
        for i, l in enumerate(block):
            if NEXT_ANCHOR.search(l):
                rest = [x.strip() for x in block[i + 1:] if x.strip()]
                # アンカー行自体に書かれている場合も拾う
                inline = NEXT_ANCHOR.sub("", l).strip(" #*:：)-（）()").strip()
                nxt = [x for x in rest if not x.lstrip().startswith("#")]
                next_action = nxt[0] if nxt else inline
                break
        if next_action:
            break
    ok10 = bool(next_action) and not any(v in next_action.lower() for v in VAGUE_NEXT)
    gate("G10 concrete next action", ok10,
         "no '次の一手' / 'Next action' anchor with a concrete command below it"
         if not next_action else f"placeholder next action: {next_action!r}")

    # G11 貼り付けプロンプトが本文に記録されている
    closed = "Session Closed" in sec
    has_prompt = bool(re.search(r"seq\s*`?\d+`?", text)) and (
        "Paste" in text or "貼り" in text or "Next Session Prompt" in text)
    gate("G11 paste prompt recorded (only when session closed)",
         (not closed) or has_prompt,
         "Session Closed present but no paste prompt recorded in the file")

    width = max(len(n) for n, _o, _m in results)
    failed = 0
    for name, ok, msg in results:
        if not ok:
            failed += 1
        detail = msg if not ok else ""
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<{width}}  {detail}".rstrip())
    print(f"\n{len(results) - failed}/{len(results)} gates passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
