---
name: combo-notion-mcp-recovery
description: Use when recovering Codex Notion MCP startup/auth failures, especially "MCP client for `notion` failed to start", missing Notion tools, expired OAuth, cancelled Notion MCP calls in Codex forks, or Japanese requests about Codex notion mcp error recovery / notion mcp 復旧.
---

# Notion MCP Recovery

Recover the Codex OAuth `notion` MCP server. For the usual startup warning, the first fix is just to refresh OAuth.

## Fast Path

Run this first:

```bash
codex mcp login notion
```

If it prints `Successfully logged in to MCP server 'notion'.`, stop. Tell the user login succeeded and that a new Codex session may be needed if MCP startup already failed in the current session.

If OAuth cannot open or complete in the current environment, report the authorization URL and ask the user to open it in a browser.

## If Login Fails

Check that the server is registered as the OAuth Notion MCP, not the `codex_apps` connector:

```bash
codex mcp list
sed -n '1,240p' ~/.codex/config.toml
```

The expected config is:

```toml
[mcp_servers.notion]
url = "https://mcp.notion.com/mcp"
default_tools_approval_mode = "approve"
```

If `notion` is missing, register it and then login:

```bash
codex mcp add notion --url https://mcp.notion.com/mcp
codex mcp login notion
```

If `default_tools_approval_mode` is missing or not `approve`, add it under `[mcp_servers.notion]`. This matters because `codex exec` forks are non-interactive; prompted Notion MCP calls can be cancelled automatically.

## Escalate Diagnosis Only When Needed

Use logs only if login/register/config checks do not explain the failure. Avoid grepping token stores or broad config trees for `token`/`oauth`; that can expose secrets in the transcript.

```bash
find ~/.codex -maxdepth 4 -type f \( -iname '*.log' -o -iname '*.jsonl' -o -iname '*.txt' \) -print
rg -n 'MCP client for .?notion.?|notion.*failed|401|403|ENOENT|permission|spawn|cancelled|invalid_grant|unauthorized' <log-file>
```

Interpret common causes:

- `401`, `403`, `invalid_grant`, `expired`, `unauthorized`: rerun `codex mcp login notion`.
- `user cancelled MCP tool call`: set `default_tools_approval_mode = "approve"`.
- `ENOENT`, `command not found`, `No such file`: inspect `codex mcp list` and fix the registered server entry.
- TOML parse or duplicate table errors: patch only the invalid Notion MCP config block.

## Boundaries

- Do not use the Bearer-token `codex_apps` Notion connector as the recovery path.
- Do not add a Notion MCP to Claude Code for this combo workflow; Claude Code uses its browser connector, while Codex uses the dedicated OAuth MCP.
- Do not delete `~/.codex`, token stores, or config files without explicit user confirmation.
- Back up `~/.codex/config.toml` before editing it.
