---
name: combo-opinion-five
description: 事実・解釈・価値観が混ざった自分のテキスト（走り書き、モヤモヤ、SNS 下書き、社内メールの草稿など）を入力に、羽田康祐『意見をつくる』の FIVE フレームワーク（Fact 事実 / Interpretation 解釈 / Value 価値基準 / Expression 表明）で分解し直し、事実部分は Web で裏取りして補完したうえで、単一の Markdown「意見」にまとめる。立場は既定で「日本人・ASIC ベンダー勤務・妻と子の 3 人家族」（reference/stance_default.md、--stance で差し替え可）。「意見にまとめて」「FIVE フレームワークで整理して」「このモヤモヤを意見にして」「事実と解釈と価値観を分けて」「自分の意見をつくって」「opinion from my notes」等で起動。
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch, mcp__codex__web_rag, mcp__codex__ask_codex
---

# combo-opinion-five — 混ざったテキスト → FIVE フレームワークの意見 md

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | 入力テキストを claim 単位に切り出し、1 件ずつ **F（事実）/ I（解釈）/ V（価値基準）** に裁定する。F は公開情報で裏取りし CONFIRMED / CORRECTED / PARTIAL / PRIVATE_PRIMARY / UNVERIFIED を付けて言い直す。I は「どの事実に乗っているか」と「同じ事実から出る別の解釈」を必ず持たせ、V は「軸・立場から来た由来・捨てるもの」を書かせる。その上で **E（表明）** として、意見・根拠・前提の価値基準・自分の行動・**この意見が変わる条件**を書く。 |
| **Input** | 事実・解釈・価値観が混ざった自分のテキスト（txt/md）1 つ以上。立場ファイルは任意（既定 `reference/stance_default.md` = 日本人 / ASIC ベンダー勤務 / 妻と子の 3 人家族）。 |
| **type** | `one-shot`（意見 md）＋ `incremental`（`work/ledger.json` の裁定、`work/evidence*` は追記・更新して残す） |
| **Output** | 単一の `.md`（既定 `opinion_<topic>_<yymmdd>.md`）と、根拠となる `work/`（`claims.json` / `ledger.json` / `evidence.json` / `evidence/*.txt` / `section0.md` / `ledger_table.md` / `sources.md` / `stance.md`）。 |
| **goal** | 出力 md が存在し、`scripts/check_opinion.py check` の 18 ゲートが全 PASS すること。 |
| **Verification** | `python3 scripts/check_opinion.py check --report <out.md> --work <work> --inputs <src...> [--stance <stance.md>]` が exit 0（exit 2 は入力不備）。 |
| **loop limit** | 3（= **台帳・md を直して再検証した回数**。HTTP の retry とは別物） |

## FIVE フレームワークとは

羽田康祐 k_bird『意見をつくる』（フォレスト出版）第 4 章「意見の構築『FIVE
フレームワーク』」の型。**意見 = 事実 + 解釈 + 価値基準 + 表明**であって、
どれが欠けても意見にならない、という分解になっている。

| | 要素 | この skill での扱い |
| - | ---- | ------------------ |
| **F** | Fact（事実） | 議論の土台となる客観情報。**裏取りしたものだけ** §1 に置く |
| **I** | Interpretation（解釈） | 事実に意味を与える視点。§2。**必ず対立解釈を併記する** |
| **V** | Value（価値基準） | 判断の軸となるスタンス。§3。**立場に紐づける** |
| **E** | Expression（表明） | 自分の言葉での発信。§4。意見・行動・**反証条件**まで書く |

出典: フォレスト出版 書籍ページ（https://www.forestpub.co.jp/author/hada/book/866803692）。

この skill が守っているのは「**混ぜない**」こと。元のテキストでは
「〜が起きている（事実）／だから〜だ（解釈）／〜であるべきだ（価値基準）」が
一続きの文で混ざっている。混ざったまま人に出すと、事実が違っていたのか、
解釈が飛んでいたのか、価値基準が合わないだけなのかが切り分けられない。

## When to Use

- モヤモヤ・違和感はあるが、意見の形になっていない自分のテキストがある
- 自分の主張のうち、どこが事実でどこが自分の価値観なのかを切り分けたい
- 人に出す前に、事実部分だけでも公開情報で裏を取っておきたい
- 反対意見に耐えるか（対立解釈・反証条件があるか）を自分で点検したい

使わない場面: カンファレンス参加メモの共有レポート（→ `combo-conference-report`）、
議論の文字起こし統合（→ `combo-transcripts-to-scqa`）、単なる要約や調べもの。

