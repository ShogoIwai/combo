# 引き継ぎ md のテンプレート

節名と順序はこのとおりにする（次セッションとゲートスクリプトが節名で拾う）。
`{...}` は書き方の指示。指示文はそのまま残さない。

```markdown
# {現在の作業を 1 行で}

**Date:** {YYYY-MM-DD}
**Status:** {COMPLETED | IN PROGRESS | BLOCKED}
**Repo:** {<launch-root>/<repo>}
**Branch:** {git branch --show-current}
**Chain:** `{chain_tag}` seq `{N}`
**Parent:** `{親ファイル名}` / `none — first in chain`
**Prior chain:** `{file1}` > `{file2}` > ... > this  /  `none — first in chain`
**Notion:** {ページ ID / URL、無ければ `none`}

---

## Stale References

{親がいて、親に出てくる識別子が現コードに無い場合だけ書く。
- `old_identifier` — 現コードに無し（親 seq N に登場）
直さない・推測しない。次セッションがコードを読んで解決する。全部見つかるなら節ごと省く。}

## Related Handoffs

{同じ chain tag だが親ではない（別の作業筋の）引き継ぎがある場合だけ書く。
- `HANDOFF_xxx_other-topic_2026-01-01.md` — {1 行} 別筋
無ければ節ごと省く。}

## Since Last Handoff

{seq > 1 のときだけ。親の "Where We're Going" と実際に起きたことを突き合わせる:
- 計画のどれをやったか / やらなかったか
- 親の Open Questions のどれが解けたか
- 想定していたリスクのどれが現実になったか
- 方向は変わっていないか
3–8 bullets。スナップショットではなく「動き」を書く。seq 1 なら節ごと省く。}

## The Goal

{3–5 文。最終的に何を達成したいか、なぜそれが要るか、ユーザにとっての完了状態。}

## Where We Are

{15–25 bullets。変更したファイル・関数を実名で。テスト件数・実測値は数字で。
何が動いていて何が動いていないか。10 未満なら掘り足りない。}

## What We Tried (Chronological)

{試した案を全部、時系列で。1 件 = 仮説 → 変更 → 結果（数字）→ なぜ効いた / 効かなかった。
**失敗した案こそ書く**（再発見が一番高くつく）。3–15 件。前セッション分も引き継ぐ。}

## Key Decisions

{自明でない決定と、その理由。**却下した代替案を必ず 1 つ以上**。5–10 bullets。}

## Evidence & Data

{このセッションで得た生データを全部:
- 比較表（案 A/B/C と指標）
- 反復履歴（v1 → v2 → v3、何を変えて結果がどうなったか）
- 進捗マトリクス（N/M 完了）
- 計測値・エラー率・所要時間・件数
- 生成データ / ログのパス

「改善した」と書かない。「28.6 → 4.1 に改善」と書く。表は Markdown table で。
20 行未満の生データ断片（設定・代表ログ・正解データ）は、それ自体が根拠なら貼る。
8–20 項目。Chunked pass なら表 3 つ以上は出るはず。}

## Code Analysis

{読んだコードの要点: シグネチャ、閾値、定数、構造、結合。コードを深く読んでいなければ省く。5–10 bullets。}

## Files Changed

### Source
- path/to/file.py — 何をなぜ変えたか

### Tests
- path/to/test.py — 何を検証しているか

### Data & results
- path/to/results.json — 中身

### Config
- path/to/config — 何を変えたか

## User Feedback & Preferences

{**省略禁止。** ユーザから来た指示・訂正・好み・不満・要望を全部、原文に近い形で。
- 訂正（「そこは A ではなく B」）
- 好み（「コストは気にしない」「勝手に整形しないで」）
- 進め方（「聞かずにやって」「先に案を出して」）
次セッションの立ち居振る舞いを決める情報。重いセッションなら 5–15 項目。}

## Where We're Going

{順序つきの次手順。3–7 bullets。誰が読んでも同じ順に実行できる粒度で。}

## Risks & Blockers

{外部依存・不安定な箇所・環境要因。2–5 bullets。無ければ "None"。}

## Open Questions

{未解決の問い。1–5 bullets。無ければ "None"。}

## Quick Start for Next Session

```bash
# リポジトリを固定（launch root では git を叩かない）
cd <launch-root>/<repo>

# 現状確認
git status -s && git log --oneline -5

# 最初に読むファイル（ここに挙がっていない隣接ファイルも 2–3 個見ること）
{3–5 個}

# 根拠データ
{テスト結果・計測ログのパス}

# 状態の検証
{テストコマンド / 確認手順}

# 次の一手（1 つだけ、具体的に）
{"continue working" のような空文句は不可}
```
```
