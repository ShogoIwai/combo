---
name: combo-windows-settings
description: Use when checking or applying grouped Windows PC settings — no sleep / lid-close, system sounds off, Windows Update delivery optimization off, Windows Search options off and dev folders excluded from the index, Teams / Copilot startup off — or when adding a new setting group. Triggers — 「Windows の設定をして」「スリープさせない設定」「Word の保存が遅い」「Teams の自動起動を止めて」「Windows 設定の状態を確認」「windows settings」.
---

# combo-windows-settings — Windows 設定(グループ別)

Windows の設定を**グループ単位**で確認・適用する。グループは `groups/<name>/` に
**手順書(`README.md`)と読み取り専用の確認スクリプト(`check.ps1`)** を置く／消すだけで増減する(データ駆動)。
設定を足すときはドライバも SKILL.md も触らず、グループのディレクトリを 1 つ足す(手順 4)。

初期のグループ:

| グループ | 内容 |
| -------- | ---- |
| `power-sleep` | 時間経過で画面オフ・スリープさせない、ノート PC はカバーを閉じてもスリープさせない |
| `system-sound` | サウンド設定を「サウンドなし」にしてシステムサウンドをまとめて消す |
| `delivery-optimization` | Windows Update の配信の最適化「他のデバイスからのダウンロードを許可する」を OFF |
| `windows-search` | 検索オプションを OFF、開発ディレクトリ・ダウンロードをインデックス対象から除外(Word 保存が遅い件の対策) |
| `startup-apps` | Teams / Copilot のスタートアップ無効化 |

原則: **goal が満たされなければ完了とせず、同じタスクを loop する。**
**Windows の設定はユーザーの了承なしに変えない。管理者権限が要る操作は自分で実行しない(昇格もしない)。**

---

## このタスクの概要

| 項目            | 内容 |
| --------------- | ---- |
| **できること**  | 各グループの状態確認(OK / NG / MANUAL / INFO)、未達の項目の GUI 手順の提示、ユーザーの了承後にグループの README にある「コマンドで適用」の実行、設定グループの追加・削除・更新 |
| **入力**        | 対象グループ(省略時は全件)、操作(status のみ / 適用 / グループの追加・変更)。WSL か Git Bash から `powershell.exe` が呼べること。環境ごとの値は環境変数で渡す(`WINSET_SEARCH_EXCLUDE` = Downloads に加えて除外を確かめる開発ディレクトリ等、`WINSET_STARTUP_DISABLE` = 無効にするアプリ名) |
| **type**        | `リカーシブ`(現在の設定を読み、NG の項目だけに手を入れる。OK の項目には触れない) |
| **出力**        | 状態一覧(`winsettings.sh status` の出力)、ユーザーに実施してもらう GUI 手順(NG / MANUAL の項目だけ)、追加・変更した `groups/<name>/` |
| **goal**        | すべての場合で `winsettings.sh lint` が `lint OK`。**status のみ**: 状態を報告できたこと(NG は直さない)。**適用**: 依頼された各グループの `status` で NG が 0 件(exit 0 または 7)、かつ MANUAL の項目はユーザーが画面で確認した旨の返答がある。INFO は判定に使わない。**グループの追加**: 追加したグループの `status` が ERROR 無く結果行を出す |
| **検証**        | `scripts/winsettings.sh status <group...>` と `scripts/winsettings.sh lint` を実際に実行した出力。MANUAL はユーザーの返答 |
| **loop 上限**   | 3 回 |

ドライバ: `<launch-root>/combo/skills/combo-windows-settings/scripts/winsettings.sh`(以下 `WS`)。
素の bash スクリプトなので、エージェント無しで人が直接実行してもよい。ドライバ自体は**設定を一切変更しない**
(`check.ps1` も読み取り専用。`WS lint` が書き込み系コマンドの直書きを NG にする)。

