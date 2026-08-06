---
name: combo-transcripts-to-scqa
description: 共通テーマで実施した複数回のグループディスカッション／座談会／インタビューの文字起こしPDFを読み、SCQA(Situation/Complication/Question/Answer)フレームワークで横断的にまとめた単一のMarkdownを生成する。合意点・相違点をセッション横断で統合し、逐語引用で根拠を残す。「文字起こしをSCQAでまとめて」「ディスカッションの議事をSCQA化」「複数PDFの議論を一本にまとめて」「複数セッションの合意点と相違点を統合して」「ワークショップ結果を経営向けに一本化」「transcripts to SCQA」「cross-session synthesis」等で起動。
---
# combo-transcripts-to-scqa — 複数の文字起こしPDF → SCQA 単一 md

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | 共通テーマの複数セッション分の文字起こし PDF を横断して読み、SCQA フレームワーク（Situation / Complication / Question / Answer）で 1 本の Markdown に統合する。セッション間の合意・対立・未決を論点マップとして明示し、判断の決め手になった発言を逐語引用で残す。 |
| **Input** | テキストレイヤを持つ文字起こし PDF を 1 つ以上（**コマンドラインに並べた順**が `D1..Dn` の ID を決める）。出力 md パスと work ディレクトリは任意指定。 |
| **type** | `one-shot` — 出力 md は毎回 PDF から全再生成し上書きする。既存出力を情報源として読まない。 |
| **Output** | 単一の `.md`（既定: `<theme>_scqa_<yymmdd>.md`）と、その根拠となる `work/`（`text/D*.txt` / `manifest.json` / `section0.md`）。 |
| **goal** | 出力 md が存在し、`scripts/check_scqa.py` の 8 ゲートが全 PASS すること（章立て・入力一覧の機械生成・全 PDF の被引用・引用先の実在・placeholder ゼロ・S/C/Q/A の実体・対立論点の複数文書裏づけ・引用文の逐語一致）。 |
| **Verification** | `python3 scripts/check_scqa.py --report <out.md> --work <work>` が exit 0。抽出時の `Warning:`（テキストレイヤ無し疑い）が出た PDF は goal 判定前に扱いを決めること。 |
| **loop limit** | 3 |

## When to Use

- 同一テーマで複数回・複数グループ実施したワークショップ／ディスカッションの結果を経営層向けに 1 枚に畳みたい
- セッションごとの議事録は在るが、横断的な「結局どういう問いに何と答えたのか」が無い
- どこが合意でどこが割れているのかを、発言の逐語根拠つきで示したい

使わない場面: 単一会議の議事録起こし（要約するだけ）、音声からの文字起こしそのもの（本 skill の入力は文字起こし済み PDF）。

## Prerequisites

1. **python3**
2. **pdftotext**（poppler-utils）— 無ければ `Error:` で停止する
3. 入力 PDF が**テキストレイヤを持つ**こと。スキャン画像 PDF は本 skill の対象外
   （`combo-docx-to-md` / OCR 経路で先にテキスト化してから渡す）

## Procedure

1. **Fix the goal.** Task Summary の goal / Verification をこの run の唯一の完了条件として固定する。途中で緩めない。
2. **Check the inputs.** PDF の実在・可読・拡張子、prerequisites を確認する。欠けていれば着手せず `blocked` で報告する（`failed` ではない）。
3. **Extract（決定的）.**
   ```bash
   python3 scripts/extract_transcripts.py <t1.pdf> <t2.pdf> ... --work <work>
   ```
   - 入力は**指定した引数順**に `D1..Dn` が振られる。ID は以後の引用キー。同じ PDF の二重指定はエラー。
   - `work/text/D*.txt`（NFKC 正規化・改ページは `\f` 保持）、`work/manifest.json`、`work/section0.md` が出る。前回の `D*.txt` は毎回消してから書く（manifest 外の古いセッションを読み込まないため）。
   - `section0.md` に時刻は入らない（同じ入力なら byte 単位で同一）。抽出日時は `manifest.json` にだけ持つ。
   - `Warning: ... likely a scanned/image PDF` が出た PDF はここで扱いを決める（除外して続けるか、blocked にするか）。判断は報告に書く。
4. **Read across, not one-by-one.** 各 `D*.txt` を読み、**セッションを跨いで**次を拾う。
   - 全セッション共通の前提（→ Situation）
   - 崩れた前提・顕在化した制約・温度差（→ Complication）
   - 議論が実際に答えようとしていた中心の問い（→ Question）
   - 出た結論と打ち手、合意に至らなかった選択肢（→ Answer）
   - 論点ごとの 合意 / 対立 / 未決 と、各セッションの立場
