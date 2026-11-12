---
name: combo-linux-tool-setup
description: Use when installing, updating, checking, or removing user-level Linux CLI tools on this host (initially nvm / Node.js LTS / Claude Code / Codex / Python 3 built with pyenv python-build), or when adding/removing a tool from the managed set. Japanese triggers — 「ツールをセットアップして」「Claude Code / Codex を入れて」「codex を更新して」「node を最新 LTS に」「nvm 入れて」「python3 を入れて」「pyenv で python をビルドして」「ツールの状態を確認」「セットアップ対象にツールを追加」「linux tool setup」.
---

# combo-linux-tool-setup — Linux 汎用ツールセットアップ

ユーザー領域(`$HOME` 配下)の CLI ツールを、**ツール定義ファイル 1 本＝ 1 ツール**の
データ駆動で導入・更新・削除する。対象は `tools/<name>.sh` を足す／消すだけで増減する。
初期の対象は **Claude Code / Codex** と、その土台の **nvm / node**、および **python3**(pyenv の python-build でビルド)。

原則: **goal が満たされなければ完了とせず、同じタスクを loop する。**
**sudo は絶対に自分で実行しない。** root 権限が要る操作はユーザーに実行を依頼する。

---

## このタスクの概要

| 項目            | 内容 |
| --------------- | ---- |
| **できること**  | 登録ツールの状態確認(導入版/最新版)、依存順の導入、最新への更新、依存元を守った削除、対象ツールの追加・削除・定義更新 |
| **入力**        | 対象ツール名(省略時は status で全件)、操作(install / update / remove / ツール定義の追加・変更)。`bash`・`curl`・`git` が使えること(無ければ sudo 依頼)。python3 を導入・入れ替えるときは**ユーザーが指定する導入先パスと版**(`PYTHON_PREFIX` / `PYTHON_VERSION`) |
| **type**        | `リカーシブ`(導入済みの状態を読み、差分だけ手を入れる。導入済み・最新のものには触れない) |
| **出力**        | 導入・更新されたツール、`~/.cshrc` の管理ブロック(node / python3。変更時は `~/.cshrc.bak.<日時>.<pid>`)、追加・変更した `tools/<name>.sh`、ユーザーへの依頼(SUDO_REQUEST / NEXT_STEPS) |
| **goal**        | 操作ごとに判定し、すべての場合で `toolsetup.sh lint` が `lint OK`。変更系(install / update / remove)は**本実行(`--dry-run` なし)が exit 0** であることが前提で、exit 3/4/5 は status に関係なく未完了(待ち・引き継ぎ。手順 5)。**install**: 加えて依頼された各ツールの `status` が `missing` でない(設定同期まで成功。導入済みの旧版は更新しないので `outdated` のままでも可)。**update**: 加えて `status` が `ok`。**remove**: 加えて `status` が `missing`(python3 は管理外の python3 が残るので、代わりに `.cshrc` に python3 の管理ブロックが無く、消した `<path>/python-<ver>` が無いこと)。**status のみ**: 実行して状態を報告できたこと(`missing` / `outdated` は直さない)。最新版が取れず `ok(latest unknown)` のときは、導入は確認できたが最新かは確認できなかった旨を報告する(update では `検証不能`) |
| **検証**        | `scripts/toolsetup.sh status <tool...>` と `scripts/toolsetup.sh lint` を実際に実行した出力と、変更系(install / update / remove)の本実行の終了コード |
| **loop 上限**   | 3 回 |

ドライバ: `<launch-root>/combo/skills/combo-linux-tool-setup/scripts/toolsetup.sh`
(以下 `TS`)。素の bash スクリプトなので、エージェント無しで人が直接実行してもよい。

