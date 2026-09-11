#!/usr/bin/env python3
"""Notion ページ本文 (Markdown) を、表題から決定論的に決めたファイル名で書き出す。

    write_page.py --title "ページ表題" [--outdir DIR] [--source URL] (--body-file F | --stdin)

- ファイル名は必ず表題から作る (`<sanitize(表題)>.md`)。名前を直接指定する手段は
  持たない — 「ファイル名 = ページ表題」がこの skill の契約なので、手で別名を
  与えられると契約が崩れるため。
- --outdir 省略時は、起動時 CWD 直下の work/ (絶対パスに固定し、無ければ作る)。
- H1 と出所コメントはこのスクリプトが決定論的に付ける。呼び出し側は MCP の
  取得結果をそのまま渡す (要約・書き換えをしない)。
- 検証は「書く前」に行い、検証を通ったものだけを一時ファイル経由で原子的に置く。
- 既存ファイルがあるときは**書かずに exit 4** で知らせる。上書きしてよいかは
  人が決めることなので、スクリプトは勝手に決めない。ユーザーが OK と言ったら
  --force を付けて同じコマンドを再実行する (そのときは WARNING を出して上書き)。

exit code:
    0 検証済みで書き出した / 1 I/O 失敗 / 2 引数不正 / 3 内容検証の失敗
    4 出力先に既存ファイルがある (--force が要る。何も書いていない)
"""
import argparse
import os
import re
import sys
import tempfile
import time
import unicodedata

EX_OK, EX_IO, EX_ARG, EX_CONTENT, EX_EXISTS = 0, 1, 2, 3, 4

DEFAULT_OUTDIR_NAME = "work"

# ファイル名に使えない / 使うと事故る文字。全角・空白はそのまま残す。
_BAD = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
# Windows の予約名 (拡張子を付けても予約されたまま)
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def die(code: int, msg: str):
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def sanitize(title: str):
    """表題 -> ファイル名。戻り値は (ファイル名, 施した変換の説明リスト)。"""
    notes = []
    name = unicodedata.normalize("NFC", title)
    if name != title:
        notes.append("Unicode NFC 正規化")
    name = name.strip()
    if not name:
        die(EX_CONTENT, "表題が空でファイル名を作れない")

    sub = _BAD.sub("_", name)
    if sub != name:
        notes.append("ファイル名に使えない文字を _ に置換")
    name = sub

    # 末尾の "." と空白は OS 側で落ちることがあるので決定論的に固定する
    stripped = name.rstrip(" .")
    if stripped != name:
        notes.append("末尾の空白/ピリオドを除去")
        name = stripped
    # "." ".." だけ、または除去で空になった場合の決定論的な退避
    if name in ("", ".", ".."):
        name = "_" + ("" if not name else name)
        notes.append("パス上の特殊名を _ 付きへ退避")
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
        notes.append("Windows 予約名を _ 付きへ退避")

    # ext4 の 255 byte 制限。".md" の 3 byte を残して UTF-8 安全に切る
    b = name.encode("utf-8")
    if len(b) > 255 - 3:
        name = b[: 255 - 3].decode("utf-8", "ignore").rstrip(" .")
        notes.append("255 byte に収まるよう表題を切り詰め")
    return name + ".md", notes


def build(title: str, body: str, source: str | None) -> str:
    """H1 と出所コメントを決定論的に付けた最終本文を返す。"""
    lines = body.replace("\r\n", "\n").split("\n")
    # 取得結果が既に同じ H1 を持つなら重複させない (その 1 行だけを取り除く)
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# ") and lines[i][2:].strip() == title.strip():
        del lines[: i + 1]
    body = "\n".join(lines).strip("\n")

    head = [f"# {title.strip()}"]
    if source:
        head.append(f"<!-- source: {source} -->")
    out = "\n".join(head) + "\n\n" + body
    return out.rstrip("\n") + "\n"


