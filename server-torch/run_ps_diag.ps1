<#
    Evaluate an ExtendScript (.jsx) file inside the running Photoshop instance through the
    COM automation interface. Handy for driving an installed UXP plugin's JSX launcher from a
    script, without going through the panel UI.

    Usage:
        pwsh -File run_ps_diag.ps1
        pwsh -File run_ps_diag.ps1 -Path 'C:\path\to\your.jsx'

    The default path points at a .jsx that has been copied into the plugin folder under
    %APPDATA%\Adobe\UXP\PluginsStorage\PHSP\<PS version>\Internal\<plugin id>\.
#>
param(
    [string]$Path = "$env:APPDATA\Adobe\UXP\PluginsStorage\PHSP\26\Internal\com.zk21.depthpro\gui_e2e_extract14.jsx"
)

$ErrorActionPreference = 'Continue'
if (-not (Test-Path -LiteralPath $Path)) { throw "ExtendScript file not found: $Path" }

$js = '$.evalFile("' + ($Path -replace '\\', '/') + '")'
$ps = New-Object -ComObject Photoshop.Application
$result = $ps.DoJavaScript($js)
Write-Output ("DoJavaScript result: " + $result)
