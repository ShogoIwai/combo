#!/usr/bin/env python3
"""MCP server: fork a task from this Claude Code session into a one-shot
`claude -p` (headless Claude Code) run, scoped to a single repository.

Why this exists / the billing angle
------------------------------------
Anthropic's 2026 "bucket" pricing splits usage in two:

  * Bucket 1 (subscription limit): interactive Claude Code in the terminal/IDE,
    Claude web, Cowork.
  * Bucket 2 (a separate monthly Agent SDK credit): **`claude -p`** and Agent SDK
    usage -- a monthly credit included with the plan (Pro $20 / Max5x $100 /
    Max20x $200), only billed at API rates after that credit is spent.

The trigger for Bucket 2 is simply *using `claude -p`* under the same
subscription account -- no separate API key. So forking work to `claude -p`
moves it off the shared subscription limit onto its own monthly credit枠, which
the built-in Agent/Task subagent cannot do (it stays on Bucket 1).

IMPORTANT (as of 2026-06-15): Anthropic **paused** the Bucket 2 rollout
("nothing has changed for now"), so today `claude -p` still draws from the
subscription limit and this fork has no billing advantage over the built-in
Agent yet. The moment Anthropic un-pauses, this same code automatically benefits
-- with zero changes -- because the fork already routes through `claude -p` on
the parent's own subscription account.

Therefore this server intentionally does NOT inject ANTHROPIC_API_KEY or a
separate config dir: doing so would switch the fork to *pure API token billing*
and forfeit the included Bucket 2 credit. It inherits the parent's auth as-is.

Like the codex fork, every call is one-shot/stateless and pinned to exactly one
repo (`cwd` + `--add-dir`), so the multi-repo launch root never leaks in -- put
everything the fork needs in `task`; it cannot see this conversation.

Only the Python standard library + FastMCP are used. Usage is logged with the
same JSONL schema as the other servers so usage_report.py can aggregate all
sources.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

# Claude Code CLI binary. nvm-installed `claude` may not be on the MCP host's
# PATH, so resolve it explicitly and allow an override.
CLAUDE_BIN = (
    os.environ.get("CLAUDE_BIN")
    or shutil.which("claude")
    or os.path.expanduser("~/.nvm/versions/node/v24.14.1/bin/claude")
)

# Base directory the `repo` argument is resolved against.
FORK_BASE = os.environ.get("CLAUDE_FORK_BASE") or os.path.expanduser("~/rep")

# Default repo when a tool call omits `repo` (relative to FORK_BASE or absolute).
FORK_DEFAULT_REPO = os.environ.get("CLAUDE_FORK_DEFAULT_REPO", "")

# Model pin. Empty -> use the CLI's configured default. Aliases like "haiku"
# or "sonnet" are accepted by `--model`.
CLAUDE_MODEL = os.environ.get("CLAUDE_FORK_MODEL", "") or None

# Wall-clock cap for one fork (seconds). Default 30 min, matching the codex fork.
TIMEOUT = int(os.environ.get("CLAUDE_FORK_TIMEOUT", "1800"))

USAGE_LOG = (
    os.environ.get("CLAUDE_FORK_USAGE_LOG")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_claude.log")
)

mcp = FastMCP("claude")


def _resolve_repo(repo: str) -> str:
    """Resolve `repo` to an absolute path under FORK_BASE (or accept an absolute
    path as-is). Empty -> FORK_DEFAULT_REPO -> FORK_BASE."""
    repo = (repo or FORK_DEFAULT_REPO or "").strip()
    if not repo:
        return FORK_BASE
    if os.path.isabs(repo):
        return repo
    return os.path.join(FORK_BASE, repo)


def _run_claude(task: str, repo: str, permission_mode: str, tool: str,
                allowed_tools: list | None = None) -> str:
    """Run one headless `claude -p` pinned to `repo` and return its printed
    response.

    Inherits the parent's auth unchanged so the run lands on the same
    subscription account's Bucket 2 (`claude -p`) credit once that枠 is active.
    `cwd=<repo>` + `--add-dir <repo>` make that single repo the working root;
    `--permission-mode` selects autonomy (`plan` = read-only, `acceptEdits` =
    may edit). stdin is detached because, when spawned by an MCP host, our stdin
    is the JSON-RPC pipe and the child would otherwise block on / steal it.

    `allowed_tools` (optional) is passed to `--allowedTools` so specific tools
    run without a permission prompt -- needed because `--permission-mode plan`
    still gates WebSearch/WebFetch, which would otherwise be denied in the
    non-interactive `-p` run.
    """
    workdir = _resolve_repo(repo)
    if not os.path.isdir(workdir):
        return f"[claude error] repo not found: {workdir}"

    cmd = [
        CLAUDE_BIN, "-p", task,
        "--add-dir", workdir,
        "--permission-mode", permission_mode,
        "--output-format", "text",
    ]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]

    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        _log(tool, repo, permission_mode, len(task), time.monotonic() - started, "timeout")
        return f"[claude error] claude -p timed out after {TIMEOUT}s in {workdir}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    _log(tool, repo, permission_mode, len(task), time.monotonic() - started, f"rc={proc.returncode}")

    if proc.returncode != 0 and not out:
        tail = err[-1000:] if err else "(no stderr)"
        return f"[claude error] claude exited {proc.returncode}: {tail}"
    return out or "[claude error] empty response"


def _log(tool, repo, permission_mode, input_chars, latency_s, status) -> None:
    """Append a JSONL record matching the other servers' schema. Never raise."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "claude",
            "tool": tool,
            "model": CLAUDE_MODEL or "claude-default",
            "repo": repo or FORK_DEFAULT_REPO or "(base)",
            "sandbox": permission_mode,
            "input_chars": input_chars,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "latency_s": round(latency_s, 3),
            "status": status,
        }
        with open(USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


@mcp.tool()
def fork_to_claude(task: str, repo: str = "", permission_mode: str = "acceptEdits") -> str:
    """Fork a self-contained coding task to a separate headless `claude -p`,
    sandboxed to one repo.

    Runs on the parent's own subscription account but through `claude -p`, so the
    usage routes to the plan's separate Agent SDK credit (Bucket 2) rather than
    the shared interactive limit -- once Anthropic activates that枠 (currently
    paused; see module docstring). The fork sees only `repo` as its working root,
    so the multi-repo launch root is irrelevant. The call is one-shot and
    stateless -- put everything the fork needs in `task`; it cannot see this
    conversation.

    Prefer the built-in Agent/Task tool when you want a subagent on the shared
    subscription limit; use this to push work onto the separate `claude -p`枠.

    Args:
        task:            Full, self-contained instructions for the fork.
        repo:            Target repo, relative to CLAUDE_FORK_BASE (~/rep) or absolute.
        permission_mode: "acceptEdits" (default; may edit the repo) or "plan"
                         (read-only analysis).
    """
    return _run_claude(task, repo=repo, permission_mode=permission_mode, tool="fork_to_claude")


@mcp.tool()
def ask_claude(question: str, repo: str = "") -> str:
    """Ask a separate headless `claude -p` a read-only question about a repo.

    Same fork mechanism as `fork_to_claude` but pinned to `--permission-mode plan`:
    the fork may inspect files but makes no edits. Use for code explanation,
    review, or "where/how is X done here" on the separate `claude -p`枠.
    """
    return _run_claude(question, repo=repo, permission_mode="plan", tool="ask_claude")


@mcp.tool()
def web_rag(query: str, repo: str = "") -> str:
    """Answer a question using a headless `claude -p` with live web search (grounded RAG).

    Use this whenever the answer depends on facts outside the model's own
    knowledge: anything post-cutoff, any "latest"/release/version/pricing claim,
    library/API docs, or any external fact you are not certain of. The fork uses
    Claude Code's built-in WebSearch/WebFetch tools, reads the results, and returns
    an up-to-date answer with source URLs.

    The run is read-only (`--permission-mode plan`) -- the fork never edits the
    repo, it only searches and reasons. `repo` just supplies a working root for the
    fork (defaults apply).
    """
    grounded = (
        "Search the web for current, authoritative information and answer the "
        "following. Cite source URLs inline.\n\n" + query
    )
    return _run_claude(grounded, repo=repo, permission_mode="plan", tool="web_rag",
                       allowed_tools=["WebSearch", "WebFetch"])


if __name__ == "__main__":
    mcp.run()
