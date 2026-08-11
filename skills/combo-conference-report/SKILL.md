---
name: combo-conference-report
description: カンファレンス／学会／展示会の参加メモ（現地で得た一次情報と自分の私見が混ざったテキスト）と「自分の立場」を入力に、一次情報を公開情報で裏取りして事実として整理し直し、その上で自組織メンバー向けの考察をまとめた単一の Markdown レポートを生成する。一次情報／私見／未確認を機械的に分離し、裏取り済みの事実には実際に取得した出典（URL・発行元・公開日・参照日・ページ内に実在する引用）を残す。「カンファレンス参加レポートを作って」「学会メモを裏取りしてレポート化」「展示会の所感をチーム向けにまとめて」「出張報告を事実と私見に分けて」「conference trip report」等で起動。
allowed-tools: Read, Write, Edit, Bash, WebSearch, WebFetch, mcp__codex__web_rag, mcp__codex__ask_codex
---

# combo-conference-report — 参加メモ → 裏取り済み事実 + 自組織向け考察の md

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | 参加メモを claim 単位に切り出し、1 件ずつ「一次情報 / 私見」を裁定する。一次情報は公開情報（一次ソース優先）で裏取りし、CONFIRMED / CORRECTED / PARTIAL / PRIVATE_PRIMARY / UNVERIFIED を付けて事実として言い直す。出典は URL を**実際に取得**して証跡を残す。その上で `stance.md` の立場を前提に、自組織メンバーへの考察（根拠・示唆・アクション）を書く。 |
| **Input** | 参加メモ（txt/md）1 つ以上 ＋ 自分の立場を書いたファイル（役割・所属・このレポートを読むメンバー）。カンファレンス名は任意。 |
| **type** | `one-shot`（レポート本文）＋ `incremental`（`work/ledger.json` の裁定、`work/evidence*`、`work/review.json` は追記・更新して残す） |
| **Output** | 単一の `.md`（既定 `<conference>_report_<yymmdd>.md`）と、根拠となる `work/`（`claims.json` / `ledger.json` / `evidence.json` / `evidence/*.txt` / `review.json` / `section0.md` / `summary.md` / `ledger_table.md` / `sources.md`）。 |
| **goal** | 出力 md が存在し、`scripts/check_report.py check` の 17 ゲートが全 PASS すること（G15 が別モデルの反証レビュー記録を必須の検査対象にし、G16 が本文の散文主体を要求するので、レビュー未実施や台帳表の貼り付けだけでは PASS しない）。 |
| **Verification** | `python3 scripts/check_report.py check --report <out.md> --work <work> --notes <notes...> --stance <stance>` が exit 0（exit 2 は入力不備）。 |
| **loop limit** | 3（= **台帳・レポートを直して再検証した回数**。HTTP の retry とは別物） |

## When to Use

- カンファレンス／学会／展示会で取ったメモが、聞いた事実と自分の感想で混ざっている
- そのまま共有すると「登壇者が言った事実」と「自分の推測」が区別できず、チームが誤った前提で動く
- 社外発表・ベンダー説明の数字や時期を、公開情報で裏を取ってから配りたい
- 単なる要約でなく、自分の立場・チームの文脈に落とした示唆とアクションまで欲しい

使わない場面: 公開情報の調査そのもの（一次情報の入力が無い）、単一資料の要約、議論の文字起こし統合（→ `combo-transcripts-to-scqa`）。

## Prerequisites

1. **python3**
2. **Web 検索の経路**（`WebSearch`/`WebFetch`、または `mcp__codex__web_rag`）
3. **URL への HTTP アクセス**（`fetch_sources.py` が実取得する）。PDF 出典を使うなら **pdftotext**（poppler-utils）
4. **別モデルへの経路**（`mcp__codex__ask_codex` 等）。手順 8 の反証レビューは G15 で必須
5. **立場ファイル**が空でないこと（誰に向けた考察かが決まらないと §2 の考察が書けない）

2〜4 のいずれかが無い環境では goal を満たせない。着手せず `blocked` で報告する（`failed` ではない）。

## Procedure

1. **Fix the goal.** Task Summary の goal / Verification をこの run の唯一の完了条件として固定する。途中で緩めない。
2. **Check the inputs.** メモと立場ファイルの実在・可読、Web 経路、別モデル経路を確認する。欠けていれば `blocked`。
3. **Segment（決定的）.**
   ```bash
   python3 scripts/init_ledger.py <notes.md> [...] --stance <stance.md> \
       --work <work> --conference "<カンファレンス名>"
   ```
   - 引数順に `N1..Nn`、claim に `C1..Cn` が振られる。ID は以後の引用キー。
   - `work` に既存生成物があると**何も書かずに**エラーで止まる。作り直すなら work を消す。
   - **`claims.json` / `ledger.json` の `id/doc/context/text` は書き換えてはいけない。**
     G0 がメモを読み直して再セグメントし、全項目を照合する（メモ自体の書き換えも検出される）。