5. **Write the report.** `reference/report_template.md` の章立てに従って 1 本の md を書く。
   - **§0 は `work/section0.md` の中身をそのまま貼る**。件数・ページ数を手で書かない（G0）。
   - 本文の主張には `[D1 p.4]` 形式で根拠を付ける。**ページ番号は省略できない**（`[D1]` は G3 で落ちる）。ページ番号は抽出テキストの改ページ順（1 起点）で、PDF に印字された番号とは限らない。
   - 論点マップの区分セルは **合意 / 対立 / 未決 / 対立なし** の 1 語のみ（説明を混ぜると G6 で落ちる）。**対立行は根拠に 2 文書以上、立場欄に両方の doc ID** を書く。
   - 実際に全セッションが一致していて対立が無いなら、**架空の対立を作らず**区分 `対立なし` の行を 1 本置く（G6 の空振り防止と両立させるための逃げ道）。
   - §6 は **1 引用ブロック = 1 引用 = 1 citation**。引用は**文字起こしからの逐語**で、しかも**引用先ページの中**に存在すること（G7 はページ単位で照合するので、実在する別ページに付け替えても落ちる）。8 文字未満の断片は根拠にならず FAIL。
   - 決められなかったことは Appendix A に置く。`TODO`/`TBD` を書いてよいのはそこだけ（G4）。
6. **Verify.** `check_scqa.py` を実際に走らせる。PASS を推定で書かない。
7. **Loop on failure.** 落ちたゲートの原因を直して再検証する。goal 達成／loop limit 3／同一原因で 2 回連続失敗／改善なし／タスク内で解けない原因（入力欠落・ツール欠落）のいずれかで停止する。
8. **Report.** `done` / `failed` / `blocked`、出力パス、ゲート結果、試行回数、残課題を述べる。

## Gates（`check_scqa.py`）

| Gate | 内容 | 落ちる典型 |
| ---- | ---- | ---------- |
| G0 | §0 の本文が `work/section0.md` と行単位で一致（貼る場所も §0 に限定） | 件数・ページ数を手打ちして実物とズレる／別の場所に貼って §0 は別内容 |
| G1 | 必須章立てが**完全一致**で順序どおり、重複なし | `## 1. Situationではない仮置き` のように語尾を足して章を骨抜きにする |
| G2 | 全入力 PDF が最低 1 回は引用される | 読みやすい 1 本だけで書き、他セッションを無視 |
| G3 | 引用は**ページ必須**で doc ID / ページが実在 | `[D1]` とページを省く／実在しない `[D4 p.99]` を付ける |
| G4 | 本文に placeholder・未置換テンプレ（`<...>` `〔…〕` 相当）無し | `TODO` やテンプレの穴を残したまま完成扱い |
| G5 | S/C/Q/A 各章が**散文 100 字以上**（表・コメント・引用記号を除いた実質）と引用を持ち、Question が疑問文 | 表や定型文で字数を水増しする／Question が「〜について」で問いになっていない |
| G6 | 論点マップが 4 列の表で、区分セルが 4 語のいずれかに**完全一致**。対立行は根拠 2 文書以上＋立場欄に両方の doc ID。対立 0 件なら `対立なし` 行が要る | ダミー対立行を 1 本置いて通す／実際の対立を全部「合意」に潰す |
| G7 | §6 の各引用ブロックが citation ちょうど 1 個を持ち、引用文が**その引用先ページ**に逐語で存在（8 文字以上） | 実在する別ページに付け替える／1 ブロックの 2 つ目の引用だけ捏造／短文に刻んで検査を逃れる |

G7 は捏造引用に対する最後の歯止めなので、通らないときは**引用文とページ番号を原文に合わせる**（ゲートを緩めない）。

## Usage

```bash
S=~/.claude/skills/combo-transcripts-to-scqa

python3 $S/scripts/extract_transcripts.py session1.pdf session2.pdf session3.pdf \
        --work ./work_scqa
# → 本文を書く（reference/report_template.md に従う）
python3 $S/scripts/check_scqa.py --report ./theme_scqa_260806.md --work ./work_scqa
```

## Output Structure

`reference/report_template.md` を参照。章は `0. 入力一覧` / `1. Situation` /
`2. Complication` / `3. Question` / `4. Answer` / `5. 論点マップ` /
`6. 根拠引用` / `Appendix A 未確定事項` の 8 つで固定。

## Limitations

- **OCR しない**: テキストレイヤの無い PDF は空同然の抽出になる（警告は出るが exit 0）。
- **話者分離をしない**: 抽出は行単位のプレーンテキストで、話者ラベルは文字起こし側の記法に依存する。立場の書き分けは読み手（LLM）の仕事。
- **ページ番号は抽出上の改ページ**であり、PDF に印字されたページ番号とは一致しないことがある。
- **G7 は §6 の blockquote だけを見る**。本文中の引用は逐語照合されない（だから決め手の発言は §6 に置く）。
- **逐語照合は「ページ内・空白無視の部分一致」**: 全空白を落として比較するため、同一ページ内であれば行・段落をまたいだ連結も一致し得る。ページ境界はまたげない。
- **参加者属性・発言母数を構造化しない**: 役割・人数・その立場が多数か少数かは抽出されない。**少数意見を落とさない**のは書き手の責任で、立場欄に「D1 の 1 名のみ」等と明記する。
- **時系列の変化を持たない**: 「初回は対立→後半で合意」といった推移は静的な 合意/対立/未決 に潰れる。重要なら §5 の立場欄か §2 に地の文で書く。
- **セッション横断の重み付けはしない**: 参加人数や回の重要度を機械的に補正しないので、偏りは本文で明示する。