| コマンド | 内容 |
| -------- | ---- |
| `WS list` | グループ一覧(`README.md` の 1 行目) |
| `WS show <group>` | グループの手順書(`README.md`)を表示 |
| `WS status [group...]` | `check.ps1` を実行し、項目ごとに `OK` / `NG` / `MANUAL`(自動で読めず目視が要る)/ `INFO`(参考)を表示。README の「確認項目」の表にある item が出ていなければ ERROR |
| `WS lint` | `groups/*/` の検査(必須ファイル・README の見出しと確認項目の表・書き込み系コマンドの禁止〈文字列照合なので完全ではない〉・PowerShell の構文〈実行できなければ NG〉) |

終了コード: `0` 全項目 OK / `1` 実行失敗(ERROR 行あり) / `2` 使い方誤り(未登録グループなど) /
`3` `powershell.exe` が無い / **`6` NG あり** / **`7` NG は無いが MANUAL あり**。

環境ごとの値(汎用表現。実際の値はユーザーに聞く):

```bash
WINSET_SEARCH_EXCLUDE='%USERPROFILE%\<開発ディレクトリ>' \
WINSET_STARTUP_DISABLE='Teams|Copilot' \
  bash <launch-root>/combo/skills/combo-windows-settings/scripts/winsettings.sh status
```

`WINSET_*` は `WS` が PowerShell 側へ注入する(`%USERPROFILE%` などは Windows 側で展開される)。
`%USERPROFILE%\Downloads` は `WINSET_SEARCH_EXCLUDE` に関係なく常に確認する(変数は追加分)。

---

## 手順

1. **概要表の goal・入力・出力・検証を読み、この実行中の唯一の完了判定として固定する**。
   実行中に goal を緩和・変更しない。変更が必要なら作業を止めて理由を報告する。
2. **現状を読む**: `WS lint` と `WS status`(対象指定があればそのグループ)を実行する。
   `windows-search` を見るときは、開発ディレクトリの場所をユーザーに聞いて `WINSET_SEARCH_EXCLUDE` で渡す
   (聞かずに決めない。未指定なら `exclude:dev-dir` が MANUAL になる)。
   **操作の指定が無い(status のみ)なら、結果を手順 7 の形で報告して終了する。** NG があっても直さない。
3. **適用する**(NG / MANUAL の項目だけ):
   - `WS show <group>` の「手順(GUI)」から、**NG / MANUAL の項目に当たる部分だけ**を抜き出してユーザーに渡す。
     GUI の表記は Windows の版で違うことがあるので、README の言い回し(「なし / 適用しない / Never」等)ごと渡す。
   - README に「コマンドで適用」があり、ユーザーが自動適用を望んだときだけ、**実行するコマンドを見せて了承を得てから**
     実行する(WSL からは `powershell.exe -NoProfile -Command '<cmd>'`)。実行前に `WS status <group>` の現在値を控え、
     戻し方と一緒に報告する。「なし(GUI のみ)」のグループは自分で設定を書き換えない(レジストリ直書きもしない)。
   - 管理者権限・組織のポリシー・アンインストールが要る対処(Teams Machine-Wide Installer の削除、Copilot のグループ
     ポリシーなど)は、README の手順を渡してユーザーに判断・実施してもらう。昇格(`Start-Process -Verb RunAs` 等)はしない。
   - `INFO` で組織のポリシーが設定を固定していると出た項目は変更しない(その旨を報告する)。
4. **設定グループの追加・削除・更新**(設定を増やす／減らす依頼のとき):
   - 追加: `groups/_template/` を `groups/<name>/` にコピーし、`check.ps1.txt` を `check.ps1` に改名して埋める。
     `<name>` は英小文字とハイフン(`_` で始まる名前は対象外)。
     - `README.md`: 1 行目 `# <タイトル>`(`WS list` に出る)、方針、`## 確認項目` の表(1 列目に `` `item` ``。`WS status` はここにある item が
       すべて出ることを確かめる。`<…>` を含む item は接頭辞の一致で足りる)・期待値・自動確認の可否、
       手順(GUI)、コマンドで適用(管理者権限が要らず元に戻せるものだけ。無ければ「なし(GUI のみ)」)、注意。
     - `check.ps1`: **読み取り専用**。`Emit <OK|NG|MANUAL|INFO> <item> [detail]` で 1 項目 1 行を出す
       (`R` は PowerShell の既定エイリアスと衝突するので使わない)。item 名は README の表と揃える。
       自動で読めない項目は `MANUAL`、判定に使わない参考は `INFO`。表の item は条件によらず毎回 1 行出す(該当なしも INFO 等で出す)。
       読み取りの失敗・空・未知の値を OK にしない(MANUAL にする)。環境ごとの値は `Opt 'WINSET_<名前>' '<既定>'` で読み、
       README に変数名と書式を書く。値を出力するときは表示言語に依存しない読み方(レジストリ・`powercfg` の 16 進値等)を選ぶ。
     - ユーザー固有の値(アカウント名・実パス・組織名)を README / check.ps1 に書かない。`%USERPROFILE%\<…>` などの汎用表現にする。
   - 削除: `groups/<name>/` を消すだけ(設定は元に戻さない。戻すなら README の手順でユーザーが行う)。
   - 定義を変えたら `WS lint` を通し、`WS status <name>` が ERROR 無く結果行を出すことを確かめてから進む。
