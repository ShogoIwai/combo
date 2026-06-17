#!/usr/bin/env python3
"""MCP server: fork a task from Claude Code into a one-shot Codex (GPT-5.5) run,
scoped to a single repository as its sandbox.

Why this exists
---------------
The harness launch root (`~/rep`) is **not** one git repo -- it holds many
independently-cloned repositories side by side. That layout breaks two things for
Codex:

  1. Running Codex from the root confuses its git scope (the stop-review-gate
     `rg .` fallback, the "operate at the second level" rule, etc.).
  2. Codex's own cloud/local sessions cannot share working context (the resume
     picker will not merge a cloud session with a local-profile one).

Forking *from Claude Code* sidesteps both: Claude Code holds the real context and
hands Codex a self-contained task, and every fork is pinned to exactly one repo
via `codex exec -C <repo>`. That single repo *is* the sandbox -- Codex never sees
the multi-repo root, so the side-by-side-clones constraint simply does not apply,
and because each call is a fresh stateless `codex exec` there is no session to
share or merge.

Beyond forking code tasks, this server also provides an external-access tool:
`web_rag` answers questions with live web search (`codex exec -c tools.web_search=true`),
reusing the same one-shot `codex exec` fork mechanism.

Auth/model: reuses the Codex login already on the host (`~/.codex`). The model is
whatever Codex is configured to use (GPT-5.5 by default) unless CODEX_MODEL pins
one. This is the *cloud* Codex path; it does not read OPENAI_* from source_local.

Only the Python standard library + FastMCP are used.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

# Codex CLI binary. nvm-installed Codex may not be on the MCP host's PATH, so
# resolve it explicitly and allow an override.
CODEX_BIN = (
    os.environ.get("CODEX_BIN")
    or shutil.which("codex")
    or os.path.expanduser("~/.nvm/versions/node/v24.14.1/bin/codex")
)

# Base directory the `repo` argument is resolved against. Each fork is pinned to
# one repo under this base, which becomes Codex's whole world (the sandbox).
FORK_BASE = os.environ.get("CODEX_FORK_BASE") or os.path.expanduser("~/rep")

# Default repo when a tool call omits `repo` (relative to FORK_BASE or absolute).
FORK_DEFAULT_REPO = os.environ.get("CODEX_FORK_DEFAULT_REPO", "")

# Model pin. Empty -> use Codex's configured default (GPT-5.5).
CODEX_MODEL = os.environ.get("CODEX_MODEL", "") or None

# Wall-clock cap for one fork (seconds). Codex tasks can be long; default 30 min.
TIMEOUT = int(os.environ.get("CODEX_FORK_TIMEOUT", "1800"))

# Usage log: one JSONL record per call, same schema as the other servers
# so usage_report.py can aggregate all sources.
USAGE_LOG = (
    os.environ.get("CODEX_FORK_USAGE_LOG")
    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "usage_codex.log")
)

mcp = FastMCP("codex")


def _resolve_repo(repo: str) -> str:
    """Resolve `repo` to an absolute path under FORK_BASE (or accept an absolute
    path as-is). Empty -> FORK_DEFAULT_REPO -> FORK_BASE."""
    repo = (repo or FORK_DEFAULT_REPO or "").strip()
    if not repo:
        return FORK_BASE
    if os.path.isabs(repo):
        return repo
    return os.path.join(FORK_BASE, repo)


def _run_codex(task: str, repo: str, sandbox: str, tool: str,
               search: bool = False) -> str:
    """Run one non-interactive `codex exec` pinned to `repo` and return the
    agent's last message.

    `-C <repo>` makes that single repo the working root (the sandbox); `-s`
    selects the sandbox policy; `--skip-git-repo-check` lets a non-git target
    still run. `search=True` turns on the native Responses `web_search` tool (via
    the `tools.web_search=true` config override) so Codex can ground its answer
    on live web results. stdin
    is detached because, when spawned by an MCP host, our stdin is the JSON-RPC
    pipe and Codex would otherwise block on / steal it.
    """
    workdir = _resolve_repo(repo)
    if not os.path.isdir(workdir):
        return f"[codex error] repo not found: {workdir}"

    cmd = [CODEX_BIN, "exec", "-C", workdir, "-s", sandbox, "--skip-git-repo-check"]
    if search:
        # `codex exec` has no --search flag (that is the interactive `codex`
        # flag); enable the native web_search tool via a config override instead.
        cmd += ["-c", "tools.web_search=true"]
    if CODEX_MODEL:
        cmd += ["-m", CODEX_MODEL]
    cmd += [task]

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
        _log(tool, repo, sandbox, len(task), time.monotonic() - started, "timeout")
        return f"[codex error] codex exec timed out after {TIMEOUT}s in {workdir}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    _log(tool, repo, sandbox, len(task), time.monotonic() - started, f"rc={proc.returncode}")

    if proc.returncode != 0 and not out:
        tail = err[-1000:] if err else "(no stderr)"
        return f"[codex error] codex exited {proc.returncode}: {tail}"
    return out or "[codex error] empty response"


def _log(tool, repo, sandbox, input_chars, latency_s, status) -> None:
    """Append a JSONL record matching the other servers' schema. Never raise."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "codex",
            "tool": tool,
            "model": CODEX_MODEL or "codex-default",
            "repo": repo or FORK_DEFAULT_REPO or "(base)",
            "sandbox": sandbox,
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
def fork_to_codex(task: str, repo: str = "", sandbox: str = "workspace-write") -> str:
    """Fork a self-contained coding task to Codex (GPT-5.5), sandboxed to one repo.

    Use this to hand off a concrete, bounded piece of work (implement X, refactor
    Y, fix the failing test in Z) that should run *inside a single repository*.
    Codex sees only `repo` as its working root, so the multi-repo launch root is
    irrelevant. The call is one-shot and stateless -- put everything Codex needs
    in `task`; it cannot see this conversation.

    Args:
        task:    Full, self-contained instructions for Codex.
        repo:    Target repo, relative to CODEX_FORK_BASE (~/rep) or absolute.
                 This repo is the sandbox. Empty -> the configured default.
        sandbox: Codex sandbox policy: "workspace-write" (default; edits the
                 repo), "read-only" (analysis only), or "danger-full-access".
    """
    return _run_codex(task, repo=repo, sandbox=sandbox, tool="fork_to_codex")


