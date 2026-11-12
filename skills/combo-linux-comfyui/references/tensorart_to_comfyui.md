# TensorArt（A1111 系）の設定を ComfyUI プリセットへ写す

TensorArt の生成画面（モデル欄・パラメータ欄のスクリーンショットや、画像の生成情報）から
`presets/<名前>.yaml` を起こすときの対応表と、実際につまずいた点。

## 項目の対応

| TensorArt / A1111 | プリセット / ComfyUI ノード | 注意 |
| --- | --- | --- |
| ベースモデル | `checkpoint.file`（CheckpointLoaderSimple） | TensorArt の表示名と Civitai のバージョン名がずれることがある（例:「BASE V1」↔「BASE 1」）。作者・公開日・ファイルサイズで同定し、どれとみなしたかを報告する |
| LoRA と重み | `loras[].weight`（LoraLoader の strength_model と strength_clip に同じ値） | TensorArt は 1 つの重みを両方にかける。並び順は画面の上から |
| VAE `sdxl_vae.safetensors` | `vae`（VAELoader） | 名前だけでは原版か fp16-fix 版か確定できない。fp16-fix 版（`madebyollin/sdxl-vae-fp16-fix`）を使う。原版とはごくわずかに違う |
| 幅・高さ | `params.width/height`（EmptyLatentImage） | latent は 8px 単位。8 の倍数でない値（例 828）は切り捨て（824）。TensorArt 側の実出力サイズは未確認と書く |
| サンプラー / スケジューラ | `params.sampler/scheduler`（KSampler） | 名前はほぼ同じ（`euler`, `euler_ancestral`, `dpmpp_2m`, `sgm_uniform`, `karras` …）。A1111 の「DPM++ 2M Karras」は sampler `dpmpp_2m` + scheduler `karras` |
| サンプリング回数 / CFG | `params.steps/cfg` | そのまま |
| クリップスキップ | `params.clip_skip`（CLIPSetLastLayer `-N`） | SDXL 系はもともと最後から 2 層目を使うので、SD1.5 ほどの差は出ない |
| ENSD（Eta Noise Seed Delta） | 対応なし | A1111 で ENSD を使うと定義されたサンプラー（ancestral / SDE 系など）の追加ノイズ用。通常の euler 等では使われない。ancestral 系で使っていた場合は「再現できない」と報告する |
| シード | `--seed` | 初期ノイズの作り方が違う（A1111 系は GPU 乱数、ComfyUI は CPU 乱数）ので、同じシードでも同じ絵にはならない |
| 強調 `(word:1.2)` | そのまま CLIPTextEncode に渡る | 重みの正規化が A1111 と違い、ComfyUI の方がやや強く効く |
| プロンプト中の `<lora:...>` | 使えない（gen.py がエラーにする） | プリセットの loras か `--lora-file` に移す |

## モデルの入手

1. **Civitai を探す**: `scripts/find_model.py civitai "<名前>" --type LORA`。
   見つかったらバージョン ID・ファイル名・sha256 をプリセットに書く。ダウンロードは API キーが必要。
2. **見つからなければ TensorArt 専用を疑う**: TensorArt のページは Cloudflare で直接読めないので、
   `scripts/find_model.py civarchive-search "<名前>"` → `civarchive <ページ>` で
   ファイル名・sha256・`allow_download`・ミラーを確認する。
3. **TensorArt 専用（`allow_download=False`）**: TensorArt からは課金してもダウンロードできない。
   第三者が Hugging Face 等に再アップロードしていることがあるが、作者が配布を許可していないものなので、
   使うかどうかはユーザーに選んでもらう（勝手に取らない）。選択肢は次の 3 つ:
   - Civitai の近い画風の LoRA で代替する（同じ作者・同じ画風名・同じ baseModel を優先）
   - その LoRA なしで進める
   - 再アップロード版を使う（ユーザーが承知の上で）
   原版の情報（ファイル名・sha256・TensorArt ページ）はプリセットに `weight: 0` で残し、
   入手できたら原版の weight を入れて代替側を 0 にする（両方有効だと重ね掛けになる）。
4. baseModel が Illustrious / NoobAI / Pony / SDXL 1.0 のどれかを確認する。Anima・Flux・Krea 等の
   別系統の LoRA は SDXL のベースモデルには使えない。

## つまずいた点

- **Civitai のダウンロードで 401**: API キーが必要（検索・メタデータの API は無認証で通る）。
- **Civitai のダウンロードで 400**: `Authorization: Bearer` ヘッダで渡すと、リダイレクト先の
  署名付き URL にもヘッダが付いて 400 になる。`?token=` のクエリで渡す（fetch_models.py はそうしている。
  エラー表示では token を伏せる）。
- **requirements.txt で torch が入れ替わる**: ComfyUI の requirements は torch を版指定しないので
  通常は入れ替わらないが、setup_comfyui.sh は `pip install --dry-run` で確かめてから入れる。
- **ディレクトリを移動・改名したら**: `ComfyUI/extra_model_paths.yaml` の `base_path` を直し、
  サーバを止めてから移す（pid ファイルの記録とずれる）。移動直後の初回はモデルの読み直しで遅い。
- **原版と見た目を比べたいとき**: 同じ設定・同じシードで 2 回生成した画像が画素一致することを
  確かめておくと、以後の改修で再現性が崩れていないか比べられる。
