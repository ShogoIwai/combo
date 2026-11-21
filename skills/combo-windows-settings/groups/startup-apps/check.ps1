$pat = Opt 'WINSET_STARTUP_DISABLE' 'Teams|Copilot'
$found = 0

# (a) Run キー + StartupApproved(先頭バイトが偶数 = 有効 / 奇数 = 無効)
$sa = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'
foreach ($hive in @('HKCU:', 'HKLM:')) {
    foreach ($pair in @(@('Software\Microsoft\Windows\CurrentVersion\Run', 'Run'),
                        @('Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', 'Run32'))) {
        $rk = Get-Item "$hive\$($pair[0])" -ErrorAction SilentlyContinue
        if ($null -eq $rk) { continue }
        foreach ($n in $rk.GetValueNames()) {
            # 値の名前と、起動コマンドの実行ファイル名(フォルダ・引数は除く)を照合する(別名で登録された自動起動を拾う)
            $cmd = "$($rk.GetValue($n))"
            $exe = if ($cmd -match '^\s*"([^"]+)"') { $Matches[1] } elseif ($cmd -match '^\s*(\S+)') { $Matches[1] } else { '' }
            $exeName = if ($exe) { Split-Path $exe -Leaf } else { '' }
            if ($n -notmatch $pat -and $exeName -notmatch $pat) { continue }
            $found++
            $ap = Get-Item "$hive\$sa\$($pair[1])" -ErrorAction SilentlyContinue
            $b = if ($ap) { $ap.GetValue($n) } else { $null }
            if ($null -ne $b -and ($b[0] % 2) -eq 1) { Emit OK "startup:$n" "無効($hive $($pair[1]))" }
            else { Emit NG "startup:$n" "有効($hive $($pair[1]))" }
        }
    }
}
# (b) スタートアップ フォルダ
foreach ($hive in @('HKCU:', 'HKLM:')) {
    $dir = if ($hive -eq 'HKCU:') { [Environment]::GetFolderPath('Startup') } else { [Environment]::GetFolderPath('CommonStartup') }
    $ap = Get-Item "$hive\$sa\StartupFolder" -ErrorAction SilentlyContinue
    Get-ChildItem $dir -ErrorAction SilentlyContinue | Where-Object { $_.Name -match $pat } | ForEach-Object {
        $found++
        $b = if ($ap) { $ap.GetValue($_.Name) } else { $null }
        if ($null -ne $b -and ($b[0] % 2) -eq 1) { Emit OK "startup:$($_.Name)" '無効(スタートアップ フォルダ)' }
        else { Emit NG "startup:$($_.Name)" '有効(スタートアップ フォルダ)' }
    }
}
# (c) ストアアプリの StartupTask(State: 0/1/3 = 無効、2/4 = 有効)
$sad = 'HKCU:\Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\SystemAppData'
$sadErr = $null
Get-ChildItem $sad -ErrorAction SilentlyContinue -ErrorVariable +sadErr | ForEach-Object {
    $pkg = $_.PSChildName
    Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue -ErrorVariable +sadErr | ForEach-Object {
        $st = $_.GetValue('State')
        if ($null -eq $st) { return }
        if ($pkg -notmatch $pat -and $_.PSChildName -notmatch $pat) { return }
        $found++
        $name = ($pkg -split '_')[0] + '/' + $_.PSChildName
        if ($st -eq 2 -or $st -eq 4) { Emit NG "startup:$name" "有効(StartupTask State=$st)" }
        else { Emit OK "startup:$name" "無効(StartupTask State=$st)" }
    }
}
if ($found -eq 0 -and $sadErr) { Emit MANUAL 'startup:none' 'ストアアプリのスタートアップ情報を読めない。設定 → アプリ → スタートアップ で確認' }
elseif ($sadErr) { Emit MANUAL 'startup:unreadable' ("ストアアプリのスタートアップ情報の一部を読めない({0} 件)。設定 → アプリ → スタートアップ で確認" -f @($sadErr).Count) }
if ($found -eq 0 -and -not $sadErr) { Emit OK 'startup:none' "スタートアップに /$pat/ に一致するものは無い" }

# Teams アプリ内の起動設定(自動起動・バックグラウンドで開く)は読み取れる場所が無いので、導入されていれば目視確認
$teams = Get-ChildItem $sad -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like 'MSTeams_*' }
# 従来版(classic)Teams はユーザー領域に入る
$classic = Test-Path (Join-Path $env:LOCALAPPDATA 'Microsoft\Teams\current\Teams.exe')
if ($teams -or $classic) { Emit MANUAL 'teams-app-settings' 'Teams → 設定 → 全般 → アプリの起動 で自動起動・バックグラウンドで開くがオフか画面で確認' }
else { Emit INFO 'teams-app-settings' 'Teams(新しい Teams・従来版とも)は未導入' }

# 参考: Teams Machine-Wide Installer(入っていると再有効化されることがある)
$mwi = foreach ($u in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
                        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall')) {
    Get-ChildItem $u -ErrorAction SilentlyContinue | Where-Object { "$($_.GetValue('DisplayName'))" -like '*Teams Machine-Wide Installer*' }
}
if ($mwi) { Emit INFO 'teams-machine-wide-installer' '導入あり。無効化しても復活するならアンインストールを検討' }
else { Emit INFO 'teams-machine-wide-installer' '導入なし' }

# 参考: OS 統合 Copilot のポリシー(TurnOffWindowsCopilot=1 で無効)
$pol = @((RegVal 'HKCU:\Software\Policies\Microsoft\Windows\WindowsCopilot' 'TurnOffWindowsCopilot'),
         (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot' 'TurnOffWindowsCopilot')) | Where-Object { $null -ne $_ }
if ($pol -contains 1) { Emit INFO 'windows-copilot-policy' 'ポリシーで無効化済み' }
else { Emit INFO 'windows-copilot-policy' 'ポリシー未設定(組織の方針に従う。このスキルでは変更しない)' }
