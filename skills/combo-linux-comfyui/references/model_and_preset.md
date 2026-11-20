# モデルの探し方とプリセットの書き方

ユーザーの指定（モデル名・LoRA 名と重み・生成パラメータ）から `presets/<名前>.yaml` を起こすときの
手順と、実際につまずいた点。

## モデルを探す（指定があるとき）

checkpoint・LoRA・VAE の指定があれば、**そのものを探し、無ければそれに近いものを探す**。
どれを選んだか（そのものか、近いものか）は必ず報告する。

1. **そのものを探す**
   - Civitai: `scripts/find_model.py civitai "<名前>" [--type LORA|Checkpoint]`。
     バージョン名は表記がずれることがある（例:「BASE V1」↔「BASE 1」）。作者・公開日・ファイルサイズで同定する。
   - ハッシュが分かっていれば `scripts/find_model.py sha256 <sha256>` で同一物か確かめる（ファイル名が違っても同じものを拾える）。
   - Hugging Face: `scripts/find_model.py hf "<名前>"` → `hf-files <repo>` で、ファイル名と sha256 を得る。
   - Civitai で削除・非公開になったもの: `scripts/find_model.py civarchive-search "<名前>"` → `civarchive <ページ>` で
     ファイル名・sha256・baseModel・`allow_download`・ミラーを確かめる。CivArchive の結果は Civitai と Hugging Face の
     記録だけを出す（それ以外の配布元は扱わない）。`sha256` も CivArchive に記録された Hugging Face のミラーまで出す。
   - ミラー（Hugging Face 等への再アップロード）は、作者が配布を許可しているもの（元ページの `allow_download=True` など）だけを使う。
   - 見つかったらバージョン ID（Civitai）か `hf_repo`・`hf_file`（Hugging Face）と、ファイル名・sha256 をプリセットに書く。
2. **見つからない・入手できないときは近いものを探す**
   - 近さの優先順: 同じ作者（`--user`）→ 同じ画風名・同じキーワード → 同じ baseModel（`--base`）。
   - 候補を 2〜3 件挙げ、どれを使うか（または使わないか）をユーザーに選んでもらう。勝手に決めない。
   - 作者が配布を許可していないもの（`allow_download=False` など）の再アップロード版は候補にしない。
3. **baseModel を合わせる**: checkpoint と LoRA の baseModel（Illustrious / NoobAI / Pony / SDXL 1.0 など）を
   確かめる。別系統（SD1.5・Flux など）の LoRA は SDXL のベースモデルには使えない。
4. 指定のものと近いものを切り替えて比べたいときは、両方をプリセットに書いて使わない側を `weight: 0` にする
   （両方有効だと重ね掛けになる）。

## 生成パラメータの書き方

| 指定 | プリセット / ComfyUI ノード | 注意 |
| --- | --- | --- |
| ベースモデル | `checkpoint.file`（CheckpointLoaderSimple） | 上の「モデルを探す」で同定したもの |
| LoRA と重み | `loras[].weight`（LoraLoader の strength_model と strength_clip に同じ値） | 1 つの重みを両方にかける |
| VAE | `vae`（VAELoader） | SDXL で黒画像になるなら fp16-fix 版（`madebyollin/sdxl-vae-fp16-fix`）を使う |
| 幅・高さ | `params.width/height`（EmptyLatentImage） | latent は 8px 単位。8 の倍数でない値（例 828）は切り捨て（824）になるので報告する |
| サンプラー / スケジューラ | `params.sampler/scheduler`（KSampler） | ComfyUI の名前で書く（`euler`, `euler_ancestral`, `dpmpp_2m`, `sgm_uniform`, `karras` …）。「DPM++ 2M Karras」のような表記は sampler `dpmpp_2m` + scheduler `karras` |
| ステップ / CFG | `params.steps/cfg` | そのまま |
| クリップスキップ | `params.clip_skip`（CLIPSetLastLayer `-N`） | SDXL 系はもともと最後から 2 層目を使うので、SD1.5 ほどの差は出ない |
| シード | `--seed` | 同じ値でも、生成環境が違えば同じ絵にはならない（初期ノイズの作り方が違う） |
| プロンプト中の `<lora:...>` | 使えない（gen.py がエラーにする） | プリセットの loras か `--lora-file` に移す |

## つまずいた点

- **Civitai のダウンロードで 401**: API キーが必要（検索・メタデータの API は無認証で通る）。
- **Civitai のダウンロードで 400**: `Authorization: Bearer` ヘッダで渡すと、リダイレクト先の
  署名付き URL にもヘッダが付いて 400 になる。`?token=` のクエリで渡す（fetch_models.py はそうしている。
  エラー表示では token を伏せる）。
- **requirements.txt で torch が入れ替わる**: ComfyUI の requirements は torch を版指定しないので
  通常は入れ替わらないが、setup_comfyui.sh は `pip install --dry-run` で確かめてから入れる。
- **ディレクトリを移動・改名したら**: `ComfyUI/extra_model_paths.yaml` の `base_path` を直し、
  サーバを止めてから移す（pid ファイルの記録とずれる）。移動直後の初回はモデルの読み直しで遅い。
- **改修の前後で見た目を比べたいとき**: 同じ設定・同じシードで 2 回生成した画像が画素一致することを
  確かめておくと、以後の改修で再現性が崩れていないか比べられる。