@mcp.tool()
def ask_codex(question: str, repo: str = "") -> str:
    """Ask Codex (GPT-5.5) a read-only question about a repository.

    Same fork mechanism as `fork_to_codex` but pinned to the "read-only" sandbox:
    Codex may inspect files and run read commands inside `repo` but makes no
    edits. Use for code explanation, review, or "where/how is X done here".
    """
    return _run_codex(question, repo=repo, sandbox="read-only", tool="ask_codex")


@mcp.tool()
def web_rag(query: str, repo: str = "") -> str:
    """Answer a question using Codex (GPT-5.5) with live web search (grounded RAG).

    Use this whenever the answer depends on facts outside the model's own
    knowledge: anything post-cutoff, any "latest"/release/version/pricing claim,
    library/API docs, or any external fact you are not certain of. Codex runs its
    native `web_search` tool, reads the results, and returns an up-to-date answer
    with source URLs.

    The run is read-only -- Codex never edits the repo, it only searches and
    reasons. `repo` just supplies a working root for the fork (defaults apply).
    """
    grounded = (
        "Search the web for current, authoritative information and answer the "
        "following. Cite source URLs inline.\n\n" + query
    )
    return _run_codex(grounded, repo=repo, sandbox="read-only",
                      tool="web_rag", search=True)


if __name__ == "__main__":
    mcp.run()
