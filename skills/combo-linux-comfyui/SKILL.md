---
name: combo-linux-comfyui
description: Use when setting up or operating a command-line (no GUI) image-generation environment on Linux with ComfyUI and an NVIDIA GPU — installing ComfyUI, fetching SDXL / Illustrious / NoobAI checkpoints and LoRAs from Civitai / Hugging Face with sha256 checks, turning TensorArt or A1111 generation settings (model, LoRA weights, sampler, scheduler, steps, CFG, clip skip, size) into a reusable preset, expanding a base prompt with per-item templates (place / action / negative) into prompt files, and generating images from prompt files. Japanese triggers — 「ComfyUI を入れて」「ComfyUI をコマンドラインで使いたい」「TensorArt の設定をローカルで再現」「TensorArt と同じ設定で画像生成」「Illustrious / NoobAI をローカルで動かす」「LoRA を落としてプリセットに入れて」「プロンプトファイルから画像生成」「テンプレートでプロンプトを差し替えて量産」「prompts から inputs を作って」「comfyui setup」「comfyui cli」.
---

# combo-linux-comfyui — ComfyUI をコマンドラインで使う画像生成環境

ComfyUI を**ヘッドレスのサーバ**として裏で動かし、`gen.py` が**プリセット（YAML）**から
API ワークフローを組んで投げる。GUI は使わない。モデルはプリセットに入手元と sha256 を書き、
`fetch_models.py` で取得・照合する。

原則:
- **goal が満たされなければ完了とせず、同じタスクを loop する。**
- **sudo は使わない。** Python は venv を作らず、ユーザーが指定する（既定は PATH 上の）`python3` に直接入れる。
  使った Python は `<install-dir>/.comfyui_python` と gen.py / fetch_models.py の shebang に記録され、生成時もそれで動く。
- **API キーをエージェントが転記・表示しない。** 置き場所（下記の token ファイルか環境変数）以外のファイル・ログ・報告・外部サービスに書かない。Civitai のキーは `~/.config/civitai/token`
  （`umask 077`）か `CIVITAI_TOKEN` に置いてもらい、中身は読まない・表示しない。
- **作者が配布を許可していないモデル（TensorArt 専用など）は勝手に取らない。** 代替・なし・再配布版のどれにするかをユーザーに選んでもらう。

---

## このタスクの概要

| 項目 | 内容 |
| --- | --- |
| **できること** | ComfyUI の導入（タグ固定）、モデル取得と sha256 照合、TensorArt / A1111 設定からのプリセット作成、プロンプトファイルからの一括生成、ベースプロンプトの項目をテンプレートで差し替えたプロンプトファイルの量産（`prompts/` → `inputs/`）、サーバの起動・停止 |
| **入力** | 導入先 `<install-dir>`（ユーザー指定）。再現したい生成設定（TensorArt のスクリーンショット・生成情報、または値の指定）。使う Python（既定は PATH 上の `python3`。CUDA 版 torch 導入済みであること）。Civitai から取るなら API キー |
| **type** | `リカーシブ`（導入済みの環境を読み、差分だけ手を入れる。既存のプリセット・inputs・モデルは上書きしない） |
| **出力** | `<install-dir>/`（`ComfyUI/`・`models/`・`presets/<名前>.yaml`・`inputs/`・`prompts/`・`gen.py`・`fetch_models.py`・`process_inputs.py`・`README.md`）、生成画像 `outputs/<日付>/` |
| **goal** | (1) `fetch_models.py --preset <名前> --check` が exit 0（有効なモデルが全部揃い sha256 一致）。(2) `gen.py -P sample --preset <名前> --seed <N>` が exit 0 で PNG を出し、**その画像を開いて**黒画像・ノイズだけ・明らかな崩れが無いことを確認。(3) `gen.py --server stop` でサーバを止めてから同じコマンドを再実行した画像が 1 枚目と画素一致（再現性。止めずに再実行すると ComfyUI のキャッシュで KSampler が再計算されず、比較にならない）。(4) 再現できない項目（入手できない LoRA・ENSD・シードの乱数系・VAE の実体など）を報告に列挙 |
| **検証** | 上の 3 コマンドの実際の終了コードと出力、生成画像の目視、画素比較（`<skill>/scripts/same_image.py A.png B.png` が `identical`） |
| **loop 上限** | 3 回 |

スクリプト（`<skill>` = このスキルのディレクトリ）:

| ファイル | 内容 |
| --- | --- |
| `<skill>/scripts/setup_comfyui.sh <install-dir> [--tag vX.Y.Z] [--python P]` | 前提確認（nvidia-smi・CUDA 版 torch）→ ComfyUI clone・タグ checkout → `pip --dry-run` で torch が入れ替わらないことを確認して requirements 導入 → `extra_model_paths.yaml`・ディレクトリ・スクリプト・雛形を配置。`--update-scripts` で gen.py / fetch_models.py / process_inputs.py だけ差し替え |
| `<skill>/scripts/gen.py` | 生成 CLI（導入先にコピーされる）。使い方は `templates/README.md` |
| `<skill>/scripts/fetch_models.py` | モデル取得・sha256 照合（導入先にコピーされる） |
| `<skill>/scripts/process_inputs.py` | `prompts/<base>.txt` の項目（`Place:` など）を `prompts/template/template-<N>.txt` で差し替え、ネガティブを付け直して（`template-<N>` に無ければ `template-1` の共通ネガティブ） `inputs/<base>-<N>.txt` を作る（導入先にコピーされる）。書式は `templates/README.md` |
| `<skill>/scripts/find_model.py` | Civitai 検索・CivArchive 参照（ID・ファイル名・sha256・ダウンロード可否・ミラー） |
| `<skill>/scripts/same_image.py A B` | 2 枚が画素単位で同一か（サイズ・モード・全チャンネル） |
| `<skill>/templates/prompts/` | `process_inputs.py` の雛形（ベース `sample.txt`・`template/template-{1,2}.txt`。テンプレートは導入先の `prompts/template/` に `template-*.txt` が 1 本も無いときだけ置く） |
| `<skill>/templates/presets/default.yaml` | プリセット雛形（書式の説明つき。`CHANGE_ME` が残っていると gen.py / fetch_models.py は止まる） |
| `<skill>/references/tensorart_to_comfyui.md` | TensorArt / A1111 → ComfyUI の項目対応、モデル入手の判断、つまずいた点 |

