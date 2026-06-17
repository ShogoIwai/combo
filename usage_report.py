#!/usr/bin/env python3
"""Monitor the Claude Code <-> Codex cross-fork MCP traffic.

Both directions of the combo log one JSONL record per MCP call, same schema:

  mcp_codex.py  (Claude Code -> cloud Codex)  -> usage_codex.log
  mcp_claude.py (Codex/CC    -> cloud claude -p) -> usage_claude.log

Record fields: ts, source ("codex"/"claude"), tool, repo, sandbox, input_chars,
latency_s, status (e.g. "rc=0", "timeout", "rc=1"). Neither `codex exec` nor
`claude -p` reports token counts, so the legacy prompt/completion/total columns
were dead weight -- this report drops them entirely and is built around what the
two forks actually emit: who called whom, on which repo, how long it took, and
whether it succeeded.

Two views:

  * aggregate (default) -- one row per group with calls / ok / err / chars /
    latency stats; the at-a-glance health table.
  * stream  (--tail N / --watch) -- the most recent calls, one per line, for
    live monitoring of forks as they land.

Examples:
    python3 usage_report.py                      # aggregate by source / tool
    python3 usage_report.py --by repo            # which repos get forked into
    python3 usage_report.py --by day             # per-day volume
    python3 usage_report.py --tail 20            # last 20 calls, newest last
    python3 usage_report.py --watch              # live tail, refresh every 3s
    python3 usage_report.py --watch 10 --tail 30 # refresh every 10s, 30 rows
    python3 usage_report.py --json               # machine-readable aggregate
    python3 usage_report.py usage_codex.log      # one explicit log
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

DEFAULT_LOGS = ["usage_codex.log", "usage_claude.log"]

# A call counts as a success only when the fork exited 0. Everything else
# (timeout, rc=N, error string) is an error.
OK_STATUS = "rc=0"

# Compact sandbox / permission_mode labels for the stream view.
SANDBOX_ABBR = {
    "workspace-write": "ww",
    "read-only": "ro",
    "danger-full-access": "danger",
    "acceptEdits": "edit",
    "plan": "plan",
}


def _source_from_path(path):
    base = os.path.basename(path)
    if base.startswith("usage_") and base.endswith(".log"):
        return base[len("usage_"):-len(".log")]
    return base


def _load(path):
    rows = []
    fallback = _source_from_path(path)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row.setdefault("source", fallback)
            rows.append(row)
    return rows


def _load_all(paths):
    rows, found = [], []
    for path in paths:
        if os.path.exists(path):
            rows.extend(_load(path))
            found.append(path)
    return rows, found


def _key(row, by):
    day = (row.get("ts") or "")[:10]
    tool = row.get("tool") or "?"
    source = row.get("source") or "?"
    repo = row.get("repo") or "?"
    parts = {
        "source": (source,),
        "tool": (tool,),
        "repo": (repo,),
        "day": (day,),
        "source-tool": (source, tool),
        "source-repo": (source, repo),
        "day-source": (day, source),
    }
    return parts[by]


def _agg(rows, by):
    buckets = defaultdict(
        lambda: {"calls": 0, "ok": 0, "err": 0, "chars": 0,
                 "lat": 0.0, "max": 0.0}
    )
    for r in rows:
        b = buckets[_key(r, by)]
        b["calls"] += 1
        if r.get("status") == OK_STATUS:
            b["ok"] += 1
        else:
            b["err"] += 1
        b["chars"] += r.get("input_chars") or 0
        lat = r.get("latency_s") or 0.0
        b["lat"] += lat
        b["max"] = max(b["max"], lat)
    return buckets


def _print_aggregate(buckets, by):
    hdr_key = by.split("-")
    keyw = max(
        [len(" / ".join(map(str, k))) for k in buckets] + [len(" / ".join(hdr_key))]
    )
    cols = f"{'calls':>6}  {'ok':>4}  {'err':>4}  {'in_kc':>7}  {'lat_s':>9}  {'avg_s':>7}  {'max_s':>7}"
    print(f"{' / '.join(hdr_key):<{keyw}}  {cols}")
    print("-" * (keyw + 2 + len(cols)))
    tot = {"calls": 0, "ok": 0, "err": 0, "chars": 0, "lat": 0.0, "max": 0.0}
    for k, v in sorted(buckets.items()):
        label = " / ".join(map(str, k))
        avg = v["lat"] / v["calls"] if v["calls"] else 0.0
        print(
            f"{label:<{keyw}}  {v['calls']:>6}  {v['ok']:>4}  {v['err']:>4}  "
            f"{v['chars'] / 1000:>7.1f}  {v['lat']:>9.1f}  {avg:>7.1f}  {v['max']:>7.1f}"
        )
        for f in ("calls", "ok", "err", "chars", "lat"):
            tot[f] += v[f]
        tot["max"] = max(tot["max"], v["max"])
    print("-" * (keyw + 2 + len(cols)))
    avg = tot["lat"] / tot["calls"] if tot["calls"] else 0.0
    print(
        f"{'TOTAL':<{keyw}}  {tot['calls']:>6}  {tot['ok']:>4}  {tot['err']:>4}  "
        f"{tot['chars'] / 1000:>7.1f}  {tot['lat']:>9.1f}  {avg:>7.1f}  {tot['max']:>7.1f}"
    )


def _print_stream(rows, n):
    rows = sorted(rows, key=lambda r: r.get("ts") or "")[-n:]
    print(
        f"{'ts (utc)':<20}  {'src':<6}  {'tool':<15}  {'repo':<18}  "
        f"{'sb':<6}  {'in_kc':>6}  {'lat_s':>8}  status"
    )
    print("-" * 96)
    for r in rows:
        ts = (r.get("ts") or "")[:19].replace("T", " ")
        src = (r.get("source") or "?")[:6]
        tool = (r.get("tool") or "?")[:15]
        repo = os.path.basename((r.get("repo") or "?").rstrip("/"))[:18]
        sb = SANDBOX_ABBR.get(r.get("sandbox"), (r.get("sandbox") or "?")[:6])
        kc = (r.get("input_chars") or 0) / 1000
        lat = r.get("latency_s") or 0.0
        status = r.get("status") or "?"
        mark = "" if status == OK_STATUS else "  <-- ERR"
        print(
            f"{ts:<20}  {src:<6}  {tool:<15}  {repo:<18}  "
            f"{sb:<6}  {kc:>6.1f}  {lat:>8.1f}  {status}{mark}"
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument(
        "logfiles", nargs="*",
        default=[os.path.join(here, n) for n in DEFAULT_LOGS],
    )
    ap.add_argument(
        "--by",
        choices=["source", "tool", "repo", "day",
                 "source-tool", "source-repo", "day-source"],
        default="source-tool",
    )
    ap.add_argument("--tail", type=int, metavar="N",
                    help="stream the last N calls instead of the aggregate")
    ap.add_argument("--watch", nargs="?", type=int, const=3, metavar="SECS",
                    help="re-render every SECS seconds (default 3); Ctrl-C to stop")
    ap.add_argument("--json", action="store_true",
                    help="emit the aggregate as JSON and exit")
    args = ap.parse_args()

    def render():
        rows, found = _load_all(args.logfiles)
        if not found:
            print("no usage logs found: " + ", ".join(args.logfiles), file=sys.stderr)
            return 1
        if not rows:
            print("usage logs are empty", file=sys.stderr)
            return 1
        if args.json:
            buckets = _agg(rows, args.by)
            out = [{"key": list(k), **v} for k, v in sorted(buckets.items())]
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif args.tail is not None or args.watch is not None:
            _print_stream(rows, args.tail or 20)
        else:
            _print_aggregate(_agg(rows, args.by), args.by)
        return 0

    if args.watch is None:
        return render()

    try:
        while True:
            sys.stdout.write("\x1b[2J\x1b[H")  # clear + home
            print(f"combo MCP monitor — {time.strftime('%Y-%m-%d %H:%M:%S')} "
                  f"(every {args.watch}s, Ctrl-C to stop)\n")
            render()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
