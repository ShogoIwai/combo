# 初回ブートストラップ(最初の 1 つを人が入れる)

スキルはエージェント(Claude Code / Codex)上で動くので、新しいマシンでは
**どちらか 1 つを人が手で入れる**。入れ方は 2 通り。どちらも sudo 不要
(`curl` と `git` が無ければ先に `sudo apt-get install -y curl git` 等を人が実行)。

## A. ドライバを人が直接叩く(推奨)

`combo` リポジトリを clone 済みなら、ドライバは素の bash なのでそのまま使える。
ログインシェルが tcsh でも `bash` で呼べばよい。

```bash
bash <launch-root>/combo/skills/combo-linux-tool-setup/scripts/toolsetup.sh install claude-code --dry-run
bash <launch-root>/combo/skills/combo-linux-tool-setup/scripts/toolsetup.sh install claude-code
# Codex を先に入れるなら claude-code の代わりに codex
```

nvm → node(最新 LTS・default を具体版に固定)→ claude-code の順に入り、
`~/.cshrc` に PATH の管理ブロックが入る。新しい tcsh を開く(または `source ~/.cshrc`)と
`claude` が使える。初回は `claude` を起動して `/login`。

その後は入れたエージェントでこのスキルを使い、残り(`codex` など)を入れる。
スキルを見せるには `<launch-root>/combo/link_skills.sh` を 1 回実行する。

## B. 実績手順そのまま(手作業)

```bash
# nvm install(<ver> は https://github.com/nvm-sh/nvm/releases の最新タグ)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/<ver>/install.sh | bash
bash
source ~/.bashrc          # install.sh が ~/.profile に書いた場合は source ~/.profile
nvm install --lts && nvm use --lts
nvm alias default "$(nvm current)"     # 具体版に固定(lts/* のままにしない)
npm install -g @anthropic-ai/claude-code   # または @openai/codex
```

```csh
# ~/.cshrc に追加(<ver> は nvm current の値)
setenv PATH "$HOME/.nvm/versions/node/<ver>/bin:${PATH}"
```

手で足したこの行は、スキルの初回 `node` 同期時に同じ位置で管理ブロックへ
置き換えられる(バックアップ `~/.cshrc.bak.<日時>.<pid>` を作ってから)。