5. **概要表の「検証」で goal を判定する**。`WS status <group...>` と `WS lint` を実際に実行して確認する
   (ユーザーが「設定した」と言っても、自動確認できる項目は status の出力を根拠にする)。
   MANUAL の項目はユーザーに画面での確認を依頼し、その返答を根拠にする。
6. **未達なら原因を特定して修正し、再検証する(loop)**。次のいずれかで停止:
   goal 達成 / 3 回検証しても未達 / 同じ原因で 2 回連続失敗 / 前回から改善なし /
   ユーザー操作(GUI での設定・管理者権限・組織への確認)待ちになった(`検証不能`、再開条件 = ユーザーの実施完了)。
7. **報告する**。`完了` / `未達` / `検証不能` のいずれかと:
   - グループごとの結果(OK / NG / MANUAL の件数と、NG・MANUAL の item)。
   - コマンドで適用したなら、実行したコマンド・変更前の値・戻し方。
   - ユーザーに依頼すること(NG / MANUAL の GUI 手順、管理者・組織に確認すべきこと)。

---

## グループごとの注意

- **power-sleep**: 対象は現在アクティブな電源プラン。プランを切り替えたら再確認する。値は `powercfg /qh`(非表示の設定も出す)で読む
  (ノート PC でも LIDACTION が `/query` に出ないことがある)。バッテリーの無い機種は `lid-close.ac/.dc` が INFO になる。バッテリー駆動時も「なし」にするとバッテリーを使い切り得る旨を報告に添える。
- **system-sound**: スキーム名だけをレジストリで書き換えても各イベントの音は消えないので、必ず `mmsys.cpl` で切り替える。
  アラーム/着信のループ音・ログオン音は「サウンドなし」でも残る仕様なので判定から除いている。
- **delivery-optimization**: `DownloadModeProvider` がポリシー由来なら組織の設定なので変更しない。
- **windows-search**: Windows Search サービス自体の停止・無効化は対象外(スタートメニュー等の検索が壊れる)。
  セーフサーチも対象外(Web 検索結果のフィルタで、インデックスの負荷に関係しない)。
  インデックスの除外ルールの書き込みは管理者権限が要るので、除外の追加は GUI でユーザーに行ってもらう。
- **startup-apps**: OS 側のスタートアップ無効化を最優先にする。Teams が入っていればアプリ内の起動設定は MANUAL(画面で確認してもらう)。アンインストール・グループ ポリシーは組織の方針を優先し、
  このスキルからは行わない。

---

## やってはいけないこと

- **ユーザーの了承なしに Windows の設定を変えない。** `check.ps1` に書き込み系コマンドを書かない(`WS lint` が NG にする)。
- **管理者権限への昇格・UAC の回避をしない。** 管理者権限が要る操作は手順を渡すだけ。
- README に「コマンドで適用」が無いグループで、レジストリを直接書き換えて済ませない。
- 組織のポリシーで固定された設定(INFO でポリシー由来と出たもの)を変えない。
- 状態の判定を手で書かない(`WS status` の出力をそのまま根拠にする)。
- README / check.ps1 にユーザー固有の値(アカウント名・実パス・組織名)を書かない。`%USERPROFILE%\<…>` などの汎用表現にし、
  実際の値は `WINSET_*` で実行時に渡す。