| コマンド | 内容 |
| -------- | ---- |
| `TS list` | 登録ツールの一覧(説明・依存) |
| `TS status [tool...]` | 導入版・最新版・状態(`ok` / `outdated` / `missing`) |
| `TS install <tool...>` | 依存ごと導入。導入済みは版を変えず、設定同期(`tool_sync`)だけ行う |
| `TS update <tool...>` | 指定ツールを最新へ。依存は未導入なら導入のみ(勝手に更新しない) |
| `TS remove <tool...> --yes` | 削除。導入済み(または設定が残っている)依存元があれば拒否。未導入で設定だけ残るツールは後始末だけ行う。`tool_owned` を持つツール(python3)は、このスキルが入れた分だけを消し、管理外の導入物は据え置く。削除後に導入版か設定が残れば失敗 |
| `TS lint` | `tools/*.sh` の定義検査(必須関数・依存の実在・sudo 直書き禁止・構文) |
| `--dry-run` | 変更系コマンドと `.cshrc` の差分を表示だけする。ファイル・`~/.nvm` の状態も変えない。未導入の依存は「導入予定」として計画を最後まで流す |

終了コード: `0` 成功 / `1` 失敗 / `2` 使い方誤り(引数不足・未登録ツール・不明オプション) /
**`3` sudo 依頼あり** / **`4` 確認待ち(`--yes` が要る)** / **`5` 自己更新の拒否**。

---

## 手順

1. **概要表の goal・入力・出力・検証を読み、この実行中の唯一の完了判定として固定する**。
   実行中に goal を緩和・変更しない。変更が必要なら作業を止めて理由を報告する。
2. **現状を読む**: `TS lint` と `TS status`(対象指定があればそのツール)を実行し、
   何が `missing` / `outdated` かを列挙する。依頼に未登録のツールが含まれていれば
   手順 4(ツール定義の追加)へ。
   **操作の指定が無い(status のみ)なら、lint の結果と状態を手順 8 の形で報告して終了する。**
   `missing` / `outdated` があっても直さない(必要なら install / update を提案するだけ)。
3. **必ず `--dry-run` を先に流す**(`install` / `update` / `remove` すべて)。
   出てきた変更系コマンドと `.cshrc` の差分をユーザーへの報告に使える形で控える。
   特に次は実行前にユーザーへ確認する:
   - `python3` の **install / update**(導入先パスと版をユーザーに聞き、`PYTHON_PREFIX` / `PYTHON_VERSION` で渡す。
     聞かずに決めない。下の「python3」)
   - `node` の **update**(メジャー版が上がる。新 LTS に旧版のグローバル npm パッケージを
     `--reinstall-packages-from` で引き継ぎ、`~/.cshrc` の PATH を新版へ書き換える)
   - すべての **remove**(`--yes` はユーザーの了承を得てから付ける。exit 4 は確認待ち)
4. **ツール定義の追加・削除・更新**(対象ツールの増減の依頼のとき):
   - 追加: `tools/_template.sh.txt` を `tools/<name>.sh` にコピーし、必須関数
     (`tool_version` / `tool_latest` / `tool_install` / `tool_remove`)と `TOOL_DESC` /
     `TOOL_DEPS` を埋める。npm グローバルなら `claude-code.sh` と同じ 4 行で足りる。
     変更系コマンドは必ず `run "<cmd>"` を通す(dry-run が効くように)。
     `tool_latest` はローカル状態を変えない方法で取る(`nvm version-remote` のように
     キャッシュや別名を書き換えるコマンドは使わない)。
     root 権限が要る前提は `tool_prereq` で `require_cmd` / `need_sudo` に積む。
     エージェント本体になり得るツールは `TOOL_SELF_REGEX`(必要なら `TOOL_SELF_BLOCK`)を書く。
     実体が消えても残る設定(管理ブロック・別名など)を書くツールは `tool_residue`(残存を 1 行ずつ出力)と
     `tool_cleanup`(冪等に消す)も定義する(`node.sh` 参照)。
     PATH 上の既存品を据え置き、自分で入れた分だけを remove の対象にするツールは `tool_owned`
     (このスキルが管理している版)を定義する(`python3.sh` 参照)。版の一致だけでは更新要否を決められない
     ツール(導入先も指定するもの)は `tool_uptodate`(0 = 指定どおり)を定義する。
   - 削除: 導入されているかどうかに関係なく、定義を消す前に `TS remove <name> --yes`
     (ユーザー確認後)を成功させる(未導入でも残存設定は定義が無いと検出・後始末できない)。その後
     `tools/<name>.sh` を消す。ほかのツールの `TOOL_DEPS` に名前が残っていないこと。
   - 定義を変えたら `TS lint` を通してから進む。
