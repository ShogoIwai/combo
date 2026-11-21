$scheme = RegVal 'HKCU:\AppEvents\Schemes' '(default)'
if ($null -eq $scheme) { Emit MANUAL 'sound-scheme' 'スキーム名を読めない。画面で確認' }
elseif ($scheme -eq '.None') { Emit OK 'sound-scheme' '.None(サウンドなし)' }
else { Emit NG 'sound-scheme' "$scheme(期待値 .None = サウンドなし)" }

# 各イベントの .Current に音声ファイルが入っている件数
# 「サウンドなし」でも消えない・ダイアログに出ないイベントは除く:
#   Notification.Looping.*(アプリが選ぶアラーム/着信音)、WindowsLogon / WindowsUnlock(起動音のチェックボックスで制御)
$skip = '^(Notification\.Looping\..*|WindowsLogon|WindowsUnlock)$'
$assigned = @()
$readErr = $null
$apps = @(Get-ChildItem 'HKCU:\AppEvents\Schemes\Apps' -ErrorAction SilentlyContinue -ErrorVariable +readErr)
$apps | ForEach-Object {
    Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue -ErrorVariable +readErr | ForEach-Object {
        if ($_.PSChildName -match $skip) { return }
        $cur = Join-Path $_.PSPath '.Current'
        if (Test-Path $cur) {
            $wav = (Get-Item $cur).GetValue('')
            if (-not [string]::IsNullOrWhiteSpace($wav)) { $assigned += $_.PSChildName }
        }
    }
}
if ($apps.Count -eq 0 -or $readErr) { Emit MANUAL 'event-sounds' ("イベントの一覧を読めない({0} 件の読み取りエラー)。画面で確認" -f @($readErr).Count) }
elseif ($assigned.Count -eq 0) { Emit OK 'event-sounds' '音の割り当て 0 件' }
else { Emit NG 'event-sounds' ("音の割り当て {0} 件(例: {1})" -f $assigned.Count, (($assigned | Select-Object -First 3) -join ', ')) }
