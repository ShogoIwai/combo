# Combo — Claude Code ⇄ Codex cross-fork coordination

`combo/` is the **cloud-only** coordination layer between the two cloud coding
agents on this host — **Claude Code** (Anthropic) and **Codex** (OpenAI GPT-5.5).
It lets either agent hand a whole, self-contained task to the other and pull the
result back, so each can lean on the other where it is stronger. It also serves as
the **single source of truth for skills shared by both harnesses** — reusable
on-demand procedures kept once under `combo/skills/` and symlinked into each
harness's search path (see [Skill sharing across both harnesses](#skill-sharing-across-both-harnesses)).

It is extracted from `ollama/`, keeping **only** the cross-agent fork machinery.
Everything Ollama-specific (local model launchers, `source_local`/`source_cloud`
static switching, the deprecated `localllm` bridge, VRAM/context tuning) is left
behind in `ollama/` — local-LLM performance was not good enough to keep it on the
critical path, so this directory assumes **both agents run in the cloud**.

The whole design rests on five facts about this environment:

1. **Both agents must handle a multi-repo workspace.** The launch root (`<launch root>`)
   is **not one git repo** — it holds many independently-cloned repositories side
   by side (mirrored in the global `CLAUDE.md` / `AGENTS.md`). Neither agent can
   run git/search tooling at the root. Every fork is therefore **pinned to one
   repo** (`codex exec -C <repo>` / `claude -p` `cwd`+`--add-dir`), so the forked
   agent sees one real working tree instead of the root — **provided the call
   passes a real `repo` (or a default repo is configured)**; an empty `repo` with
   no default falls back to the launch root itself, so always pin one.
