---
name: combo-session-handoff
description: セッションを閉じる／context が尽きかけた／作業を中断するときに、次のセッション（Claude Code でも Codex でも、別日の自分でも）がそのまま再開できる単一の引き継ぎ Markdown を作る。会話履歴を掘って「やったこと・試して駄目だった案とその理由・数字つきの実測・決定と却下案・次の一手」を書き出し、git 状態と直前の引き継ぎ（chain）を機械的に取り込み、決定的スクリプトのゲートで薄い引き継ぎを弾く。git commit はしない。「引き継ぎを作って」「ハンドオフして」「セッションを閉じる」「context が足りないので保存して」「作業状況を残して」「次のセッション用にまとめて」「session handoff」「hand off this session」等で起動。
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# combo-session-handoff — セッション → 次セッションが再開できる引き継ぎ md

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

出典: 公開スキル [REMvisual/claude-handoff](https://github.com/REMvisual/claude-handoff)
（session handoff / chain 追跡 / 自己検証の設計）を本環境向けに翻案。翻案点は
**Adaptations** を参照。

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | 現セッションの会話履歴を掘り、次のセッションが**再調査ゼロで再開できる**単一の引き継ぎ Markdown を書く。git 状態・直前の引き継ぎ（chain）・上流の正本パスは決定的に取り込み、「試して駄目だった案と理由」「数字つきの実測」「ユーザからの指示・好み」を必ず残す。最後に**次セッションへ貼るプロンプト**を出す。 |
| **Input** | 現セッションの会話そのもの。対象リポジトリ（`<launch-root>/<repo>/...` の実在する git working tree）1 つ。任意で理由（"context low" / "end of day"）、任意で Notion ページ ID（cross-session の正本）。 |
| **type** | `incremental` — 同じ chain の引き継ぎは seq を増やして積む（親を上書きしない）。 |
| **Output** | 単一の `HANDOFF_<chain>_<slug>_<YYYY-MM-DD>.md`（出力先は **Step 2**）。セッションを閉じるときは同じファイルに `## Session Closed` と `## Next Session Prompt`（貼り付けプロンプト本体）を追記し、会話にも出す。 |
| **goal** | 引き継ぎ md が存在し、`check_handoff.py` の 11 ゲートが全 PASS すること。 |
| **Verification** | `python3 "$SKILL_DIR/scripts/check_handoff.py" --file <handoff.md> --repo <repo root>` が exit 0（exit 2 = 入力不備。`--repo` は git working tree のトップで、引き継ぎファイルはその配下にあること）。 |
| **loop limit** | 3（= 薄いセクションを埋めて再検証した回数） |

## When to Use

- 「引き継ぎを作って」「ハンドオフして」「セッションを閉じる」「作業状況を残して」
- context が残り 25% 前後になり、compaction で失われる前に確定させたいとき
- 作業を別 harness（Claude Code ⇄ Codex）や別セッション・別日の自分に渡すとき

**Guards（起動しない条件）:**
- **freeform 禁止.** 引き継ぎらしい文書を、このスキルを通さず即興で書かない。即興の要約は
  chain 追跡もゲートも無いので「それらしいが薄い」ものになる。
- **作成要求のときだけ.** 「引き継ぎって何？」「引き継ぎファイルを直して」等は対象外。曖昧なら聞く。
- **git commit しない.** ユーザの明示指示があるときだけ commit する（**Adaptations** 参照）。

---

## Adaptations（本環境向けの翻案 — 上流との差分）

| 上流 (claude-handoff) | 本スキル | 理由 |
| ---- | ---- | ---- |
| `bd`(beads) で chain tag / メモ更新 | beads を使わない。chain tag は **Notion ページ ID**（あれば）→ **ブランチ名** → `standalone-<hex>` の順で解決 | この環境に beads は無い |
| 終了時に既定で `git commit` | **commit しない**。ユーザが明示的に指示したときだけ | グローバル運用ルール（git commit 禁止） |
| 出力先 `plans/handoffs/` / `.claude/handoffs/` | 同じ順で探し、無ければ `.handoff/` を作る | `.claude/` は Claude 専用。Codex からも読む |
| Claude Code 専用（plan mode・Agent 前提） | `name`/`description` のみの最小 frontmatter、手順は素の Bash/Read/Write | Codex(`~/.agents/skills`) と共用 |
| cross-session の記憶はローカルのみ | 恒久的な cross-session 文脈は **Notion** が正本。ファイルはその写し | `combo/README.md`「Context carry-over via Notion」 |
| 単一リポジトリ前提 | **必ずリポジトリを 1 つに固定**して git を叩く。launch root では叩かない | launch root は多リポジトリで git が無効 |

---

## Step 1: 文脈を集める

### 1A: 外部状態（Bash を 1 メッセージで並列。sub-agent は使わない）

まず **2 つのパスを解決する**（以後この 2 変数だけを使い、相対パスで参照しない）:

```bash
# 1) 対象リポジトリ（git working tree のトップ。launch root では git を叩かない）
REPO=$(git -C <launch-root>/<repo> rev-parse --show-toplevel)

# 2) このスキル本体（両 harness の user-global に symlink されているので実体を解決する）
for d in "$HOME/.claude/skills/combo-session-handoff" \
         "$HOME/.agents/skills/combo-session-handoff"; do
  [ -e "$d" ] && SKILL_DIR=$(cd "$(dirname "$(readlink -f "$d")")" && \
                             echo "$(pwd)/$(basename "$(readlink -f "$d")")") && break
done
ls "$SKILL_DIR/scripts/check_handoff.py" "$SKILL_DIR/references/output_template.md"
```

`REPO` が取れない（launch root を指した等）なら、そこで止めて対象リポジトリを聞く。

続けて外部状態を集める:

```bash
cd "$REPO" && git branch --show-current
cd "$REPO" && git log --oneline -20
cd "$REPO" && git diff --stat
cd "$REPO" && git status -s | head -30
ls "$REPO"/plans/handoffs/ "$REPO"/.claude/handoffs/ "$REPO"/.handoff/ 2>/dev/null
```

### 1B: chain（連なり）の解決

**chain tag** は最初に当てはまったものを使う:
1. Notion ページ ID / 明確なテーマ名がある → それを kebab-case で
2. 作業ブランチが `main` 以外 → ブランチ名
3. どれも無い → `python3 -c "import secrets;print('standalone-'+secrets.token_hex(4))"`

**親の引き継ぎを探す**（先に当たった方で止める）:

- **Tier A（確定）**: このセッションの冒頭でユーザが
  `Read HANDOFF_xxx.md (seq 2, chain-x) and continue...` 相当を貼っていたか。
  貼っていればそれが親。seq = 親 + 1。
- **Tier B（推定）**:
  ```bash
  grep -l "^\*\*Chain:\*\* \`{chain_tag}\`" "$REPO"/{plans/handoffs,.claude/handoffs,.handoff}/HANDOFF_*.md 2>/dev/null
  ```
  **同じ chain tag は候補であって継続の証明ではない。** 候補の `## Where We're Going`
  を読み、今回の作業がその続きか判定する。続きなら seq+1・parent 設定。
  無関係なら **seq 1（新 chain）** とし、`## Related Handoffs` に参考として並記する。
  判断がつかなければユーザに聞く（既定は新 chain）。

**親が存在するなら親は必読。** `## Since Last Handoff`（計画 vs 実際）は親を読まないと書けない。
親から拾った識別子（関数名・ファイル名・定数）は現コードに対して `grep` し、
見つからないものを `## Stale References` に列挙する（推測で直さない。次セッションが解決する）。

### 1C: 会話のマイニング（これは自分にしかできない — sub-agent に投げない）

**掘り方を宣言してから掘る**（宣言は必須。「Mining with Deep pass (約 250K tokens)」等）:

| Pass | いつ | やり方 |
| ---- | ---- | ---- |
| Quick | 会話が ~100K tokens 未満 | 下のチェックリストで 1 パス |
| Deep | ~100K–500K、またはツール呼び出し 20+ | 2 パス（1 周目=時系列で出来事、2 周目=数値と却下案の回収） |
| Chunked | 500K+、またはツール呼び出し 50+ / 1 時間超 | 会話を時間帯で 3–5 区間に割り、区間ごとに要約 → 統合 |

**抽出チェックリスト**（1 項目でも空欄のまま先に進まない）:

- [ ] 目的（ユーザが到達したい状態）
- [ ] やったこと（変更したファイル・関数を具体名で）
- [ ] 試した順序（成功・失敗の両方、時系列）
- [ ] **失敗した案と、なぜ駄目だったか**（再発見が最も高くつく）
- [ ] 実測・テスト結果（生の数字。「改善した」ではなく「28.6 → 4.1」）
- [ ] 生成したデータ・ログのパス
- [ ] 決定と、**却下した代替案**
- [ ] 発見・落とし穴
- [ ] コード解析（シグネチャ・閾値・定数）
- [ ] **ユーザの指示・好み・訂正**（原文に近い形で）
- [ ] 未解決の問い / 他作業への依存

流し読みしていると気づいたら止めて読み直す。細部が価値そのもの。

---

## Step 2: 出力先を決める

`$REPO` 配下で最初に存在したもの。無ければ `.handoff/` を作る:
1. `plans/handoffs/`  2. `.claude/handoffs/`  3. `.handoff/`（作成）

## Step 3: ファイル名

`HANDOFF_{chain_tag}_{slug}_{YYYY-MM-DD}.md`（slug は 2–4 語の kebab-case）。
衝突したら `_2`, `_3` を付ける。日付は `date +%F` で取る（推測しない）。

## Step 4: 本文を書く

**`$SKILL_DIR/references/output_template.md` を読み**、その節名・順序どおりに書く
（次セッションもゲートも節名で拾う）。`## Quick Start for Next Session` には
**`次の一手` / `Next action` のアンカー行**を必ず置き、その直後の行に実行できるコマンド
（または誰が読んでも同じ動作になる具体行）を 1 つ書く — ゲート G10 はここを見る。

### 分量の目安

| | 標準 context (200K) | 拡張 context (1M) |
| ---- | ----: | ----: |
| 目標（上限を狙う） | 300–400 行 | 500–800 行 |
| 下限（これを割ったら不合格） | 150 行 | 250 行 |
| 軽いセッションの下限 | 80 行 | 120 行 |

**上限を狙う。** 800 行の引き継ぎは 1M context の 0.7% でしかない。
長すぎるのは安い。短すぎるのは次セッションの数時間で払う。

### 2 フェーズ書き

1. **Phase 1 — 一括 Write.** 全節を 1 回の Write で書く。**Phase 1 単体で下限を超えること**。
   「Phase 2 で肉付けする」前提で薄く書き始めない。
2. **Phase 2 — 穴埋め.** 書いたファイルを読み返し、会話に戻って取りこぼし
   （表・数値・言及だけして詳細を書かなかった案・ユーザの訂正）を Edit で追記する。
   Deep / Chunked では **必須**。Quick でも上限の 8 割に届いていなければ実施。

## Step 5: 自己検証（ゲート）

```bash
python3 "$SKILL_DIR/scripts/check_handoff.py" --file <出力 md> --repo "$REPO"
```

入力ゲート（exit 2）: `--repo` が git working tree の**トップ**であること、引き継ぎファイルが
その配下にあること。

11 ゲート:

| ゲート | 内容 |
| ---- | ---- |
| G1 | ファイルが存在し非空 |
| G2 | 必須ヘッダ Date/Status/Repo/Chain/Parent がフェンス外にあり値つき。Date は `YYYY-MM-DD`、Status は COMPLETED/IN PROGRESS/BLOCKED のいずれか |
| G3 | 必須節を全部持つ |
| G4 | 行数下限（既定 150。`--min-lines` で変更） |
| G5 | `## What We Tried` が 3 エントリ以上（**トップレベル**項目 / `###` 見出し / 表の行で数える。入れ子 bullet は水増しにならない） |
| G6 | `## Evidence & Data` に実数が 3 個以上・2 行以上に散っている（日付・時刻は数に入らない。負値・桁区切り・指数も正しく数える） |
| G7 | `## Key Decisions` の**同じ項目の中**に却下の言い回し（却下／不採用／ではなく／instead of 等）がある |
| G8 | `## User Feedback & Preferences` が非空 |
| G9 | chain 整合。seq 1 なら Parent=none。継続なら親を**許可した handoff ディレクトリ内だけ**で探し、親の Chain tag が一致し親 seq == 自分 −1 であること |
| G10 | `## Quick Start for Next Session` に `次の一手`/`Next action` アンカーがあり、その直後に具体的な行がある（"continue working"/"TBD" 等は不合格） |
| G11 | `## Session Closed` を追記したなら、貼り付けプロンプトも同じファイルに記録されていること |

**FAIL したら薄い節を埋めて再実行。**（loop limit 3）残り context をここで使い切ってよい。

## Step 6: 報告

- ファイルパスと行数
- chain（tag / seq / 新規か継続か）
- ゲート結果（FAIL から埋めた場合は何を埋めたか）
- **次の一手（1 つ）**

## Step 7: セッションを閉じるか聞く

> **引き継ぎを書きました。このセッションを閉じますか？**
> - **はい** — 引き継ぎに `## Session Closed` を追記し、次セッション用の貼り付けプロンプトを出します。
>   **git commit はしません**（必要なら「commit して」と明示してください）。
> - **いいえ** — 作業を続けます。「セッションを閉じて」と言われた時点で同じ処理をします。

「はい」のとき:

1. 引き継ぎ md に追記:
   ```markdown
   ## Session Closed
   **Closed at:** {YYYY-MM-DD HH:MM}
   **Uncommitted files:** {git status -s の件数、または "clean"}
   **Session status:** Handed off to next session
   ```
2. Notion ページ ID が与えられていれば、そのページに
   「引き継ぎファイルのパス / chain / seq / 状態 / 次の一手」だけを追記する
   （本文全部は写さない。ファイルが本体、Notion は所在と現況の索引）。
   Claude Code は browser connector から、Codex は自分の Notion MCP から書く。
3. **同じ引き継ぎ md に `## Next Session Prompt` として下のプロンプト本体を追記し**、
   会話にも同じものを出す（会話は流れるが、ファイルは次セッションが必ず開く）。
   追記後に **Step 5 のゲートを再実行**する（G11 がこの追記を見る）。

```plain
-------------------------------------------------------
次のセッションにこれを貼ってください (Paste this into the next session):
-------------------------------------------------------
`{path}` (seq {N}, chain `{chain_tag}`) を読み、"Where We're Going" から続けてください。
リポジトリは {REPO} に固定してください。

作業を始める前に、立ち上がりを口頭で示してください:
1. 引き継ぎを読み、理解した内容（目的・現状・試したこと）を要約する
2. 最初に検証すること（テスト実行・現状確認・読むべきファイル）を述べる
3. "Key files" を読み、そこに挙がっていない隣接ファイルも 2–3 個見る
   （引き継ぎは前セッションが見ていた範囲であって、全体ではない）
4. 最初の一手とその理由を述べる
そのうえで、こちらの合図を待ってください。
-------------------------------------------------------
```

## Cleanup: 終わった chain の退避

作業が完了したら消さずに退避する（過去の決定は後で効く）:

```bash
mkdir -p "$REPO"/.handoff/archive/
grep -l "^\*\*Chain:\*\* \`{chain_tag}\`" "$REPO"/.handoff/HANDOFF_*.md | xargs -r -I{} mv {} "$REPO"/.handoff/archive/
```

---

## Prerequisites

- `python3`（ゲートスクリプト。標準ライブラリのみ）
- 対象が実在する git working tree であること（`<launch-root>` 直下では動かさない）
