# ComfyUI をコマンドラインで使う画像生成環境

ComfyUI をヘッドレスのサーバとして裏で動かし、`gen.py` からプリセットどおりのワークフローを API で投げる。
GUI は使わない（サーバは 127.0.0.1 だけで待ち受ける）。

## 構成

| パス | 内容 |
| --- | --- |
| `ComfyUI/` | ComfyUI 本体（タグ固定）。導入時に指定した python で直接動かす（venv なし。`.comfyui_python` と各スクリプトの shebang に記録） |
| `ComfyUI/extra_model_paths.yaml` | モデル置き場を `models/` に向ける |
| `models/{checkpoints,loras,vae,embeddings,upscale_models}` | モデル |
| `presets/*.yaml` | プリセット（モデル・LoRA の重み・生成パラメータ・入手元・sha256） |
| `inputs/*.txt` | プロンプトファイル（`inputs/sample.txt` が書式例） |
| `gen.py` | 生成 CLI |
| `fetch_models.py` | プリセットのモデルを取得して sha256 を照合 |
| `outputs/<日付>/` | 生成画像（PNG にワークフローが埋め込まれる） |
| `logs/` | サーバログ・pid・ロック |

## 準備

```sh
# Civitai の API キー（User Settings → API Keys）を本人だけ読める形で置く（bash。入力は表示されず履歴にも残らない）
mkdir -p ~/.config/civitai && (umask 077; read -rs -p 'Civitai API key: ' k && printf '%s\n' "$k" > ~/.config/civitai/token); echo
chmod 600 ~/.config/civitai/token      # 環境変数 CIVITAI_TOKEN でも可
./fetch_models.py               # モデル取得・sha256 照合
./fetch_models.py --check       # 有無と sha256 の確認だけ
./fetch_models.py --redownload  # sha256 が合わないファイルを *.bad に退避して取り直す
```

取得は一時ファイルに行い、sha256 が合ったときだけ正式名にする。失敗したモデルは最後に一覧で出す（終了コード 1）。
エラー表示の URL はホストとパスだけ（クエリ・認証情報は出さない）。
`models/<種別>` を共有置き場への symlink にしてよい。モデルファイル自体が symlink の場合は照合だけ行い、
取得・取り直しはしない（リンク先を手で更新する）。

## 生成

```sh
./gen.py -p "masterpiece, best quality, 1girl, solo, ..."      # 初回はサーバ自動起動
./gen.py -P sample --seed 1234 -c 4                            # inputs/sample.txt から・シード 1234〜1237 の 4 枚
./gen.py -P a b                                                # inputs/ の複数ファイルを順に
./gen.py -P inputs/                                            # inputs/ の *.txt を全部
./gen.py -p "..." --preset other                               # presets/other.yaml を使う
./gen.py -p "..." --lora style=0.6 --lora detail=0             # プリセット LoRA の重み変更（0 で外す）
./gen.py -p "..." --lora-file foo.safetensors=0.5              # models/loras/ の任意 LoRA を追加
./gen.py -p "..." -W 832 -H 1216 --steps 30 --cfg 4.5          # パラメータを上書き
./gen.py -p "..." --timeout 600                                # 1 回の生成を待つ上限（秒、既定 1800）
./gen.py -p "..." --dry-run                                    # 送るワークフロー JSON を表示
./gen.py --server status | start | stop | log                  # サーバ操作
```

既定プリセットは `$COMFYUI_PRESET` → `presets/default.yaml` → `presets/` に 1 本だけならそれ。
別名のプリセットを主に使うなら、雛形の `default.yaml` は消すか `COMFYUI_PRESET` を設定する。
`CHANGE_ME` が残ったプリセットは使えない（エラーで止まる）。
ポートは `$COMFYUI_PORT`（既定 8188）。

### プロンプトファイル（`-P`）

- 指定はパス、または `inputs/` 内の名前（`.txt` 省略可）。ディレクトリなら中の `*.txt` 全部。
- ポジティブは複数行に分けてよい（行は空白でつながるので、タグ区切りのカンマは行末にも付ける）。
- `Negative prompt:` で始まる行以降はネガティブ。`Steps: ...` のパラメータ行は無視する。
  無ければプリセットの negative。`-n` が最優先。
- 解釈するのはテキストと上の 2 種類の行だけ。A1111 の `<lora:名前:重み>` タグはエラーにする
  （LoRA は `--lora` / `--lora-file` かプリセットで指定）。パラメータ行の値も反映しない。
- `#` で始まる行はコメント。
- 出力ファイル名の先頭はプロンプトファイル名（`outputs/<日付>/<名前>_<時刻>_s<シード>_00001_.png`）。

### 枚数とシード

- 総枚数 = プロンプト数 × `-c` × `-b`。`-c` の各回はシードを +1 する（プロンプトが複数でも同じシード列）。
- `-b` は 1 回分のバッチ。バッチ内の 2 枚目以降は「seed+1 で 1 枚生成」とは別の絵になる。
- 数値（枚数・サイズ・ステップ・CFG・シード・LoRA 重み）は範囲外ならサーバを起動する前にエラーにする。
- 生成エラー・中断・`--timeout` 超過・キューからの削除はメッセージを出して終了コード 1。

### サーバ

生成後も常駐し、モデルを VRAM に保持する（2 回目以降はロード不要）。使い終わったら `--server stop`。
`logs/comfyui.pid` に pid・起動時刻・ポートを記録し、停止時は今動いているプロセスと照合してから止める
（一致しなければ何も止めず記録だけ消す）。同時に複数の `gen.py` を起動してもサーバは 1 つだけ立つ。
