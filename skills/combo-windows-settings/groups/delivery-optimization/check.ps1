if (-not (Get-Command Get-DOConfig -ErrorAction SilentlyContinue)) {
    Emit MANUAL 'download-mode' 'Get-DOConfig が無い(古い Windows)。画面で確認'
} else {
    $c = Get-DOConfig -ErrorAction SilentlyContinue
    if ($null -eq $c) { Emit MANUAL 'download-mode' 'Get-DOConfig が値を返さない。画面で確認' }
    else {
        $mode = "$($c.DownloadMode)"
        # OFF 相当: HttpOnly/CdnOnly(0)・Simple(99)・Bypass(100) / ON 相当: Lan(1)・Group(2)・Internet(3)
        if ($mode -match '^(HttpOnly|CdnOnly|Simple|Bypass|0|99|100)$') { Emit OK 'download-mode' "$mode(Microsoft のサーバーからのみ)" }
        elseif ($mode -match '^(Lan|Group|Internet|1|2|3)$') { Emit NG 'download-mode' "$mode(他のデバイスからのダウンロードが ON)" }
        else { Emit MANUAL 'download-mode' "DownloadMode を判定できない('$mode')。画面で確認" }
        if ("$($c.DownloadModeProvider)" -match 'Policy|Mdm') { Emit INFO 'download-mode.provider' "$($c.DownloadModeProvider)(組織のポリシーで固定。変更しない)" }
    }
}