def verify(text: str, title: str, source: str | None):
    """書く前の内容検証。問題があれば理由のリストを返す。"""
    bad = []
    lines = text.split("\n")
    if lines[0] != f"# {title.strip()}":
        bad.append(f"先頭行が `# <表題>` でない: {lines[0][:60]!r}")
    if source and lines[1] != f"<!-- source: {source} -->":
        bad.append("2 行目の出所コメントが期待形と違う")
    # H1 と出所コメントを除いた実体が残っているか
    rest = "\n".join(lines[2 if source else 1:]).strip()
    if not rest:
        bad.append("本文が空 (H1 と出所コメントしかない)")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="Notion ページの表題 (= ファイル名の元)")
    ap.add_argument("--outdir", help="出力ディレクトリ (既定: CWD 直下の work/)")
    ap.add_argument("--source", help="出所として本文に埋め込む Notion URL")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body-file", help="MCP 取得結果の Markdown ファイル")
    src.add_argument("--stdin", action="store_true", help="取得結果を標準入力から読む")
    ap.add_argument("--force", action="store_true",
                    help="既存ファイルを上書きする (ユーザーが上書きを了承したときだけ)")
    ap.add_argument("--allow-empty-page", action="store_true",
                    help="元ページが本当に空だと確認できたときだけ、空本文を許す")
    a = ap.parse_args()

    if not a.title.strip():
        die(EX_ARG, "--title が空")
    if a.outdir is not None and not a.outdir.strip():
        die(EX_ARG, "--outdir が空文字")

    # 出力先: 既定は「起動時 CWD の work/」を絶対パスに固定する
    # (以後 cd されても保存先が動かないようにするため)
    default_outdir = a.outdir is None
    outdir = os.path.abspath(a.outdir if a.outdir else DEFAULT_OUTDIR_NAME)
    if not os.path.isdir(outdir):
        if default_outdir:
            try:
                os.makedirs(outdir, exist_ok=True)
            except OSError as e:
                die(EX_IO, f"既定の出力先を作れない: {outdir} ({e})")
        else:
            die(EX_ARG, f"出力先ディレクトリが無い: {outdir}")

    try:
        body = open(a.body_file, encoding="utf-8").read() if a.body_file else sys.stdin.read()
    except OSError as e:
        die(EX_IO, f"本文を読めない: {e}")
    if not body.strip() and not a.allow_empty_page:
        die(EX_CONTENT, "取得結果が空。Notion の取得に失敗しているか、"
                        "本当に空ページなら --allow-empty-page を付ける")

    fname, notes = sanitize(a.title)
    path = os.path.join(outdir, fname)

    text = build(a.title, body, a.source)
    bad = verify(text, a.title, a.source)
    if bad and not (a.allow_empty_page and bad == ["本文が空 (H1 と出所コメントしかない)"]):
        for b in bad:
            print(f"ERROR: 内容検証 NG: {b}", file=sys.stderr)
        die(EX_CONTENT, "検証を通らなかったので何も書いていない")

    existed = os.path.lexists(path)
    if existed and not a.force:
        print(f"ALERT: 出力先に既存ファイルがある: {path}", file=sys.stderr)
        try:   # リンク切れのシンボリックリンク等、stat できない既存物もある
            st = os.stat(path)
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
            print(f"ALERT: 既存 {st.st_size} bytes, 更新日時 {mtime}", file=sys.stderr)
        except OSError:
            print("ALERT: 既存物の情報を読めない (リンク切れ等)", file=sys.stderr)
        die(EX_EXISTS, "上書きしてよいかユーザーに確認し、了承されたら --force を付けて再実行する"
                       " (何も書いていない)")
    try:
        fd, tmp = tempfile.mkstemp(dir=outdir, prefix=".write_page.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)          # 同一ディレクトリ内なので原子的
    except OSError as e:
        die(EX_IO, f"書き出しに失敗: {e}")

    # 書き戻して全文一致を確認する (途中で欠けていないこと)
    try:
        back = open(path, encoding="utf-8").read()
    except OSError as e:
        die(EX_IO, f"書き戻し確認に失敗: {e}")
    if back != text:
        die(EX_CONTENT, f"書き戻しが一致しない: {path}")

    for n in notes:
        print(f"NOTE: ファイル名: {n}", file=sys.stderr)
    if existed:
        print(f"WARNING: 既存ファイルを上書きした (--force): {path}", file=sys.stderr)
    print(f"written: {path} ({os.path.getsize(path)} bytes) verified", file=sys.stderr)
    print(path)
    return EX_OK


if __name__ == "__main__":
    sys.exit(main())
