---
name: combo-linux-git-bare-repo
description: Use when creating an on-premises (self-hosted, file-system based) shared git remote repository on Linux — a bare repository that a group of users push to over a shared disk or ssh. Japanese triggers — 「リモートリポジトリを作って」「オンプレでリモートリポジトリ作成」「bare リポジトリを作って」「共有リポジトリを作って」「git init --bare して」「push 先のリポジトリを作って」「create bare repo」「shared remote repository」.
---

# combo-linux-git-bare-repo — オンプレミスのリモートリポジトリ作成

共有ディスク上に、グループで push できる **bare リポジトリ**を作る。
手順は次の 4 行で固定（順番も変えない）:

```bash
mkdir <rep>
chmod 770 <rep>
chmod g+s <rep>
git init --bare --shared=true <rep>
```

- `chmod 770` … 所有者とグループだけ読み書き可、その他は不可。
- `chmod g+s` … setgid。配下に作られるファイル・ディレクトリが `<rep>` のグループを引き継ぐ。
- `--shared=true` … `core.sharedRepository=1`(group)。git が作るオブジェクトをグループ書き込み可にする。

原則:
- **goal が満たされなければ完了とせず、同じタスクを loop する。**
- **既存のパスは上書きしない。** `<rep>` が既にあれば（空ディレクトリでも）作らずにユーザーへ確認する。
- **sudo は使わない。** 親ディレクトリに書き込み権が無い・グループを変える（`chgrp`）必要があるときはユーザーに依頼する。
- **初回 push・clone はしない**（依頼されたときだけ）。

---

## このタスクの概要

| 項目 | 内容 |
| --- | --- |
| **できること** | 共有 bare リポジトリの作成、既存リポジトリの権限・共有設定の検査 |
| **入力** | 作成するパス `<rep>`（ユーザー指定。慣例で末尾 `.git`、例 `<共有ディスク>/git/<name>.git`）。親ディレクトリは既存であること |
| **type** | `新規`（既存のパスには手を入れない） |
| **出力** | `<rep>/`（bare リポジトリ、mode `2770`、`core.sharedRepository=1`） |
| **goal** | `create_bare_repo.sh --check <rep>` が exit 0（最上位が mode 2770・bare・`<rep>/config` の `core.sharedRepository=1`、配下が全て同じグループでディレクトリ g+rwxs・ファイル g+r）。`WARN`（祖先ディレクトリをグループ・その他が辿れない）は報告する |
| **検証** | `--check` の実際の出力と終了コード |
| **loop 上限** | 3 回 |

スクリプト（`<skill>` = このスキルのディレクトリ）:

| ファイル | 内容 |
| --- | --- |
| `<skill>/scripts/create_bare_repo.sh [--dry-run] <rep>` | 前提確認（`<rep>` が無い・親が在って書ける）→ 上の 4 行を順に実行 → 検査。exit 0 = OK / 1 = 検査 NG・コマンド失敗 / 2 = 引数・前提エラー（何も変更していない） |
| `<skill>/scripts/create_bare_repo.sh --check <rep>` | 検査のみ（何も変更しない）。`HEAD` の指すブランチも表示する |

スクリプトは `GIT_DIR`・`GIT_OBJECT_DIRECTORY` など作成先を `<rep>` 以外へ向け得る git の環境変数を外して実行し、`core.sharedRepository` は global/system 設定を拾わないよう `<rep>/config` だけを読む。

---

## 手順

### 1. パスを決める

- `<rep>` はユーザーに確認する（推測で作らない）。相対パスで渡されたら絶対パスにして確認する。
- グループの指定があれば、親ディレクトリのグループ（`stat -c %G <親>`）と一致するか見る。
  `mkdir` で作られる `<rep>` のグループは、親が setgid なら親のグループ、そうでなければ自分の一次グループになる。
  違うグループにしたいときは、作成後に `chgrp -R <group> <rep>` が要る（自分がそのグループに属していれば sudo 不要。属していなければユーザーに依頼）。

### 2. 作成する

```bash
<skill>/scripts/create_bare_repo.sh --dry-run <rep>   # 実行内容の確認
<skill>/scripts/create_bare_repo.sh <rep>
```

- exit 2 で `既に存在する` と出たら作らない。既存のものが目的のリポジトリかをユーザーに確認し、
  必要なら `--check` だけ回して報告する。
- `chgrp` した場合は、その後にもう一度 `--check` を回す。

### 3. 検査して報告する

```bash
<skill>/scripts/create_bare_repo.sh --check <rep>
```

報告には次を書く:
- 作成したパスと `--check` の出力（mode・bare・sharedRepository・group・配下の権限・WARN・HEAD）。
- **HEAD のブランチ**: bare の `HEAD` は `init.defaultBranch`（未設定なら `master`）を指す。初回 push するブランチがこれと違うと、push は通っても clone でチェックアウトされない。
  初回 push のブランチを `HEAD` に合わせるか、ユーザーの了承を得て `git --git-dir=<rep> symbolic-ref HEAD refs/heads/<branch>` で合わせる（固定の 4 行は変えない）。
- 使い方（clone / 既存リポジトリからの push 先登録）:

```bash
git clone <rep>                                 # 同じホスト・共有ディスク
git clone ssh://<host><rep>                     # 別ホストから ssh（<rep> は / 始まりの絶対パス）
git remote add origin <rep> && git push -u origin <branch>   # 既存リポジトリを登録して初回 push
```

---

## つまずきやすい点

- `--shared=true` は既存の `<rep>` に後から `git init` しても**既存ファイルの権限は直らない**。だから作成は必ず空の状態から 4 行を通す。
- 利用者の `umask` が 077 でも、`core.sharedRepository=1` により git が書くオブジェクトはグループ書き込み可になる。ただし `git` 以外（`cp` 等）で中に置いたファイルは対象外なので、中を手で触らない。
- 770 なのでグループ外のユーザーは clone もできない。読み取り専用で公開したい人がいれば、グループに入れてもらうようユーザーへ伝える（権限を緩めない）。
