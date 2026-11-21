# Windows Update 配信の最適化: 他のデバイスからのダウンロードを OFF

## 方針

- 原則 **OFF**(更新プログラムは Microsoft のサーバーからのみ取得する)に統一する。

## 確認項目(check.ps1 の item 名)

| item | 期待値 | 自動確認 |
| ---- | ------ | -------- |
| `download-mode` | 他のデバイスからのダウンロード = OFF(DownloadMode が LAN / Group / Internet 以外) | 可(`Get-DOConfig`。空・未知の値は MANUAL) |

## 手順(GUI)

1. 設定(Win + I)→ **Windows Update** → **詳細オプション** → **配信の最適化**
2. **他のデバイスからのダウンロードを許可する** を **オフ**

## コマンドで適用(任意・ユーザーの了承後のみ)

なし(GUI のみ)。

## 注意

- `DownloadModeProvider` が `GroupPolicy` / `MdmProvider` のときは組織のポリシーで決まっていて、画面の切り替えは効かない(INFO で表示する)。その場合は変更しない。
- DownloadMode の表記は版で異なる(OFF 相当は `HttpOnly` / `CdnOnly` / `Simple` / `Bypass`、ON 相当は `Lan` / `Group` / `Internet`)。