5. **実行する**(`--dry-run` を外す)。
   - **exit 3(SUDO_REQUEST)**: 表示された `sudo ...` 行(直前の `#` 行は理由)を
     そのままユーザーに渡し、**自分では実行しない**。Claude Code ならプロンプトで
     `! sudo <cmd>` と打てばこの場で実行できる旨を添える。ユーザーの実行完了を待って
     同じコマンドを再実行する。待ちになった時点で `検証不能`(再開条件 = sudo 実行完了)
     として報告してよい。
   - **exit 4(CONFIRM_REQUIRED)**: ユーザー確認を取ってから `--yes` で再実行。
   - **exit 5(SELF_UPDATE_BLOCKED)**: 自分を動かしているツールの更新・削除。表示された
     コマンドを、もう一方のエージェントかエージェント外の端末で実行するようユーザーに
     依頼する(下の「自己更新」)。回避して自分で実行しない。
6. **概要表の「検証」で goal を判定する**。`TS status <tool...>` と `TS lint` を実際に
   実行して確認する(推測で PASS にしない)。変更系は本実行の終了コードも見る。
   `.cshrc` の管理ブロック(`# >>> combo-linux-tool-setup node >>>` 〜 `<<<`)は、**node が
   処理された操作のときだけ**確認する(依頼外の `.cshrc` 修正に進まないため):
   - node の install / update が走った(依存として導入・同期された場合を含む): ブロックが 1 つだけで、
     指す版が `TS status node` の導入版と一致する。
   - node を remove した: `TS status node` が `missing` で、ブロックが 1 つも無い(マーカーの片方だけ
     残るのも不可)。nvm は残っていてよい。
   - それ以外(`update nvm`、`remove codex` など node が処理されない操作)ではブロックを判定に使わない。
   python3 の管理ブロック(`# >>> combo-linux-tool-setup python3 >>>`)も同じく、**python3 をビルドした／
   remove した操作のときだけ**確認する: ビルドしたならブロックが 1 つだけで、指す `<path>/python-<ver>/bin/python3`
   の版が `PYTHON_VERSION` と一致する。remove したならブロックが無い。据え置き(導入済みで何もしなかった)なら見ない。
7. **未達なら原因を特定して修正し、再検証する(loop)**。次のいずれかで停止:
   goal 達成 / 3 回検証しても未達 / 同じ原因で 2 回連続失敗 / 前回から改善なし /
   ユーザー操作(sudo・ログイン・ネットワーク許可)待ちになった。
8. **報告する**。`完了` / `未達` / `検証不能` のいずれかと:
   - 各ツールの導入版(前→後)、`.cshrc` を変えたならその差分とバックアップの場所。
   - install 後も `outdated` のツールは、その旨と `TS update <tool>` で更新できることを添える(勝手に update しない)。
   - ユーザーに依頼すること: SUDO_REQUEST の行、NEXT_STEPS(`claude` の `/login`、
     `codex login`、tcsh での `source ~/.cshrc`、実行中エージェントの再起動など)。
   - `node` を更新した場合は旧版を残したこと(消すのはエージェント再起動後にユーザー判断)。

---

## 自分自身を更新するとき(ブートストラップと自己更新)

このスキルは Claude Code か Codex の**どちらかが既に動いている**ことを前提に、
もう一方や土台(nvm/node)を整える。**最初の 1 つは人が手で入れる**必要がある。
新しいマシンでの初回手順は `references/bootstrap.md`(実績手順そのもの＋`TS` を
人が直接叩く方法)を参照。

- **自己更新は禁止**。npm の更新・削除は実行中の配置を置き換えるので、走っている
  エージェントの実行ファイルや後から読むファイルが消える(実行ファイルが
  `(deleted)` になった実例あり)。ドライバはプロセスの祖先を `TOOL_SELF_REGEX` で調べ、
  該当すれば exit 5 で止める:
  - `claude-code` の update/remove は Claude Code の中から、`codex` の update/remove は
    Codex の中からは実行できない。もう一方のエージェントか、エージェント外の端末で
    `bash .../toolsetup.sh update claude-code` を実行する。更新後は再起動してもらう。
  - `node` / `nvm` は remove だけ止める(update は旧 node を残し、nvm の update は
    nvm のスクリプトだけを入れ替えるので、実行中のエージェントに影響しない)。
