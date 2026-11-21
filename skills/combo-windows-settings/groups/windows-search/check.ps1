# 1) 検索オプション(HKCU\...\SearchSettings。1=ON / 0=OFF)
$ss = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\SearchSettings'
$opts = @(
    @('search.cloud-msa',   'IsMSACloudSearchEnabled',      'クラウド コンテンツの検索(Microsoft アカウント)'),
    @('search.cloud-aad',   'IsAADCloudSearchEnabled',      'クラウド コンテンツの検索(職場または学校アカウント)'),
    @('search.history',     'IsDeviceSearchHistoryEnabled', 'このデバイスの検索履歴'),
    @('search.highlights',  'IsDynamicSearchBoxEnabled',    '検索のハイライト')
)
foreach ($o in $opts) {
    $v = RegVal $ss $o[1]
    if ($null -eq $v) { Emit MANUAL $o[0] "$($o[2]): 値が無い(未設定 = 既定で ON の可能性)。画面で OFF にする" }
    elseif ($v -eq 0) { Emit OK $o[0] "$($o[2]) = OFF" }
    else { Emit NG $o[0] "$($o[2]) = ON" }
}

# 2) インデックスの除外ルール(CrawlScopeManager の WorkingSetRules。読み取りのみ)
# Downloads は常に確認し、WINSET_SEARCH_EXCLUDE(開発ディレクトリ等)はそれに足す
$raw = Opt 'WINSET_SEARCH_EXCLUDE' ''
$extra = @($raw.Split(';') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
# 表記ゆれ(末尾の \ や \. 等)を GetFullPath で正規化してから比べる
function NormPath([string]$p) { try { return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($p)).TrimEnd('\') } catch { return $p.TrimEnd('\') } }
$dlFull = NormPath '%USERPROFILE%\Downloads'
$dev = @($extra | Where-Object { (NormPath $_) -ne $dlFull })
$targets = @('%USERPROFILE%\Downloads') + $dev
if ($dev.Count -eq 0) {
    Emit MANUAL 'exclude:dev-dir' '開発ディレクトリが未指定。WINSET_SEARCH_EXCLUDE で渡す(ユーザーに場所を聞く)'
} else {
    Emit OK 'exclude:dev-dir' ("指定あり({0} 件)" -f $dev.Count)
}
$rules = @()
$ruleErr = $null
$base = 'HKLM:\SOFTWARE\Microsoft\Windows Search\CrawlScopeManager\Windows\SystemIndex\WorkingSetRules'
Get-ChildItem $base -ErrorAction SilentlyContinue -ErrorVariable +ruleErr | ForEach-Object {
    $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue -ErrorVariable +ruleErr
    if ("$($p.URL)" -like 'file:///*') {
        # file:///<drive>:\[{volume-guid}]\<path>\ → <drive>:\<path>\
        $path = ("$($p.URL)" -replace '^file:///', '') -replace '^([A-Za-z]:)\\\[[^\]]*\]', '$1'
        # Include が無いルールは種別を判定できない(0 扱いにすると誤って除外済みになる)ので読み取り失敗として扱う
        if ($null -eq $p.Include) { $ruleErr = @($ruleErr) + "Include が無い: $path" }
        else { $rules += [pscustomobject]@{ Path = $path; Include = [int]$p.Include } }
    }
}
if ($rules.Count -eq 0 -or $ruleErr) {
    # 一部でも読めなければ判定しない(配下の Include を取りこぼすと誤って OK になる)
    foreach ($t in $targets) { Emit MANUAL "exclude:$t" ("インデックスのルールを読めない({0} 件の読み取りエラー)。画面(Windows の検索)で確認" -f @($ruleErr).Count) }
} else {
    foreach ($t in $targets) {
        $full = (NormPath $t) + '\'
        # ルールのパスは * だけをワイルドカードとして扱い、[ ] など他の文字はそのまま比べる
        $covering = @($rules | Where-Object {
            $pattern = [Management.Automation.WildcardPattern]::Escape($_.Path.TrimEnd('\') + '\').Replace('`*', '*') + '*'
            $full -like $pattern
        })
        # 対象を覆うルールのうち最も具体的(長い)もので判定する
        $hit = $covering | Sort-Object { $_.Path.Length } -Descending | Select-Object -First 1
        # 対象より下に Include のルールがあれば、その部分はインデックスされる
        $inner = @($rules | Where-Object {
            $_.Include -eq 1 -and $_.Path.Length -gt $full.Length -and
            $_.Path.StartsWith($full, [StringComparison]::OrdinalIgnoreCase)
        })
        if ($inner.Count -gt 0) { Emit NG "exclude:$t" ("配下がインデックス対象({0} が Include)" -f $inner[0].Path) }
        elseif ($null -ne $hit -and $hit.Include -eq 0) { Emit OK "exclude:$t" "除外済み($($hit.Path))" }
        elseif ($null -ne $hit) { Emit NG "exclude:$t" "インデックス対象($($hit.Path) が Include)" }
        else { Emit OK "exclude:$t" 'どのインデックス対象にも含まれない' }
    }
}