---

## 手順

### 1. 前提と導入先を決める

- `nvidia-smi` と、使う Python で `import torch; torch.cuda.is_available()` が True になることを確認する。
  torch が無い・CPU 版なら、ユーザーに PyTorch 公式手順での導入を依頼する（このスキルでは入れない）。
- 導入先 `<install-dir>` はユーザーに確認する。モデルは 1 本数 GB あるので、空きの大きいディスクを選ぶ。
- 既に導入済みなら 2. は `--update-scripts` だけにし、既存のプリセットや models には触れない。

### 2. ComfyUI を導入する

```sh
<skill>/scripts/setup_comfyui.sh <install-dir> [--python <python3>]
```

`requirements.txt が torch を入れ替えようとしています` で止まったら、勝手に `--allow-torch-change` を付けず、
ユーザーに確認する（CUDA 版 torch が CPU 版に置き換わる事故を避ける）。

### 3. 生成設定をプリセットにする

`references/tensorart_to_comfyui.md` の対応表に沿って `presets/<名前>.yaml` を書く
（導入時に置かれる `presets/default.yaml` を編集するか、別名で作る。別名にするなら `default.yaml` は消すか、
毎回 `--preset <名前>`／`COMFYUI_PRESET=<名前>` で指定する。既定は `$COMFYUI_PRESET` → `default.yaml` → `presets/` に 1 本だけならそれ）。スクリーンショットから読むときは、
モデル名・LoRA 名と重み・VAE・幅・高さ・サンプラー・スケジューラ・ステップ・CFG・クリップスキップ・ENSD を
すべて書き出し、プリセットに入らない項目は報告用に控える。

### 4. モデルを探して取得する

```sh
<skill>/scripts/find_model.py civitai "<モデル名>" [--type LORA]
<skill>/scripts/find_model.py civarchive-search "<モデル名>"     # Civitai に無いとき
<install-dir>/fetch_models.py --preset <名前>
```

- 見つけたバージョン ID・ファイル名・sha256 をプリセットに書いてから取得する。
- TensorArt 専用（`allow_download=False`）など入手できないものは、`references/` の手順でユーザーに選んでもらう。
- Civitai のキーが無ければ、**ユーザー自身に**置いてもらう（エージェントはキーを受け取らない・コマンドに書かない）。案内する手順は
  bash で（キーは画面にも履歴にも残らない）:
    mkdir -p ~/.config/civitai && (umask 077; read -rs -p 'Civitai API key: ' k && printf '%s\n' "$k" > ~/.config/civitai/token); echo; chmod 600 ~/.config/civitai/token

### 5. 生成して確かめる（goal）

```sh
<install-dir>/fetch_models.py --preset <名前> --check
<install-dir>/gen.py -P sample --preset <名前> --seed 12345
<install-dir>/gen.py --server stop                               # キャッシュを捨てる
<install-dir>/gen.py -P sample --preset <名前> --seed 12345     # 2 回目
<skill>/scripts/same_image.py <1 回目の PNG> <2 回目の PNG>        # identical なら再現性 OK
```

画像は必ず開いて見る。黒画像なら VAE（fp16-fix 版か）、崩れなら sampler / scheduler / CFG とベースモデルの系統
（v-prediction 系なのに epsilon 用設定など）を疑う。

### 6. 報告

- 導入先・ComfyUI のタグ・使った Python と torch の版
- プリセットの中身と、元の設定から変えた点・再現できない点（代替した LoRA、824 への切り捨て、ENSD、シード、VAE など）
- 生成したファイルのパスと所要時間、画素一致の結果
- サーバを起動したままか（止めるなら `gen.py --server stop`）

---

## 使い方（生成時）

導入後の操作は `<install-dir>/README.md`（`templates/README.md` のコピー）にまとめてある。要点:

```sh
./gen.py -P <inputs の名前> [--seed N] [-c 枚数] [--preset 名前]
./gen.py -p "<プロンプト>" --lora <name>=0.6 --lora-file <file>=0.5
./gen.py --server status | start | stop | log
./process_inputs.py [base ...] [--dry-run]   # prompts/<base>.txt × template-<N> → inputs/<base>-<N>.txt
```

`process_inputs.py` は `inputs/<base>-<N>.txt` を毎回作り直す（範囲外になった番号は消す）。ユーザーが手で直した
`inputs/<base>-<N>.txt` があれば上書きされるので、直すならベースかテンプレートの側を直す。