- `node` の update で旧版は消さない。消すのはエージェント再起動後にユーザーが判断する。
- `node` の remove は **nvm 管理下の node を全版**削除し、default 別名と `.cshrc` の
  管理ブロックを外す(旧版が残って `missing` にならない状態を作らない)。node の実体が既に
  無く設定だけ残っている場合も、remove はその後始末(`tool_cleanup`)だけを行う。
  nvm 本体(`nvm.sh`)が失われて版ディレクトリだけ残っている場合、node は導入済みと判定され、
  remove は nvm で消せないので失敗する(`TS install nvm` で nvm を入れ直してから再実行)。
- 未導入でも設定が残っているツールを依存元に持つツール(例: 管理ブロックだけ残った node に対する
  nvm)の remove は拒否される。先に依存元を remove して後始末する。

---

## 管理するシェル初期化ファイル

- ログインシェルが **tcsh** の環境では nvm(bash 関数)が効かないため、`node` は
  `~/.cshrc` に PATH を **管理ブロック**で書く(実績手順の `setenv PATH` 行と同じ中身)。
  ```csh
  # >>> combo-linux-tool-setup node >>>
  setenv PATH "$HOME/.nvm/versions/node/<ver>/bin:${PATH}"
  # <<< combo-linux-tool-setup node <<<
  ```
  手で書かれた同形の行(`$HOME/.nvm/versions/node/<ver>/bin`)は、導入済みでも
  `install` / `update` のたびに走る設定同期で**その位置のまま**ブロックへ置き換える
  (PATH の優先順位を変えない)。書き換え前に必ず `~/.cshrc.bak.<日時>.<pid>` を作り、
  バックアップに失敗したら書かない。ブロックの外は触らない。マーカーが壊れている
  (片方だけ・重複・順序逆)とき、また手書きの行が複数ある・管理ブロックと手書きの行が
  混在するとき(1 つにまとめると間の PATH 行との優先順位が変わり得る)は、何も変えずに
  失敗するので、手で 1 つに整理してもらう。
- nvm の install.sh は bash 側の初期化(`~/.profile` / `~/.bashrc` の `NVM_DIR` 行)を
  初回導入時だけ自分で足す。更新時は `PROFILE=/dev/null` で追記させない(既に
  `~/.profile` にあっても `~/.bashrc` へ重複追記した実例あり)。スキルはそこを編集しない。
- `node` の `default` 別名は**必ず具体版(`<ver>`。先頭の `v` を含む版文字列)に固定**する。`lts/*` のような
  動く別名にすると、別名が進んだ瞬間に `default` が未導入版を指し、導入済みなのに
  `missing` と判定されて意図しない再導入が走る。設定同期は `lts/*` 等を見つけたら
  導入版へ固定し直す。最新 LTS は `nodejs.org/dist/index.json` から取り、
  `~/.nvm/alias/lts/*` を書き換える `nvm version-remote` は使わない。
- 環境変数で対象を差し替えられる: `NVM_DIR`(既定 `~/.nvm`)、`CSHRC`(既定
  `~/.cshrc`)、`NVM_VERSION`(nvm の版を固定)。動作確認は
  `NVM_DIR=<scratch>/nvm PROFILE=/dev/null CSHRC=<scratch>/cshrc` で隔離して行う。

---

## python3(pyenv の python-build でビルド)

- **既に python3 があれば据え置く**。`tool_version` は `~/.cshrc` の管理ブロック → 管理ブロック外の手書きの
  `setenv PATH "<path>/python-<ver>/bin:${PATH}"` 行 → PATH 上の `python3` の順に見て、どれかが動けば
  導入済みとする(`install` は版を変えず何もしない)。`status` の LATEST は `PYTHON_VERSION` を渡したときだけ出る
  (最新版は追わない。渡さなければ `ok(latest unknown)`)。