4. **Adjudicate（LLM）.** `work/ledger.json` の**全行**を埋める。行を消したり間引いたりしない。
   - `kind`: `fact`（登壇者・展示・配布資料から得た一次情報）／`opinion`（自分の解釈・感想・推測）。
     `kind_rationale` に**その行固有の**分類理由。数値・日付・「発表/提供/搭載」等を含む記述を
     `opinion` にするなら、なぜ一次情報でないのかを 40 字以上で明示する（G8。面倒な fact を
     opinion に逃がすのは迂回）。
   - `fact` の行は必ず裏取りする。**公式発表・規格団体・論文・決算資料などの一次ソースを優先**し、
     二次記事しか無い場合は `PARTIAL` に留める。
   - `status`:
     - `CONFIRMED` — 公開情報とメモの内容が一致
     - `CORRECTED` — 数値・時期・主体が違っていた（`restated` に正しい記述、`status_rationale` に差分）
     - `PARTIAL` — 一部だけ裏が取れた（どこまで取れたかを `status_rationale` に）
     - `PRIVATE_PRIMARY` — **非公開の一次資料しか根拠が無い**（会場配布資料、社外秘スライド、
       認証・ペイウォール越しの資料）。`private_sources` に
       `document` / `page` / `classification` / `holder` / `quote` を書き、公開 URL は付けない。
       これは「裏が取れていない」ではなく「公開情報では検証できない」という別の状態。
     - `UNVERIFIED` — 公開情報が見つからない。**捏造で埋めない。** `searched` に
       `{"queries": [...], "domains": [...]}` を構造化して残す
   - `restated`: 裏取り後の「事実としての言い直し」。**メモの写経は G3 で落ちる。**
   - `sources`（公開出典）: `title` / `url` / `publisher` / `date`(YYYY[-MM[-DD]]) /
     `accessed`(YYYY-MM-DD) / `quote`（**そのページに実在する逐語引用**、10 字以上、
     主張を裏づけている箇所。ナビゲーションやフッタから拾うのは迂回）。実際に開いた URL だけ。
   - `status_rationale`: 行ごとに固有の文。定型文のコピーは G7 で落ちる（数字を足しての回避も潰してある）。
5. **Fetch the sources（決定的）.**
   ```bash
   python3 scripts/fetch_sources.py --work <work>
   ```
   URL を実取得し `work/evidence.json` / `work/evidence/*.txt` に証跡を残す（HTML と PDF に対応。
   非公開アドレスへは行かない）。**status 200 でない出典・引用がページ本文に無い出典は G4 で落ちる。**
   robots・403・ペイウォール・JS 描画・画像 PDF のように**再試行で直らない**ものはループを消費させず、
   ただちに公式 PDF／静的ページへ差し替えるか、`PRIVATE_PRIMARY` / `UNVERIFIED` へ裁定し直す。
   証跡ファイルを手で書くのは goal 未達の隠蔽（G4 が `text_sha256` を照合する）。
6. **Generate（決定的）.**
   ```bash
   python3 scripts/check_report.py report --work <work>
   ```
   `summary.md` / `ledger_table.md` / `sources.md` が出る。**件数はここでしか作らない。**
   これらは**付録**であって本文ではない。
7. **Write the report.** `reference/report_template.md` の章立てに従って 1 本の md を書く。
   **読み物としての主役は本文 §0〜§3 の散文**で、台帳・サマリ・出典のような
   チャンク単位の表は付録 §4〜§9 へ回す（G16）。参考の完成形:
   `eai_doc/DX/CadenceLIVE_Japan_2026_report.md`。
   - §0 は「この報告の読み方」を 120 字以上の地の文で。立場・読み手と、事実／考察／未確認の
     読み分けを明示する。
   - §1 は `###` テーマ配下に**地の文で**書く（節全体で 200 字以上、テーマごとに 40 字以上の地の文）。
     裏取り済み fact は本文のどこかに一度は現れ、その記述に `restated` を逐語で含め `[C#]` を添える。
     表を使ってよいのは「並べた方が読める情報」（拠点一覧・数字の層分け・比較）だけで、
     本文の 40% 超が表になると落ちる。1 つの記述（段落・箇条書き 1 行・表 1 行）に置ける CID は 3 個までで、置いたら**その全部**の `restated` を含めること。私見表現は §2 へ。
   - §2 は `### 見出し` ごとに 200 字以上。観察 → 解釈 → 留保 → 結論 で展開し、
     `- 根拠:`（裏取り済み fact のみ）/ `- 示唆:` または `- 結論:`（30 字以上）/
     `- アクション: 担当=… / 内容=… / 期限=<ISO 日付>` を必ず置く。立場・読み手に必ず言及する。
   - §3 まとめは 200 字以上、うち地の文 80 字以上（箇条書きの前に総括の一段落を置く）。§2 の各項目と一対一で対応させる。
   - 付録A（§4）に UNVERIFIED を全件、付録B（§5）に opinion を全件、**1 行 1 件・必須列を埋めて**
     移す。捨てない。付録A の「探した範囲」は台帳 `searched` の語を含めること（G12）。
   - 付録C〜F（§6〜§9）は生成物の**見出し以降を該当章へ逐語で**貼る（G9b が章ごとに一致を見る）。
   - 件数は台帳・引用に出る数値以外を書かない（G14b）。雛形の山括弧を残さない（G14a）。
