---
name: combo-notion-mcp-recovery
description: Use when recovering Codex Notion MCP startup/auth failures, especially "MCP client for `notion` failed to start", missing Notion tools, expired OAuth, cancelled Notion MCP calls in Codex forks, or Japanese requests about Codex notion mcp error recovery / notion mcp 復旧.
---

# combo-notion-mcp-recovery — Codex Notion MCP の復旧

Codex の OAuth `notion` MCP サーバを復旧する。よくある起動警告は OAuth を
更新するだけで直る。

原則: **goal が満たされなければ完了とせず、同じタスクを loop する。**

---

## このタスクの概要

| 項目                | 内容                                                                                                                                                                   |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **できること** | Codex の `notion` MCP の起動失敗・認証切れ・ツール呼び出しキャンセルを診断し、ログイン／登録／設定修正で復旧する                                                          |
| **入力**       | Codex CLI (`codex`) が実行可能なこと、`~/.codex/config.toml` への読み書き権限、OAuth を完了できるブラウザ（無い場合はユーザーに URL を渡せること）                        |
| **type**       | `リカーシブ`（既存の `~/.codex/config.toml` と登録済みサーバを読み込み、必要な箇所だけ差分修正する）                                                                       |
| **出力**       | 復旧した `notion` MCP 登録＋有効な OAuth トークン、必要なら修正した `[mcp_servers.notion]` ブロック（編集した場合はバックアップ）、原因と実施内容の報告                     |
| **goal**       | `codex mcp login notion` が成功し、`codex mcp list` に OAuth 版 `notion`（`url = "https://mcp.notion.com/mcp"`, `default_tools_approval_mode = "approve"`）が登録されている |
| **検証**       | `codex mcp login notion` が `Successfully logged in to MCP server 'notion'.` を出す／`codex mcp list` と `~/.codex/config.toml` の `[mcp_servers.notion]` が上記 2 項目と一致する |
| **loop 上限**  | 3 回                                                                                                                                                                    |

---

## 手順

1. **概要表の goal・入力・出力・検証を読み、この実行中の唯一の完了判定として固定する**。
   実行中に goal を緩和・変更しない。変更が必要と判断したら作業を止め、理由を報告する。
2. **入力（`codex` の存在、`~/.codex` への読み書き権限、OAuth を完了できる手段）が
   揃っているか確認する**。`codex` が無い／`~/.codex` を読めない場合は着手せず
   **`検証不能`** として、不足物と再開条件を報告する。
   OAuth については、**現環境でブラウザを開けなくても、認可 URL をユーザーに渡せるなら
   着手してよい**（手順 4 の Fast Path まで進める）。URL を渡してユーザーの完了待ちに
   なった時点で **`検証不能`** とし、再開条件（ユーザーが認可を完了したら再実行）を報告する。
3. **既存状態を処理する（リカーシブ）**。
   - **通常はまず手順 4 の Fast Path（`codex mcp login notion`）を実行する。**
     成功すればこの時点で設定調査は不要で、手順 5 の検証へ進む
     （元々「起動警告なら OAuth 更新だけで直る」ケースが大半のため）。
   - **login が失敗した場合のみ**、既存設定を読み込む。`codex mcp list` と
     `~/.codex/config.toml` の `[mcp_servers.notion]` を確認し、未登録／設定不備
     （`url` 違い、`default_tools_approval_mode` 欠落）／認証切れのどれなのかを
     切り分け、**今回直すべき差分を列挙してから**手を入れる。
     直すのは列挙した差分だけで、他の MCP 設定には触れない。
   - **編集する前に `~/.codex/config.toml` をバックアップする。**
   - `codex mcp list` も `config.toml` も読めない場合は、新規生成に切り替えず
     **`検証不能`** として報告する（設定を盲目的に作り直すと既存の他 MCP を壊す）。