## Prerequisites

1. **python3**
2. **Web 検索の経路**（`WebSearch`/`WebFetch`、または `mcp__codex__web_rag`）
3. **URL への HTTP アクセス**（`fetch_sources.py` が実取得する）。PDF 出典を使うなら **pdftotext**（poppler-utils）

2〜3 が無い環境では F の裏取りができず goal を満たせない。着手せず `blocked` で報告する（`failed` ではない）。

## Procedure

1. **Fix the goal.** Task Summary の goal / Verification をこの run の唯一の完了条件として固定する。途中で緩めない。
2. **Check the inputs.** 入力テキストの実在・可読、Web 経路を確認する。欠けていれば `blocked`。
   立場を差し替えるなら `reference/stance_default.md` をコピーして編集し `--stance` で渡す
   （既定のままでも動く）。
3. **Segment（決定的）.**
   ```bash
   python3 scripts/init_opinion.py <input.md> [...] --work <work> --topic "<お題>"
   #   立場を差し替えるなら: --stance <stance.md>
   ```
   - 引数順に `N1..Nn`、claim に `C1..Cn` が振られる。ID は以後の引用キー。
   - `work` に既存生成物があると**何も書かずに**エラーで止まる。作り直すなら work を消す。
   - **`claims.json` / `ledger.json` の `id/doc/context/text` は書き換えてはいけない。**
     G0 が入力を読み直して再セグメントし、全項目を照合する（入力自体の書き換えも検出される）。
4. **Adjudicate F/I/V（LLM）.** `work/ledger.json` の**全行**を埋める。行を消したり間引いたりしない。
   - `kind` は 3 択。**迷ったら「その記述が偽であることを、誰かが公開情報で示せるか」で切る。**
     示せる = `fact`、示せないが事実から導いた意味づけ = `interpretation`、
     何を良しとするかの表明 = `value`。`kind_rationale` に**その行固有の**理由（15 字以上）。
   - `fact`:
     - `status`: `CONFIRMED`（一致）/ `CORRECTED`（数値・時期・主体が違った）/
       `PARTIAL`（一部だけ裏が取れた）/ `PRIVATE_PRIMARY`（非公開の一次資料しか根拠が無い。
       `private_sources` に `document`/`page`/`classification`/`holder`/`quote`、公開 URL は付けない）/
       `UNVERIFIED`（公開情報が見つからない。**捏造で埋めない**。`searched` に
       `{"queries":[...],"domains":[...]}` を残す）
     - **公式発表・規格団体・統計・論文・決算資料などの一次ソースを優先**し、
       二次記事しか無い場合は `PARTIAL` に留める。
     - `restated`: 裏取り後の言い直し（**元の記述の写経は G3 で落ちる**）。§1 の本文はこれと逐語一致。
     - `sources`: `title`/`url`/`publisher`/`date`/`accessed`/`quote`（**そのページに実在する
       逐語引用**、10 字以上、主張を裏づけている箇所）。実際に開いた URL だけ。
     - `status_rationale`: 行ごとに固有の文（15 字以上）。
   - `interpretation`:
     - `basis`: この解釈が乗っている**裏取り済み fact の CID**（1 つ以上、必須）。
       裏が取れていない事実の上に解釈を積まない。
     - `interpretation`: 事実に与えた意味（60 字以上）
     - `alternative`: **同じ事実から出る別の解釈**（30 字以上）。形式的な留保ではなく、
       自分の解釈と実際に競合する読み方を書く。ここが弱いと意見は反論に耐えない。
   - `value`:
     - `axis`: 何を良しとするか（例:「回収に数年かかる賭けより、来期の実装で効く改善を優先する」）
     - `origin`: その基準が**自分のどこから来ているか**。立場ファイルの語に紐づける（G8）。
     - `tradeoff`: その基準を採ると**何を捨てるか**（30 字以上）。由来の言い換えは落ちる。
5. **Fetch the sources（決定的）.**
   ```bash
   python3 scripts/fetch_sources.py --work <work>
   ```
   URL を実取得し `work/evidence.json` / `work/evidence/*.txt` に証跡を残す（HTML と PDF に対応）。
   **status 200 でない出典・引用がページ本文に無い出典は G4 で落ちる。**
   robots・403・ペイウォール・JS 描画・画像 PDF のように**再試行で直らない**ものはループを
   消費させず、ただちに公式 PDF／静的ページへ差し替えるか、`PRIVATE_PRIMARY` / `UNVERIFIED`
   へ裁定し直す。証跡ファイルを手で書くのは goal 未達の隠蔽（G4 が `text_sha256` を照合する）。
