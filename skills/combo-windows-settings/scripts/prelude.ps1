# prelude.ps1 — winsettings.sh が各 groups/<name>/check.ps1 の前に連結する共通部(読み取り専用)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$OutputEncoding = [Text.Encoding]::UTF8
# 結果 1 行 = "<STATUS>\t<item>\t<detail>"。STATUS は OK / NG / MANUAL / INFO
function Emit([string]$Status, [string]$Item, [string]$Detail = '') {
    [Console]::Out.WriteLine(("{0}`t{1}`t{2}" -f $Status, $Item, $Detail))
}
# 環境変数(winsettings.sh が WINSET_* を注入)を既定値つきで読む
function Opt([string]$Name, [string]$Default = '') {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrEmpty($v)) { return $Default } else { return $v }
}
# レジストリ値(無ければ $null)
function RegVal([string]$Path, [string]$Name) {
    try { return (Get-ItemProperty -Path $Path -Name $Name -ErrorAction Stop).$Name } catch { return $null }
}
