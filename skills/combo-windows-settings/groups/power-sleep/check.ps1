# 電源プラン(SCHEME_CURRENT)の AC/DC 値を powercfg /qh で読む(表示言語に依存しない正規表現で抽出)
# /query は非表示属性の設定(ノート PC でも LIDACTION が非表示のことがある)を出さないので /qh(非表示も含む)を使う
$hasBattery = $null
try { $hasBattery = @(Get-CimInstance Win32_Battery -ErrorAction Stop).Count -gt 0 } catch { }
# 筐体の種類(ノート系: 8 9 10 11 12 14 18 21 30 31 32)。バッテリーが無くても持ち運び型なら蓋がある前提で扱う
$portable = $null
try {
    $types = @((Get-CimInstance Win32_SystemEnclosure -ErrorAction Stop).ChassisTypes | Where-Object { $_ -notin 1,2 })  # 1=Other 2=Unknown
    # 種類が取れないときは不明($null)のまま。不明なら蓋が無いとは判定しない
    if ($types.Count -gt 0) { $portable = @($types | Where-Object { $_ -in 8,9,10,11,12,14,18,21,30,31,32 }).Count -gt 0 }
} catch { }
$items = @(
    @{ Item = 'display-off'; Sub = 'SUB_VIDEO';   Set = 'VIDEOIDLE';   Label = '画面オフまでの時間(秒)' },
    @{ Item = 'sleep';       Sub = 'SUB_SLEEP';   Set = 'STANDBYIDLE'; Label = 'スリープまでの時間(秒)' },
    @{ Item = 'lid-close';   Sub = 'SUB_BUTTONS'; Set = 'LIDACTION';   Label = 'カバーを閉じたときの動作(0=何もしない)' }
)
foreach ($i in $items) {
    $out = & powercfg.exe /qh SCHEME_CURRENT $i.Sub $i.Set 2>$null
    $rc = $LASTEXITCODE
    $ac = $null; $dc = $null
    foreach ($line in $out) {
        if ($line -match 'AC.*:\s*0x([0-9a-fA-F]+)\s*$') { $ac = [Convert]::ToInt64($Matches[1], 16) }
        elseif ($line -match 'DC.*:\s*0x([0-9a-fA-F]+)\s*$') { $dc = [Convert]::ToInt64($Matches[1], 16) }
    }
    if ($null -eq $ac -and $null -eq $dc) {
        # カバーの無い機種と判断するのは、powercfg が成功し、バッテリーが無く、筐体も持ち運び型でないと分かったときだけ
        $noLid = ($i.Item -eq 'lid-close' -and $rc -eq 0 -and $hasBattery -eq $false -and $portable -eq $false)
        foreach ($s in @('ac', 'dc')) {
            if ($noLid) { Emit INFO "$($i.Item).$s" '該当設定なし(バッテリーが無く持ち運び型でない機種)' }
            else { Emit MANUAL "$($i.Item).$s" "値を読めない(powercfg rc=$rc)。画面で確認" }
        }
        continue
    }
    foreach ($p in @(@('ac', $ac), @('dc', $dc))) {
        $name = "$($i.Item).$($p[0])"
        if ($null -eq $p[1]) { Emit MANUAL $name '値を読めない。画面で確認' }
        elseif ($p[1] -eq 0) { Emit OK $name "$($i.Label)=0" }
        else { Emit NG $name "$($i.Label)=$($p[1])(期待値 0)" }
    }
}