2. **Cross-session context is carried in Notion, not in the fork.** Each fork is
   one-shot and stateless — it cannot see the caller's conversation. Durable,
   cross-session working context is pooled in **Notion** and pulled back in as
   needed (see [Context carry-over via Notion](#context-carry-over-via-notion)).
3. **Each side registers only the MCP that reaches the *other* agent.** An agent
   never registers an MCP that forks back into itself — that would be a useless
   self-loop. Claude Code registers the **`codex`** server; Codex registers the
   **`claude`** server (see [Who registers what](#who-registers-what)).
4. **Every MCP call is logged and monitorable.** Both servers append one JSONL
   record per call so the cross-fork traffic can be watched live or summarized
   (see [Monitoring the traffic](#monitoring-the-traffic)).
5. **Skills are shared from one source of truth.** Reusable procedures live once
   under `combo/skills/` and are exposed to both harnesses by symlink, since
   neither auto-detects an arbitrary directory; names are prefixed by origin layer
   (`combo-` / `<repo>-` / `<harness>-`) to keep one flat namespace collision-free
   (see [Skill sharing across both harnesses](#skill-sharing-across-both-harnesses)).

---

## Directory contents

| File                 | Role                                                                                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `up_version.csh`   | Reinstall Claude Code + Codex CLIs to latest (`npm i -g @anthropic-ai/claude-code@latest @openai/codex@latest`).                                                                                     |
| `mcp_codex.py`     | MCP server giving**Claude Code** a fork into cloud **Codex** (`fork_to_codex` / `ask_codex`) + `web_rag`, each pinned to one repo as its sandbox.                                    |
| `mcp_claude.py`    | MCP server giving**Codex** (or another Claude Code) a fork into a one-shot headless **`claude -p`** (`fork_to_claude` / `ask_claude`) + `web_rag`.                                 |
| `usage_report.py`  | Monitor the cross-fork MCP traffic from the two JSONL logs — aggregate table or live stream (see[Monitoring the traffic](#monitoring-the-traffic)).                                                      |
| `skills/`          | **Shared skills** source of truth — one dir per skill (`combo-<name>/SKILL.md`), symlinked into both harnesses (see[Skill sharing across both harnesses](#skill-sharing-across-both-harnesses)). |
| `skills/link.sh`   | Idempotent bootstrap that symlinks `combo/skills` into each harness's search path (`.claude/skills`, `.agents/skills`).                                                                          |
| `usage_codex.log`  | JSONL usage records written by `mcp_codex.py` (gitignored).                                                                                                                                          |
| `usage_claude.log` | JSONL usage records written by `mcp_claude.py` (gitignored).                                                                                                                                         |

### Dependency

Both servers use FastMCP, so the `mcp` package must be importable by the **same
`python3`** the client launches; the fork calls themselves use only the standard
library (they shell out to the `codex` / `claude` CLIs).

```bash
python3 -m pip install --user mcp        # once, for the python3 on PATH
python3 -c 'import mcp'                   # verify (no output = OK)
```

> If the client runs a different interpreter, registration connects but tool
> calls fail with `ModuleNotFoundError: No module named 'mcp'`. Install `mcp` for
> that interpreter, or register with its absolute `python3` path.

---

## Multi-repo handling: each fork is pinned to one repo

The workspace is a launch root holding many side-by-side clones, not a single git
repo, so an agent running at the root has no valid git scope (and the old "`rg .`
fallback at the root" is the historical cause of orphaned `rg` processes). The
fork model removes that at the source: **every fork names exactly one repo as its
entire world.**

- **`mcp_codex.py`** runs `codex exec -C <repo> -s <sandbox> --skip-git-repo-check`
  — `-C <repo>` makes that one repository Codex's working root; *that repo is the
  sandbox* (the `web_rag` tool additionally passes `-c tools.web_search=true`).
- **`mcp_claude.py`** runs `claude -p` with that repo as `cwd` + `--add-dir` and a
  `--permission-mode`.

`repo` is resolved relative to the fork base (`CODEX_FORK_BASE` / `CLAUDE_FORK_BASE`,
default `<launch root>`) or accepts an absolute path. As long as the call points at a real
project — pass `repo`, or set `CODEX_FORK_DEFAULT_REPO` / `CLAUDE_FORK_DEFAULT_REPO`
— the forked agent never sees the root and the "operate at the second level or
deeper / git fails at the root" problem cannot arise.

> **Caveat:** an empty `repo` with **no** default configured resolves to the fork
> base (`<launch root>`) — i.e. the multi-repo launch root itself. The servers do not
> reject that, so always pass a `repo` or configure a default repo; otherwise the
> very layout this section avoids leaks back in.

Each call is **one-shot and stateless** — put everything the forked agent needs
into `task`/`question`; it cannot read the caller's conversation. That same
statelessness is also why Codex's cloud/local resume-list split is irrelevant
here: a fork carries no session, so there is nothing to merge.

---

## Context carry-over via Notion

A fork cannot see the caller's conversation, and the two agents keep entirely
separate session histories. So **durable, cross-session context lives in Notion**,
not in any single agent's transcript: write the shared state (decisions, current
status, facts the other side will need) to a Notion page, and have either agent
pull it back in at the start of a task. A fork that needs prior context is handed
the **Notion page ID/URL** in its `task` string and reads it through whichever
Notion access path that agent has.

The two agents reach Notion **differently**, and this asymmetry is the key setup
detail:

| Agent       | Notion access                                  | Setup needed                                                      |
| ----------- | ---------------------------------------------- | ----------------------------------------------------------------- |
| Claude Code | **Browser connector** (claude.ai)        | **None here** — already linked via the account's connector |
| Codex       | **Dedicated Notion MCP** in `~/.codex` | **Required** — register + OAuth (below)                    |

### Claude Code — no setup

The **interactive** Claude Code session (the caller that drives this coordination)
reaches Notion through the **browser connector** configured in the claude.ai
account, so there is **nothing to register in this directory** for the Claude side
— do not add a Notion MCP to Claude Code.

> **Scope of "no setup".** This covers the interactive Claude Code session only.
> The headless `claude -p` fork that `mcp_claude.py` spawns is a separate process
> (started with just `--add-dir` / `--permission-mode` / `--output-format text`);
> whether it inherits the account's Notion connector is environment-dependent and
> not established by this repo's code. So treat Notion as the **interactive
> caller's** job — do Notion reads/writes from the driving session (or via
> `ask_codex`), not from inside a `fork_to_claude` task.

### Codex — dedicated Notion MCP (required)

Codex has no such connector, so it needs the Notion MCP registered in its own
config. Register it once (adds `[mcp_servers.notion]` to `~/.codex/config.toml`),
then complete OAuth in the browser:

```bash
codex mcp add notion --url https://mcp.notion.com/mcp
codex mcp login notion        # OAuth in the browser; grants page access
```

> **Must auto-approve, OAuth connector only.** A fork runs `codex exec`
> **non-interactively** (`stdin` closed), so any MCP call that needs per-call
> approval is auto-**cancelled** (`user cancelled MCP tool call`). Set the OAuth
> `notion` connector to auto-approve in `~/.codex/config.toml`:
>
> ```toml
> [mcp_servers.notion]
> url = "https://mcp.notion.com/mcp"
> default_tools_approval_mode = "approve"   # values: auto | prompt | approve
> ```
>
> Without it, a Notion write through `ask_codex` returns `user cancelled MCP tool call`. Do **not** rely on the Bearer-token `codex_apps` managed connector — it
> lacks page access (`UNAUTHORIZED`); phrase the task to use the OAuth `notion`
> connector explicitly. Verified end-to-end (append + delete on a target page).

Once registered, Notion reads/writes are reachable by handing a Notion task to
`ask_codex` (e.g. "append section Z to page `<id>` with …"). Put the full intent
— target page (by **ID** is most reliable), title, exact content — in the
question.

---

## Who registers what

The rule: **register only the MCP that forks into the *other* agent; never
register your own.** A server that forks back into the same agent is a self-loop
with no purpose.

```bash
# In Claude Code: register ONLY the codex fork (reaches the other agent).
claude mcp add -s user codex python3 $REP/combo/mcp_codex.py

# In Codex: register ONLY the claude fork (reaches the other agent).
codex mcp add claude -- python3 $REP/combo/mcp_claude.py
```

- Claude Code → registers **`codex`** (`mcp_codex.py`). It does **not** register
  `claude` — forking Claude Code back into `claude -p` from Claude Code is a
  self-loop.
- Codex → registers **`claude`** (`mcp_claude.py`). It does **not** register
  `codex` for the same reason.

(`$REP` is the launch root, e.g. `<launch root>`. Verify with `/mcp` in Claude Code or
`codex mcp list`.)

### Tools exposed by each server

**`mcp_codex.py`** (Claude Code → cloud Codex):

| Tool                                   | Sandbox (default)   | Use for                                                                   |
| -------------------------------------- | ------------------- | ------------------------------------------------------------------------- |
| `fork_to_codex(task, repo, sandbox)` | `workspace-write` | Hand a bounded coding task to Codex; it edits the one repo.               |
| `ask_codex(question, repo)`          | `read-only`       | Repo question / review; also the Notion path (above).                     |
| `web_rag(query, repo)`               | `read-only`       | Live web search (`codex exec -c tools.web_search=true`), cited sources. |

**`mcp_claude.py`** (Codex → headless `claude -p`):

| Tool                                            | Permission mode (default) | Use for                                                         |
| ----------------------------------------------- | ------------------------- | --------------------------------------------------------------- |
| `fork_to_claude(task, repo, permission_mode)` | `acceptEdits`           | Hand a bounded coding task to `claude -p`; it edits the repo. |
| `ask_claude(question, repo)`                  | `plan`                  | Read-only question / review of a repo. No edits.                |
| `web_rag(query, repo)`                        | `plan`                  | Web grounding via Claude Code's built-in WebSearch/WebFetch.    |

> The `sandbox` / `permission_mode` column shows each tool's **default**;
> `fork_to_codex` and `fork_to_claude` pass a caller-supplied value straight
> through to the CLI without validation (`fork_to_codex` accepts `read-only`,
> `workspace-write`, `danger-full-access`). "read-only" / `plan` is read-only with
> respect to the **repo/filesystem only** — an external MCP tool such as an
> auto-approved Notion connector can still write (that is exactly how the Notion
> path through `ask_codex` works).

Each fork runs on the **cloud** model: `mcp_codex.py` reuses the Codex login
(`~/.codex`, GPT-5.5 by default) and `mcp_claude.py` inherits the parent Claude
Code auth as-is. `web_rag` is the external-access path — query it whenever an
answer depends on facts outside the model's knowledge (anything post-cutoff, any
"latest"/version/pricing claim) rather than guessing.

### Environment overrides

Both servers share the same shape of knobs (`CODEX_*` / `CLAUDE_*`):

| Codex var (`mcp_codex.py`) | Claude var (`mcp_claude.py`) | Default                     | Meaning                              |
| ---------------------------- | ------------------------------ | --------------------------- | ------------------------------------ |
| `CODEX_BIN`                | `CLAUDE_BIN`                 | CLI on PATH → nvm fallback | Agent CLI binary                     |
| `CODEX_FORK_BASE`          | `CLAUDE_FORK_BASE`           | `<launch root>`           | Base a relative `repo` resolves to |
| `CODEX_FORK_DEFAULT_REPO`  | `CLAUDE_FORK_DEFAULT_REPO`   | (empty)                     | Repo used when a call omits `repo` |
| `CODEX_MODEL`              | `CLAUDE_FORK_MODEL`          | (empty → CLI default)      | Pin the fork model                   |
| `CODEX_FORK_TIMEOUT`       | `CLAUDE_FORK_TIMEOUT`        | `1800`                    | Per-fork wall-clock cap (seconds)    |
| `CODEX_FORK_USAGE_LOG`     | `CLAUDE_FORK_USAGE_LOG`      | `combo/usage_*.log`       | JSONL usage log path                 |

---

## Claude Code rg-cleanup `Stop` hook (insurance)

With the fork model, neither agent runs search tooling at the multi-repo root, so
the old root-level `rg .` fallback that orphaned `rg` processes should not fire.
The `Stop` hook below is kept as **belt-and-suspenders only** — it costs nothing
and still reaps any stray `rg` from other sources when a Claude Code session ends.

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "MY_SID=$(ps -p $$ -o sid= 2>/dev/null | tr -d ' '); pgrep -u \"$(id -un)\" rg 2>/dev/null | while read p; do [ \"$(ps -p $p -o sid= 2>/dev/null | tr -d ' ')\" = \"$MY_SID\" ] && kill $p 2>/dev/null; done; true"
          }
        ]
      }
    ]
  }
}
```

The hook matches `rg` processes by **session ID (SID)**: SID is inherited at fork
and survives reparenting to init, so an orphaned `rg` still carries the Claude
Code session's SID.

> **Best-effort:** `rg` started from the *same terminal session* that launched
> Claude Code shares the SID and would also be killed. In practice intentional
> long-running `rg` in that terminal alongside an active session is rare, so the
> trade-off is acceptable.

---

## Monitoring the traffic

Every MCP call appends one JSONL record so the cross-fork traffic is observable.
Both servers write the **same schema**:

```json
{"ts":"…+00:00","source":"codex","tool":"fork_to_codex","model":"codex-default","repo":"…",
 "sandbox":"workspace-write","input_chars":4210,"prompt_tokens":null,"completion_tokens":null,
 "total_tokens":null,"latency_s":122.5,"status":"rc=0"}
```

`source` is `codex` or `claude` (which fork ran), `status` is `rc=0` on success
or `timeout` / `rc=N` on failure. The `prompt_tokens` / `completion_tokens` /
`total_tokens` fields are **always `null`** — neither `codex exec` nor `claude -p`
reports token counts — so the monitor format here is **redesigned from scratch
around what the forks actually emit** (who called whom, on which repo, how long,
success or not): `usage_report.py` **ignores those always-null token fields**
rather than printing dead columns. (`model` is recorded — `codex-default` /
`claude-default` unless `CODEX_MODEL` / `CLAUDE_FORK_MODEL` pins one — but the
report does not group on it.)

`usage_report.py` reads both logs (`usage_codex.log` + `usage_claude.log`) and has
two views:

**Aggregate (default)** — one row per group, the at-a-glance health table:

```bash
python3 usage_report.py                 # by source / tool (default)
python3 usage_report.py --by repo       # which repos get forked into most
python3 usage_report.py --by day        # per-day volume
python3 usage_report.py --by source     # codex vs claude totals
python3 usage_report.py --json          # machine-readable
```

All `--by` groupings: `source`, `tool`, `repo`, `day`, `source-tool` (default),
`source-repo`, `day-source`.

```
source / tool             calls    ok   err    in_kc      lat_s    avg_s    max_s
---------------------------------------------------------------------------------
claude / fork_to_claude       1     1     0      3.1      142.0    142.0    142.0
codex / ask_codex             1     1     0      0.9       33.2     33.2     33.2
codex / fork_to_codex         1     1     0      4.2      122.5    122.5    122.5
codex / web_rag               1     0     1      0.1      430.2    430.2    430.2
---------------------------------------------------------------------------------
TOTAL                         4     3     1      8.3      727.9    182.0    430.2
```

Columns: `calls` total, `ok`/`err` split (success = exit 0), `in_kc` input
kilochars, and latency `sum / avg / max` in seconds — latency and error rate are
what matter for cloud forks, since tokens are unavailable.

**Stream / live monitor** — the most recent calls, one per line, errors flagged:

```bash
python3 usage_report.py --tail 20       # last 20 calls, newest last
python3 usage_report.py --watch         # live, refresh every 3s (Ctrl-C stops)
python3 usage_report.py --watch 10 --tail 30   # every 10s, 30 rows
```

```
ts (utc)              src     tool             repo                sb       in_kc     lat_s  status
------------------------------------------------------------------------------------------------
2026-06-17 08:31:02   codex   fork_to_codex    app-backend         ww         4.2     122.5  rc=0
2026-06-17 09:02:55   codex   web_rag          combo               ro         0.1     430.2  timeout  <-- ERR
```

`--watch` clears and re-renders on an interval for a live dashboard of forks as
they land; `sb` is the abbreviated sandbox / permission_mode (`ww`=workspace-write,
`ro`=read-only, `danger`=danger-full-access, `edit`=acceptEdits, `plan`).

---

## Skill sharing across both harnesses

> **Read this section before creating, editing, or sharing any skill**
> (referenced from the global `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md`). It is the
> single source of truth for skill layout, symlinking, naming, and the
> rule-vs-skill-vs-MCP split across both harnesses.

Skills (reusable, on-demand procedures — RTL review, sim-debug runbooks, release
notes, Notion research) can be **shared** by both agents from a single source of
truth in `combo/`, while harness- or repo-specific procedures stay local. The
whole scheme rests on one fact about how each harness discovers skills:

|                            | Claude Code                                                   | Codex (GPT-5.5)                                                                                              |
| -------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Format                     | `SKILL.md` (`name` / `description` / `allowed-tools`) | `SKILL.md` (`name` / `description`, optional `metadata.short-description`)                           |
| User-global path           | `~/.claude/skills/`                                         | `~/.agents/skills/` (`~/.codex/skills/` still read but **deprecated**)                             |
| Repo / project path        | `<repo>/.claude/skills/`                                    | `<repo>/.agents/skills/` — every dir from CWD up to the repo root; also `<trusted-repo>/.codex/skills/` |
| Admin / system path        | —                                                            | `/etc/codex/skills/` (machine-wide); bundled OpenAI skills under `~/.codex/skills/.system/`              |
| Arbitrary-path auto-detect | ❌ no                                                         | ❌ no                                                                                                        |
| `combo/skills` direct    | ❌ not discovered                                             | ❌ not discovered                                                                                            |

`name` + `description` are the **portable minimum** both harnesses read; the rest
is harness-local and simply ignored by the other side (`allowed-tools` is
Claude-only; `metadata.short-description` is a Codex extra), so including them is
safe. The real split is the **search path** — `.claude/skills` vs `.agents/skills`
— and neither harness auto-detects an arbitrary directory, so a skill sitting in
`combo/skills` is *not* found on its own; it must be exposed through each
harness's search path via **symlink**. (Codex follows symlinked skill folders in
these locations, so linking works.)

### Layout — single source of truth + symlinks

Keep the skill **body** under version control in `combo/skills/`, and link it into
both harnesses' **project-scope** search paths at the launch root:

```plain
<launch root>/combo/skills/combo-<name>/  # the real skill (git-tracked, shared; combo- prefix)
  SKILL.md
  references/   scripts/   …

<launch root>/.claude/skills   ->  symlink to <launch root>/combo/skills  (Claude Code finds every skill)
<launch root>/.agents/skills   ->  symlink to <launch root>/combo/skills  (Codex finds every skill)
```

The link is at the **`skills` directory** level (one symlink per harness, 2 per
launch root), not per individual skill. Both harnesses follow a symlinked
`skills` folder, so every directory under `combo/skills/` is visible at once —
**adding a skill needs no relinking**: drop the dir into `combo/skills/` and both
sides see it immediately.

Why link into `<launch root>` project scope rather than `~/.claude` / `~/.agents`
user-global:

- The body lives **once** in `combo/` (git-tracked) — no duplication, shareable
  with the team.
- Linking at the **launch-root project scope** (not user-global) keeps the
  machine's personal config clean and scopes the skills to **this workspace**.
- An **interactive** session started at the launch root discovers the links
  naturally (Codex walks `.agents/skills` from CWD up; Claude reads the
  project-scope `.claude/skills`). **Forks are different**: a fork is pinned to
  one repo (`codex exec -C <repo>` / `claude -p` `cwd`), so its skill walk is
  rooted at *that* repo, not the launch root — launch-root links are **not**
  guaranteed to be in scope. For forks, either link the skill under the forked
  repo's own path / user-global `~/.agents/skills`, or name it explicitly in the
  task string (see [Skills through a Codex fork](#skills-through-a-codex-fork)).

> **`<launch root>` is not a git repo** — it is the multi-repo aggregation root.
> So `<launch root>/.claude/skills` is *not* a git-shared `<repo>/.claude/skills`;
> it is a **local workspace project scope**. Anything you want versioned/shared
> goes in the body under `combo/skills`, which *is* tracked.

### Relinking script (idempotent)

Symlinks are machine-local and do not travel in git. Because the link is at the
`skills` directory level, it is just **2 links per launch root** — re-created only
when a new environment is set up (a freshly cloned launch root with no links yet),
**not** when a skill is added. That makes manual setup fine, but a tiny
bootstrap script — `combo/skills/link.sh` — keeps it idempotent:

```bash
#!/bin/bash
# combo/skills/link.sh — expose the whole combo/skills dir to both harnesses
ln -sfn <launch root>/combo/skills <launch root>/.claude/skills
ln -sfn <launch root>/combo/skills <launch root>/.agents/skills
```

`ln -sfn` makes it idempotent. Adding a skill is just "drop the dir under
`combo/skills/`" — no relink needed, since the `skills` dir itself is the link.

### Where each procedure belongs

| Layer                                                   | What goes there                                                                                  |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `<launch root>/combo/skills/combo-<name>`             | **Shared** skill body (`combo-` prefix) — Markdown steps, `references/`, `scripts/` |
| `<launch root>/.claude/skills`, `…/.agents/skills` | Each is a single symlink →`combo/skills` — the shared-skill entry point for each harness     |
| `<repo>/.agents/skills` (+ `<repo>/.claude/skills`) | **Repo-specific** skills — that repo's design rules, tests, review focus                  |
| `<harness>/.agents/skills`                            | **Harness-specific** skills — simulation/lint/debug steps that assume one harness         |

And the rule-vs-skill split:

- **Always-on rules** (don't git at the root; descend to a repo for git/diff/test;
  pin a repo on every fork; Notion access path) → `CLAUDE.md` / `AGENTS.md`.
- **On-demand reusable procedures** → skills (the layout above).
- **External-system connections** (Notion / GitHub / Slack) → MCP.
- **Bundled distribution** (skill + MCP + assets together) → a plugin.

> A shared skill is visible to **every** repo. Anything that depends hard on a
> specific project's paths, EDA tools, env vars, or harness layout belongs in a
> **repo- or harness-specific** skill, not in `combo/skills`.

### `SKILL.md` and naming

Keep frontmatter **minimal** so it works in both harnesses, and make the
`description` say **when to use** the skill (trigger phrasing, in both JP/EN where
relevant) rather than what it does — that text drives auto-selection:

```markdown
---
name: combo-rtl-review
description: Use when reviewing RTL changes for reset, clocking, interface timing, CDC risks, and missing tests.
---

# RTL Review
1. Check the changed RTL files first.
2. Review reset behavior and clock assumptions.
3. Check interface timing and CDC risks.
4. Identify missing or stale tests.
5. Report findings first, with file:line references.
```

#### Name by origin layer (prefix convention)

`combo/skills` is symlinked into **every** repo, so its names share one flat
namespace with each repo's own `.claude/skills` / `.agents/skills`. To keep them
from colliding and to make a skill's **origin** obvious in any listing, prefix the
name by the layer it lives in — and use the **same string for the directory name
and the frontmatter `name:`** (both harnesses select on `name` + `description`, so
the two must match):

| Layer                                                         | Prefix         | Example              |
| ------------------------------------------------------------- | -------------- | -------------------- |
| `combo/skills` — **shared**                          | `combo-`     | `combo-rtl-review` |
| `<repo>/.claude(.agents)/skills` — **repo-specific** | `<repo>-`    | `cpu-sim-debug`    |
| `<harness>/.agents/skills` — **harness-specific**    | `<harness>-` | `claude-lint`      |

The prefix is for **human/listing disambiguation and collision avoidance**, not
for triggering — auto-selection is still driven by `description`, so keep that
focused on *when to use* the skill.

### Skills through a Codex fork

A `fork_to_codex` / `ask_codex` call is **one-shot and stateless**, so a skill
sitting in `.agents/skills` may **not** auto-fire inside the fork. To use one
reliably from a fork: **pin `repo` to `combo`** (or the target repo), and **name
the skill explicitly** in the `task`/`question` — e.g. "follow the steps in
`combo/skills/combo-<name>/SKILL.md`". (Same statelessness as everywhere else in this
directory: the fork sees only what the call string carries.)