6. **Generate（決定的）.**
   ```bash
   python3 scripts/check_opinion.py report --work <work>
   ```
   `ledger_table.md` / `sources.md` が出る。**件数はここでしか作らない。**
7. **Write the opinion.** `reference/opinion_template.md` の章立てで 1 本の md を書く。
   - §0 は地の文 120 字以上。読み分け（F/I/V/E/未確認）と立場を明示する。
   - §1 は `###` テーマ配下に**地の文で**。裏取り済み fact は全件本文に現れ、その記述に
     `restated` を逐語で含め `[C#]` を添える（1 記述に CID は 3 個まで）。
     **私見表現（〜と思う／だろう／べきだ／印象／かもしれない 等）は §1 に 1 語でも入れない**（G10）。
   - §2 は解釈を全件 `###` 項目に。`- 事実:` / `- 解釈:` / `- 他の見方:` を必ず置き、
     台帳の `interpretation` / `alternative` を逐語で入れる。
   - §3 は価値基準を全件 `###` 項目に。`- 軸:` / `- 由来:` / `- トレードオフ:`。
   - §4 は `- 意見:` / `- 根拠:`（CID 2 件以上）/ `- 前提の価値基準:`（§3 の CID）/
     `- 私はこうする:` / `- この意見が変わる条件:`。立場の語が 2 つ以上出ること。
   - 付録A（§5）に UNVERIFIED を全件 1 行 1 件、付録B（§6）に反論と応答を 2 件以上。
   - 付録C〜E（§7〜§9）は生成物の**見出し以降を該当章へ逐語で**貼る。
   - 件数は台帳の集計に無い数値を書かない（G16）。雛形の山括弧を残さない。
8. **（推奨・非ゲート）Adversarial review.** `mcp__codex__ask_codex` 等の別モデルに md と
   `work/ledger.json` を渡し、(a) `quote` は本当に `restated` を裏づけているか、
   (b) `value` を `fact` に偽装していないか（逆も）、(c) `alternative` は本当に競合する解釈か、
   (d) §4 は立場固有か、を反証させる。**これはゲートしていない**（記録の網羅を機械で見ても
   独立性は保証できないため）。指摘は台帳へ戻して直す。
9. **Verify.**
   ```bash
   python3 scripts/check_opinion.py check --report <out.md> --work <work> \
       --inputs <input.md> [...] [--stance <stance.md>]
   ```
   `--inputs/--stance` を渡すと manifest のパス申告を信用せずその入力で再現照合する（推奨）。
10. **Loop.** FAIL が残るうちは 4〜9 を回す（上限 3 回）。**ゲートを緩める／台帳の行を消す／
    証跡を自作する**のは修正ではない。上限に達したら残 FAIL を明示して `failed`。
    外部要因（ネットワークが無い等）で回せないときは `blocked` と区別して報告する。

## Gates（`check_opinion.py check`）