8. **Adversarial review（別モデル）.** 決定的スクリプトでは判定できない 3 点を**別モデル**に反証させる。
   レポートと `work/ledger.json` を渡して（例: `mcp__codex__ask_codex`）:
   1. 各 sourced fact の `quote` は本当にその `restated` を裏づけているか（引用の実在は機械で
      確認済み。**意味の対応**は未確認）
   2. `fact` / `opinion` の裁定は妥当か（数値・発表を含む記述を opinion に逃がしていないか）
   3. §2 の考察は `stance.md` 固有か、一般論に置き換えられる作文になっていないか
   4. **§1 に根拠のない断定が混ざっていないか**（CID の無い地の文、および `restated` の
      前後に足した裏取り外の断定。これは決定的には検査できない）

   `PRIVATE_PRIMARY` の行は公開 URL を持たないので、1. は `private_sources.quote` と
   `restated` の対応を見る。

   回答を `work/review.json` に保存する（スキーマは `reference/review_schema.json`）。
   `supported` / `agree` / `specific` が false のものは台帳へ戻して直し、**直してから再レビュー**する。
   false のまま、あるいは対象を欠いたままでは PASS しない（G15）。

   > G15 が機械的に保証するのは**記録の網羅**だけ。review.json を書ける主体はレポートを
   > 書いた主体と同じなので、「本当に別モデルに聞いたか」は保証できない（信頼ベースの
   > attestation）。ここを自分で埋めるのは、ゲートを通すためだけの偽装であって作業ではない。
9. **Verify.**
   ```bash
   python3 scripts/check_report.py check --report <out.md> --work <work> \
       --notes <notes.md> [...] --stance <stance.md>
   ```
   `--notes/--stance` を渡すと manifest のパス申告を信用せずその入力で再現照合する（推奨）。
10. **Loop.** FAIL が残るうちは 4〜9 を回す（上限 3 回）。**ゲートを緩める／台帳の行を消す／証跡や
    レビュー記録を自作する**のは修正ではない。上限に達したら残 FAIL を明示して `failed`。
    外部要因（ネットワーク・別モデル経路が無い）で回せないときは `blocked` と区別して報告する。

## Gates（`check_report.py check`）