4. **作業する。**

   **Fast path — まずこれを実行する:**

   ```bash
   codex mcp login notion
   ```

   `Successfully logged in to MCP server 'notion'.` が出たら**設定調査はせず**手順 5 へ。
   すでに現セッションで MCP 起動に失敗している場合は、新しい Codex セッションが
   必要な旨をユーザーに伝える。現環境で OAuth を開けない／完了できない場合は、
   認可 URL を報告してユーザーにブラウザで開いてもらい、その時点では
   **`検証不能`**（再開条件 = 認可完了後に再実行）として手順 7 で報告する。

   **login が失敗する場合 — 登録が OAuth 版 Notion MCP か確認する**
   （Bearer トークンの `codex_apps` コネクタではない）:

   ```bash
   codex mcp list
   sed -n '1,240p' ~/.codex/config.toml
   ```

   期待する設定:

   ```toml
   [mcp_servers.notion]
   url = "https://mcp.notion.com/mcp"
   default_tools_approval_mode = "approve"
   ```

   `notion` が無ければ登録してから login する:

   ```bash
   codex mcp add notion --url https://mcp.notion.com/mcp
   codex mcp login notion
   ```

   `default_tools_approval_mode` が無い／`approve` でなければ
   `[mcp_servers.notion]` に追記する。`codex exec` の fork は非対話なので、
   承認プロンプトが出る Notion MCP 呼び出しは自動でキャンセルされる。

   **診断ログは上記で説明がつかないときだけ見る。** トークンストアや設定ツリー全体を
   `token`/`oauth` で grep しない（秘密情報が transcript に出る）:

   ```bash
   find ~/.codex -maxdepth 4 -type f \( -iname '*.log' -o -iname '*.jsonl' -o -iname '*.txt' \) -print
   rg -n 'MCP client for .?notion.?|notion.*failed|401|403|ENOENT|permission|spawn|cancelled|invalid_grant|unauthorized' <log-file>
   ```

   よくある原因の読み方:

   - `401`, `403`, `invalid_grant`, `expired`, `unauthorized`: `codex mcp login notion` をやり直す。
   - `user cancelled MCP tool call`: `default_tools_approval_mode = "approve"` を設定する。
   - `ENOENT`, `command not found`, `No such file`: `codex mcp list` を見て登録エントリを直す。
   - TOML パースエラー・テーブル重複: 壊れている Notion MCP の設定ブロックだけを直す。

5. **概要表の「検証」の方法で goal を判定する**。推測で PASS としない。
   `codex mcp login notion` の出力と、`codex mcp list` / `config.toml` の
   `[mcp_servers.notion]` を実際に確認する。あわせて、概要表の「出力」（登録済みの
   `notion` エントリと有効な OAuth トークン、編集した場合は config のバックアップ）が
   実際に存在し、次の Codex セッション／fork がそのまま使える状態であることを確認する。
   config を編集した場合は、他の MCP サーバ設定を壊していないこと（TOML がパースでき
   `codex mcp list` が通ること）も確認する（リカーシブの回帰検証）。
6. **未達なら原因を特定して修正し、再検証する（loop）**。次のいずれかで停止する。
   - goal を満たした
   - 3 回検証しても未達
   - 同じ原因による失敗が 2 回連続した
   - 前回から検証結果が改善しなかった
   - ブラウザ不在・アカウント権限など、このタスク内で解消できない原因が判明した
7. **報告する**。`完了` / `未達` / `検証不能` のいずれかと、以下を簡潔に述べる。
   - **出力**: `notion` MCP の登録状態（url / `default_tools_approval_mode`）、
     login の可否、config を編集したならその差分とバックアップの場所。
   - 特定した原因と実施した修正、検証結果、試行回数。
   - 再開に必要な対応（新しい Codex セッションの起動、ユーザーによる OAuth 完了など）。
   - **goal は「login 成功＋登録が正しい」までしか見ていない。** 実際に Notion ツールを
     使って再度失敗した場合は、その症状を添えてこの skill を再実行してもらう。

## やってはいけないこと

- **Bearer トークンの `codex_apps` Notion コネクタを復旧経路にしない。**
- **この combo ワークフローのために Claude Code へ Notion MCP を追加しない。**
  Claude Code はブラウザコネクタ、Codex は専用 OAuth MCP を使う。
- **`~/.codex`・トークンストア・設定ファイルをユーザーの明示確認なしに削除しない。**
- **`~/.codex/config.toml` をバックアップ無しで編集しない。**