| Gate | 見るもの | 塞いでいる迂回 |
| ---- | -------- | -------------- |
| G0 | 入力不変性（入力→claims→ledger を再セグメントして全項目照合、`claims_sha256`、立場のハッシュ） | 台帳の `text` を裏取りしやすい主張にすり替える／入力を短く書き換えて claim を減らす／`work/stance.md` だけ盛る |
| G1 | 台帳網羅（1 claim = 1 行、ID 重複なし、行数 = manifest） | 面倒な claim を台帳から消して 0 件 PASS |
| G2 | 未裁定 0（kind/kind_rationale＋種別ごとの必須欄と最低字数） | 空欄のまま「整理済み」と称する |
| G3 | `restated` が元の記述の写経でない | 言い直さず原文コピーで裏取り済みを装う |
| G4 | 公開出典の実体（実取得 status=200・`text_sha256` 一致・ページ内に実在する引用・日付書式） | URL の作文、実在ドメイン＋架空パス、証跡テキストの手編集、取得していない URL |
| G5 | `PRIVATE_PRIMARY` の証跡と `UNVERIFIED` の探索証跡 | 非公開資料で公開裏取り済みを装う／「見つからなかった」だけで探索範囲が無い |
| G6 | 判定根拠が行ごとに固有（CID・数字を除去して重複判定） | 同じ一文を全行にコピー、末尾に ID を足して回避 |
| G7 | 解釈の足場（`basis` が裏取り済み fact を指す・`alternative` が `interpretation` と別物） | 裏の取れていない事実の上に解釈を積む、対立解釈欄に同じことを書く |
| G8 | 価値基準の由来が立場ファイルの語に紐づき、`tradeoff` が由来の言い換えでない、`value` が 1 件以上 | 誰にでも当てはまる一般論を「自分の価値基準」と称する、捨てるものを書かずに良いことだけ並べる |
| G9 | 章立て（順序・正確な表題）と 付録C〜E（§7〜§9）の**章ごと**逐語一致 | 生成物を手で書き換えて件数を合わせる |
| G10 | §1 は `###` テーマ配下・裏取り済み fact が**全件**本文に出る・その記述が `restated` 逐語・1 記述に CID は 3 個まで・事実以外の CID なし・私見表現ゼロ | 取りこぼし、解釈や価値観を事実編に紛れ込ませる、台帳をベタ貼りして ID だけ並べる |
| G11 | §2 が解釈を全件覆い、各項目に 事実(CID)/解釈(60字)/他の見方(30字)、台帳の文言と逐語一致 | 都合の悪い解釈を落とす、対立解釈を書かずに断定する |
| G12 | §3 が価値基準を全件覆い、各項目に 軸/由来/トレードオフ、台帳の文言と逐語一致 | 価値基準を本文から消して「客観的な分析」に見せる |
| G13 | §4 に 意見(40字)/根拠(CID 2 件以上)/前提の価値基準(§3 の CID)/私はこうする(30字)/この意見が変わる条件(30字)、立場の語 2 つ以上 | 反証条件のない断定、根拠を示さない結論、自分の行動を伴わない評論 |
| G14 | 付録A に UNVERIFIED **のみ**全件、1 行 1 件、探した範囲が台帳 `searched` と対応 | 未確認を黙って本文へ混ぜる、探索範囲を作文する |
| G15 | 付録B に反論 2 件以上、`- 反論:` `- 応答:` 各 40 字以上、複製なし | 反論欄に弱い藁人形を 1 つ置いて済ませる |
| G16 | placeholder・山括弧ゼロ / §0〜§6 の件数は台帳の集計に出るものだけ | TODO 残し、手集計の件数 |
| G17 | 散文主体（§0 地の文 120 字・§1 全体 200 字＋テーマごと 40 字・§2 各項目 150 字・§4 150 字・§0〜§4 の表比率 40% 以下） | 箇条書きと表の羅列で済ませる、台帳を本文の代わりにする |

### 決定的に検査できないこと（正直な限界）

`work/` を書ける主体は「入力も claims も manifest も evidence も全部作り直して整合させる」
一貫改竄が原理的に可能で、これは暗号署名なしには防げない。G0/G4 が守るのは*部分的な*
すり替えまで。

**意味の判断は決定的には検査できない。** 具体的には:

- 出典が主張を本当に裏づけているか（G4 は引用の**実在**しか見ない）
- `fact` / `interpretation` / `value` の裁定が妥当か（**価値判断を事実に偽装する**のが
  この skill で最も起きやすい迂回で、G2/G6 は書式と固有性しか見ない）
- `alternative` が本当に競合する解釈か（G7 は「別の文字列であること」しか見ない）
- §1 の CID の無い地の文に、根拠のない断定が混ざっていないか

ここは手順 8 の反証レビューに回しているが、**ゲートしていない**（レビュー記録を書ける主体と
md を書く主体が同じなので、網羅を機械で見ても独立性は保証できない）。**ここは規範に
依存している**と理解して使うこと。

## Output Contract

- 意見は**単一 md**。`work/`（証跡込み）は残す（消さない）。
- F（§1）・I（§2）・V（§3）・E（§4）・未確認（付録A）は**必ず別の節**。
  読み手が節を見ただけで「事実なのか、あなたの価値観なのか」が分かること。
- §1 の全記述は台帳 ID で 付録D へ、付録D は URL で 付録E へ、付録E は `evidence.json` で
  実取得記録へ辿れること。
- 裏が取れなかったことは**欠点ではない**。UNVERIFIED / PRIVATE_PRIMARY として残すのが
  正しい出力で、埋めるのは誤り。
- 意見が「まだ持てない」という結論も正しい出力になりうる。その場合は §4 の
  「この意見が変わる条件」に、何が分かれば決められるかを書く。

## Reporting

完了時に次を報告する。

- 出力 md のパス、`work/` のパス
- `check_opinion.py check` の最終行（PASS/FAIL とゲート名）
- **CORRECTED になった claim**（自分の認識が公開情報と違っていた箇所）— 最も価値の高い差分
- **fact だと思っていたが value / interpretation だった claim** — 2 番目に価値の高い差分
- UNVERIFIED として残した claim と、次に何を確認すれば意見が固まるか
