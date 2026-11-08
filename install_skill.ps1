<#
  Install the arch-env-design skill into a DSH skills directory.
  DSH hot-reloads skills, so no restart is needed.

  Usage:
    .\install_skill.ps1                        # user level (all sessions)
    .\install_skill.ps1 -Scope Workspace       # workspace level (.dsh\skills in cwd)
    .\install_skill.ps1 -Scope Workspace -WorkspaceRoot "D:\some\workspace"

  If PowerShell blocks unsigned scripts, use install_skill.cmd instead.
#>
param(
    [ValidateSet('User', 'Workspace')][string]$Scope = 'User',
    [string]$WorkspaceRoot = (Get-Location).Path,
    [string]$Source = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'

$bundleItems = @('SKILL.md', 'references', 'scripts', 'assets', 'examples')
foreach ($item in $bundleItems) {
    if (-not (Test-Path (Join-Path $Source $item))) {
        throw "Missing '$item' in source. Run this script from the repository root."
    }
}

if ($Scope -eq 'User') {
    $skillsRoot = Join-Path $env:USERPROFILE '.dsh\skills'
} else {
    $skillsRoot = Join-Path $WorkspaceRoot '.dsh\skills'
}
$dest = Join-Path $skillsRoot 'arch-env-design'

New-Item -ItemType Directory -Path $dest -Force | Out-Null
foreach ($item in $bundleItems) {
    Copy-Item -LiteralPath (Join-Path $Source $item) -Destination $dest -Recurse -Force
}
Get-ChildItem -Recurse -Directory -Filter '__pycache__' $dest | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -File $dest -Include '*.pyc' | Remove-Item -Force

$files = Get-ChildItem -Recurse -File $dest
Write-Host "installed -> $dest"
Write-Host ("files={0} bytes={1}" -f $files.Count, ($files | Measure-Object Length -Sum).Sum)
Write-Host "entry SKILL.md present: $(Test-Path (Join-Path $dest 'SKILL.md'))"