| Gate | 見るもの | 塞いでいる迂回 |
| ---- | -------- | -------------- |
| G0 | 入力不変性（メモ→claims→ledger を再セグメントして全項目照合、`claims_sha256` 再計算、stance のハッシュ） | 台帳の `text` を裏取りしやすい主張にすり替える／メモを短く書き換えて claim を減らす |
| G1 | 台帳網羅（1 claim = 1 行、ID 重複なし） | 面倒な claim を台帳から消して 0 件 PASS |
| G2 | 未裁定 0（kind/kind_rationale、fact は status/restated/status_rationale） | 空欄のまま「裏取り済み」と称する |
| G3 | `restated` がメモの写経でない | 言い直さず原文コピーで裏取り済みを装う |
| G4 | 公開出典の実体（実取得 status=200・`text_sha256` 一致・安全な証跡ファイル名・ページ内に実在する引用） | URL の作文、実在ドメイン＋架空パス、証跡テキストの手編集、取得していない URL |
| G5 | `PRIVATE_PRIMARY` の証跡（資料名/ページ/機密区分/保持者/引用、公開 URL を持たない） | 非公開資料を根拠に公開裏取り済みを装う／逆に裏取り不能を隠す |
| G6 | UNVERIFIED の探索証跡（queries / domains） | 「見つからなかった」だけで探索範囲が無い |
| G7 | 判定根拠が行ごとに固有（CID・数字を除去して重複判定） | 同じ一文を全行にコピー、末尾に ID を足して回避 |
| G8 | 数値・日付・発表動詞を含む記述を opinion にするなら理由が 40 字以上 | 裏取りが面倒な fact を opinion に逃がす |
| G9 | 章立て（順序・正確な表題）と 付録C〜F（§6〜§9）の**章ごと**一致 | 生成物を手で書き換えて件数を合わせる／索引だけ貼る |
| G10 | §1 はテーマ配下・裏取り済み fact が**全件**本文に出る・その記述が `restated` 逐語・**1 記述に CID は 3 個まで**で置いた CID 全部の `restated` を含む・私見表現なし・§1 の CID は本文に置ける区分のみ | 取りこぼし、私見を事実編に紛れ込ませる、**台帳を 1 段落にベタ貼りして ID だけ並べる** |
| G11 | 付録B に opinion 全件、1 行 1 件、必須列が実質的に埋まり台帳 text と対応 | 私見を消して「事実だけ」に見せる、同じ作文を全行に複製、1 行に全 ID を詰める |
| G12 | 付録A に UNVERIFIED **のみ**全件、探した範囲が台帳 `searched` と対応 | 未確認を黙って本文へ混ぜる、探索範囲を作文する、裏取り済みの ID を未確認欄に紛れ込ませる |
| G13 | §2 が 根拠(裏取り済み fact)+示唆/結論(30 字)+アクション(担当/内容/ISO 期限)、stance の語に紐づく | 「示唆がある」「アクションを行う」で通す、opinion を根拠にする、期限を書かない |
| G14 | placeholder・山括弧ゼロ / 生成物以外の章の件数は台帳・引用に出るものだけ | TODO 残し、手集計の件数で内訳の和を母集団に合わせる |
| G15 | 反証レビュー記録（`work/review.json`）が全 sourced fact・要注意 opinion・§2 各項目を覆う（**網羅の検査。レビューの独立性は機械保証できない**） | レビュー対象の取りこぼし、note の複製 |
| G16 | 本文（§0〜§3）が散文主体。**地の文の文字数**で判定する（§0 120 字・§1 全体 200 字＋テーマごとに 40 字・§1 の表が半分未満・§2 各項目 200 字かつ表 30% 未満・§3 全体 200 字＋地の文 80 字・本文の表が 40% 超で FAIL） | 台帳やチャンクの表を本文の前面に貼って散文を書かない、箇条書き・表の羅列で済ませる、まとめが一行、**短い行を量産して行数比を作る**（比率は行数でなく文字数で見る） |

### 決定的に検査できないこと（正直な限界）

`work/` を書ける主体は「メモも claims も manifest も evidence も review も全部作り直して
整合させる」一貫改竄が原理的に可能で、これは暗号署名なしには防げない。G0/G4 が守るのは
*部分的な*すり替え（台帳だけ書き換える／証跡テキストを手編集する／取得していない URL を
書く／別 URL の証跡を流用する／`work/stance.md` だけ盛る）まで。

意味の判断（出典が主張を裏づけるか・裁定の妥当性・考察が立場固有か）は決定的には検査できず、
G15 は**反証レビュー記録の網羅**を見るだけで、その独立性は保証しない。

**§1 の「根拠のない記述」も決定的には落とせない。** G10 が見るのは「裏取り済み fact が
全部出ているか」「CID を置いた記述がその `restated` を含むか」「私見表現が無いか」までで、
CID の無い地の文や、`restated` に足した裏取り外の断定は素通りする（文脈をつなぐ地の文は
必要なので、事実らしい表現を機械的に禁止すると散文が書けなくなる）。ここは手順 8 の
反証レビュー観点 4 に回している。同様に G16 が見るのは**分量と構成**であって文章の質ではない。独立性まで機械で
担保するなら、別モデル側の実行系が実行 ID つきの成果物を作成側が書けない場所に出し、それを
検証する仕組みが必要になる（現状は未実装）。**ここは規範に依存している**と理解して使うこと。

## Output Contract

- レポートは**単一 md**。`work/`（証跡・レビュー記録込み）は残す（消さない）。
- 事実（§1）・考察と私見（§2 / 付録B）・未確認（付録A）は**必ず別の節**。読み手が節を見ただけで確度が分かること。
- **本文は散文、機械生成の表は付録**。上から読んで話が通ることを優先し、台帳を本文の代わりにしない。
- §1 の全記述は台帳 ID で 付録E へ、付録E は URL で 付録F へ、付録F は `evidence.json` で実取得記録へ辿れること。
- 裏が取れなかったことは**欠点ではない**。UNVERIFIED / PRIVATE_PRIMARY として残すのが正しい出力で、
  埋めるのは誤り。

## Reporting

完了時に次を報告する。

- 出力 md のパス、`work/` のパス
- `check_report.py check` の最終行（PASS/FAIL とゲート名）と、手順 8 の反証レビューで出た指摘の処置
- CORRECTED になった claim（メモの認識が公開情報と違っていた箇所）— 最も価値の高い差分なので必ず明示する
- UNVERIFIED / PRIVATE_PRIMARY として残した claim と、次のフォロー先
