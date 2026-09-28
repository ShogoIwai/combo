---
name: combo-english-listening-script
description: 英語リスニング兼音読トレーニング用のスクリプトを作る。skill 起動時に渡された「テーマ」に沿って、日本語の「状況」、約1000文字の日本語「会話」、その会話の英訳を、work/ 以下に 3 つの md ファイルとして書き出す。会話文には発言者ラベルやト書きを付けない。「英語リスニング用スクリプトを作って」「音読トレーニングのスクリプト」「リスニング練習の会話文を作って」「英会話スクリプト生成」「listening script」「shadowing script」等で起動。
allowed-tools: Read, Write, Bash
---
# combo-english-listening-script — テーマ → 日本語状況・日本語会話・英語会話 md

One task = one goal. The goal is verified before the task is reported as
complete; if it is not met, the task loops until it is met or the loop limit is
reached.

---

## Task Summary

| Item | Content |
| ---- | ------- |
| **What it does** | 渡されたテーマに沿った場面（状況）を 1 つ決め、その場面の日本語会話（約1000文字）を書き、会話だけを自然な口語英語に訳して、3 つの md に分けて保存する。 |
| **Input** | **テーマ**（skill の引数、または起動時のユーザー発言。必須）。出力先 `--work` は任意で、既定は CWD 直下の `work/`。 |
| **type** | `one-shot` — 1 回の起動で 1 セット。既存の出力ディレクトリは上書きしない（同日・同じ見出しなら `_2`, `_3` … を付けて新規作成）。 |
| **Output** | `work/<slug>_<yymmdd>/` に `ja_situation.md`（テーマ＋状況）、`ja_conversation.md`（日本語会話）、`en_conversation.md`（英語会話）。 |
| **goal** | 3 ファイルが存在し、`scripts/check_script.py` の 8 ゲートが全 PASS すること。 |
| **Verification** | `python3 $S/scripts/check_script.py --dir <出力dir> --theme "<テーマ>"` が exit 0（exit 2 は入力不備）。 |
| **loop limit** | 3（同じゲートで 2 回続けて FAIL したらその時点で打ち切り、`blocked` として報告する） |

`$S` はこの skill のディレクトリ（`combo/skills/combo-english-listening-script`）。

## When to Use

- 英語のリスニング・音読（シャドーイング、オーバーラッピング）用に、日本語と英語の対訳スクリプトがほしい
- 同じテーマで場面を変えたスクリプトを何本も作りたい（起動するたびに新しいディレクトリへ 1 セットずつ作る）

使わない場面: 既存の英文の添削、単語帳づくり、TOEIC 等の試験問題形式の作成。

## Prerequisites

1. **python3**（検査スクリプト用。追加パッケージは不要）
2. **テーマ**が渡されていること。無ければテーマだけを短く尋ねて待つ（ファイルは作らない）。

## Procedure

1. **Fix the goal.** Task Summary の goal / Verification をこの run の唯一の完了条件として固定する。
2. **Decide the output dir.** `<work>/<slug>_<yymmdd>/` を作る。`yymmdd` は当日、`slug` は
   手順 3 で決める**場面を表す見出し**を短い英小文字＋ハイフンで書いたもの（2〜4 語。テーマの
   英訳そのものにはしない。例: テーマ「空港」で乗り継ぎ便に遅れる場面 → `missed-connection_260928`、
   テーマ「仕事」で提案書の締め切りを上司に相談する場面 → `proposal-deadline_260928`）。
   場面を先に決めてからディレクトリ名を付ける。既に存在すれば
   末尾に `_2`, `_3` … を付けて新しく作る（例: `missed-connection_260928_2`）。既存のファイルは上書きしない。
3. **Write `ja_situation.md`（日本語の状況）.**
   ```
   # テーマ: <テーマ（ユーザーの入力そのまま）>

   ## 状況
   <誰と誰が、どこで、何をしている場面か。2〜4文>
   ```
   - 会話の中で誰が話しているかを読み手が判断できるよう、登場人物の立場・関係はここで示す。
4. **Write `ja_conversation.md`（日本語の会話）.**
   ```
   # 会話

   <発言1>

   <発言2>

   ...
   ```
   - **約1000文字**（空白・改行を除いて 900〜1100 文字）、6 発言以上。
   - **1 発言 = 1 段落**（発言ごとに空行で区切る）。発言を鉤括弧で囲まない。
   - **発言者名・話者ラベル・ト書き・括弧の補足を一切付けない**（`A:`、`店員：`、`【客】`、
     `（笑いながら）` などはすべて禁止）。誰の発言かは状況と会話の流れだけで分かるように、
     呼びかけ・質問と返答・相づちを入れる。
5. **Write `en_conversation.md`（英語の会話）.**
   ```
   # Conversation

   <Utterance 1>

   <Utterance 2>

   ...
   ```
   - `ja_conversation.md` の**会話だけ**を訳す（状況は訳さない）。前置き・解説・語注は付けない。
   - **日本語と同じ順序・同じ区切り**で、1 発言ずつ対応させる（日英を突き合わせて音読するため）。
   - 直訳調を避け、ネイティブが実際に話す自然な口語英語にする。話者ラベル・ト書きは付けない。
6. **Verify.**
   ```bash
   python3 $S/scripts/check_script.py --dir <出力dir> --theme "<テーマ>"
   ```
   FAIL があれば該当ファイルを直して再実行する（loop limit まで）。
7. **Report.** 下の Reporting に従う。

## Gates（`check_script.py`）

| Gate | 内容 |
| ---- | ---- |
| G1 theme | `ja_situation.md` にテーマがそのまま書かれている |
| G2 situation | 状況が空でなく日本語で書かれている |
| G3 ja length | 日本語会話が空白・改行を除いて 900〜1100 文字 |
| G4 ja turns | 日本語会話が 6 発言以上 |
| G5 ja no labels | 日本語会話に話者ラベル（`名前：` `【名前】`）や括弧のト書きが無い |
| G6 en no labels | 英語会話に話者ラベル（`Name:` `[Name]`）や `( )` `[ ]` `*…*` のト書きが無い |
| G7 en is English | 英語会話に日本語の文字が混ざっていない |
| G8 ja/en aligned | 日本語と英語の発言数が一致している |

### 決定的に検査できないこと

- 英訳が日本語の内容と意味の上で正しく対応しているか、英語が自然か（G8 は数しか見ない）。
- 話者ラベルなしでも誰の発言か文脈で分かるか。

この 2 点は書き終えたあとに自分で読み直して確認し、Report で「確認した」と明記する。

## その後（任意）

- 英文法に関する追加の質問を受けたら、`en_conversation.md` の英文を例に取りながら日本語で答える。
  質問への回答はファイルに書かない。
- 同じテーマで別のスクリプトを求められたら、Procedure を最初からやり直して別ディレクトリに作る。

## Output Contract

- 出力は `<work>/<slug>_<yymmdd>/` の 3 ファイルだけ。ほかの場所には書かない。
- 既存のディレクトリ・ファイルは上書きしない。
- 会話本文はチャットにも貼らない（ファイルを正本にする）。

## Reporting

完了時に次を報告する。

- 出力ディレクトリのパスと 3 ファイル名
- 状況の要約（1 行）
- `check_script.py` の最終行（PASS/FAIL とゲート数）と、日本語会話の文字数・発言数
- 「決定的に検査できないこと」の 2 点を読み直して確認したこと