- **導入先パスと版はユーザーが指定する**。エージェントは決めずに聞き、環境変数で渡す:
  ```bash
  PYTHON_PREFIX=<path> PYTHON_VERSION=<ver> bash .../toolsetup.sh install python3 --dry-run
  PYTHON_PREFIX=<path> PYTHON_VERSION=<ver> bash .../toolsetup.sh install python3
  ```
  `<path>` は絶対パス(英数字と `. _ / + @ , = -` のみ。tcsh が展開する `` ` `` `$` `!` `~` や空白は不可)、
  `<ver>` は `3.12.4`(または `3.14.0rc1` 等)のような具体版。前提チェックで python.org にその版のソース
  tarball があるかを確かめ(dry-run でも行う。接続できなければ確認不能として失敗)、未指定・不正・実在しない版は
  何も変えずに失敗する(exit 1)。clone 後に `python-build --definitions` に無ければそこで止める
  (pyenv の clone/pull は済んでいる)。
- 実行内容は実績手順そのもの:
  ```bash
  cd <path>
  git clone https://github.com/pyenv/pyenv        # 既に clone 済みなら git pull --ff-only
  env PYTHON_CONFIGURE_OPTS="--enable-shared --enable-optimizations --with-lto" \
    pyenv/plugins/python-build/bin/python-build <ver> <path>/python-<ver>
  ```
  ```csh
  # >>> combo-linux-tool-setup python3 >>>
  setenv PATH "<path>/python-<ver>/bin:${PATH}"
  # <<< combo-linux-tool-setup python3 <<<
  ```
  `<path>/python-<ver>` に同じ版が既にあればビルドせず PATH だけ張る。別物があれば止める。
  PGO+LTO のビルドは数十分かかるので、Bash ツールではバックグラウンドで回す。
- 前提(`tool_prereq`): `git` / `make` / `gcc` と、拡張モジュール用の開発ヘッダ(zlib / openssl / libffi /
  bzip2 / readline / sqlite3 / xz / ncurses / uuid)。足りなければ SUDO_REQUEST(exit 3)。
- **別の版・別の導入先へ入れ替えるのは update**(`PYTHON_PREFIX` / `PYTHON_VERSION` 必須)。
  更新要否は版だけでなく導入先も見る(`tool_uptodate`: 使われている python3 が `<path>/python-<ver>/bin` のものか)。新しい版をビルドし、
  管理ブロックを新版へ書き換える。手書きの行が 1 つだけあれば、その位置で管理ブロックへ置き換える
  (PATH の優先順位を変えない。複数ある・管理ブロックと混在するときは何も変えずに失敗)。旧版のディレクトリは消さない。
- **remove は管理ブロックが指す `<path>/python-<ver>` と管理ブロックだけ**を消す(`tool_owned`。
  python3 が起動できなくなっていてもディレクトリが残っていれば `broken` として消す)。消す前に管理ブロックの
  マーカーを検査し、不正なら何も消さずに失敗する。
  手書きの行や PATH 上の python3(システム・共有ディレクトリの python)は管理外として据え置き、何もしない。
  pyenv の clone は残す。
- 動作確認は `CSHRC=<scratch>/cshrc PYTHON_PREFIX=<scratch>/inst` で隔離し、python3 を除いた PATH で行う。

---

## やってはいけないこと

- **`sudo` を自分で実行しない**(Bash ツールでも、ツール定義の中でも)。依頼として出すだけ。
  `TS lint` は `tools/*.sh` 内の `sudo` 直書きを NG にする。
- **`--dry-run` を流さずに install / update / remove を実行しない。**
- **ユーザー確認なしに `--yes` を付けない。** `nvm` の remove は `~/.nvm` ごと
  (全 node・グローバル npm パッケージごと)消える。
- `~/.cshrc` の管理ブロック外、`~/.profile` / `~/.bashrc` を書き換えない。
- python3 の導入先パス・版を自分で決めない(ユーザーに聞く)。管理外の python3 を消さない。
  `.cshrc` の中身を報告に貼るときは管理ブロックの差分だけにする(他の行に認証情報が
  書かれていることがある)。
- 導入物の検査結果を手で書かない(`TS status` の出力をそのまま根拠にする)。
