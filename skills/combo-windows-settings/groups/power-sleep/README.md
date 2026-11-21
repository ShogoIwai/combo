# 電源とスリープ: 時間経過・カバーを閉じてもスリープさせない

## 方針

- 画面オフ・スリープまでの時間は、バッテリー駆動時／電源接続時とも「なし」にする。
- ノート PC は、カバー(蓋)を閉じたときの動作をバッテリー駆動時／電源接続時とも「何もしない」にする。
- 対象は**現在アクティブな電源プラン**。プランを切り替えたら再確認する。

## 確認項目(check.ps1 の item 名)

| item | 期待値 | 自動確認 |
| ---- | ------ | -------- |
| `display-off.ac` / `display-off.dc` | 画面オフまでの時間 = なし(0) | 可(`powercfg /qh`) |
| `sleep.ac` / `sleep.dc` | スリープまでの時間 = なし(0) | 可 |
| `lid-close.ac` / `lid-close.dc` | カバーを閉じたとき = 何もしない(0) | 可(バッテリーの無い機種は INFO。読めなければ MANUAL) |

## 手順(GUI)

### 時間が経ってもスリープさせない(設定アプリ)

1. スタート → **設定**(Win + I)
2. **システム** → **電源とバッテリー**(機種により **電源**)
3. **画面とスリープ**(または「画面、スリープ、休止状態のタイムアウト」)を開く
4. 次の 4 項目をすべて **なし**(「適用しない」「しない」「Never」など表記は版により異なる)にする
   - バッテリー駆動時、次の時間が経過後に画面をオフにする
   - 電源に接続時、次の時間が経過後に画面をオフにする
   - バッテリー駆動時、次の時間が経過後に PC をスリープ状態にする
   - 電源に接続時、次の時間が経過後に PC をスリープ状態にする

### ノート PC でカバーを閉じてもスリープさせない(コントロール パネル)

1. スタートメニューで「コントロール パネル」と入力して開く
2. **ハードウェアとサウンド** → **電源オプション**
3. 左メニューの **カバーを閉じたときの動作を選択する**
4. 「カバーを閉じたときの動作」の **バッテリ駆動** / **電源に接続** をそれぞれ **スリープ状態 → 何もしない** に変更
5. **変更の保存**

## コマンドで適用(任意・ユーザーの了承後のみ)

現在の電源プランへの書き込みで、通常は管理者権限なしで通る(組織のポリシーで固定されていれば効かない)。
適用前に `winsettings.sh status power-sleep` で現在値を、`powercfg /getactivescheme` で電源プランの GUID を控える。
元に戻すときは下の「復元の例」を使う(適用に使った `/change` は分単位なので使わない)。
status の時間は**秒**。秒のまま戻せるよう、復元には `/change`(分単位)ではなく `/setacvalueindex` / `/setdcvalueindex` を使う
(90 秒のように 60 で割り切れない値もそのまま戻せる)。カバーの動作は番号のまま入れる(0=何もしない / 1=スリープ / 2=休止状態 / 3=シャットダウン)。

```powershell
# 復元の例(<GUID> は控えたプラン、<秒> / <番号> は status で控えた値。プランを切り替えていても控えたプランへ戻す)
powercfg /setacvalueindex <GUID> SUB_VIDEO VIDEOIDLE <秒>
powercfg /setdcvalueindex <GUID> SUB_VIDEO VIDEOIDLE <秒>
powercfg /setacvalueindex <GUID> SUB_SLEEP STANDBYIDLE <秒>
powercfg /setdcvalueindex <GUID> SUB_SLEEP STANDBYIDLE <秒>
powercfg /setacvalueindex <GUID> SUB_BUTTONS LIDACTION <番号>
powercfg /setdcvalueindex <GUID> SUB_BUTTONS LIDACTION <番号>
powercfg /setactive SCHEME_CURRENT   # 控えたプランが今のプランなら、変更を反映する
```

```powershell
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

## 注意

- カバーを閉じたまま動かし続けると放熱が悪くなる機種がある。持ち運び時は手動でスリープ／シャットダウンする。
- バッテリー駆動時も「なし」にすると、電源を外したまま放置したときにバッテリーを使い切る。
- 休止状態(HIBERNATEIDLE)はこのグループの対象外。必要なら別グループとして足す。
