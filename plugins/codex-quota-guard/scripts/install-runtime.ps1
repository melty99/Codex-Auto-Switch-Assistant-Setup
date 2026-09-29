param([string]$PluginRoot = (Split-Path $PSScriptRoot -Parent), [switch]$NoStartup)
$ErrorActionPreference = 'Stop'
$guardRoot = [IO.Path]::GetFullPath($PluginRoot)
if (-not (Test-Path -LiteralPath (Join-Path $guardRoot '.codex-plugin/plugin.json'))) { throw '插件目录无效' }
$guardData = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Codex/QuotaGuard'
$guardExisting = @{}
$guardInstallPath = Join-Path $guardData 'installation.json'
if (Test-Path -LiteralPath $guardInstallPath) {
    (Get-Content -LiteralPath $guardInstallPath -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $guardExisting[$_.Name]=$_.Value }
}
$guardPython = $guardExisting.python_exe
if (-not $guardPython -or -not (Test-Path -LiteralPath $guardPython)) { $guardPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' }
if (-not (Test-Path -LiteralPath $guardPython)) { $guardPython=(Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $guardPython) { throw 'Python 3.11+ with tkinter is required. Open settings and run Quick setup.' }
$guardCodex = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'OpenAI/Codex/bin') -Filter codex.exe -Recurse -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $guardCodex) { $guardCodex=$guardExisting.codex_exe }
if (-not $guardCodex) { $guardCodex=(Get-Command codex.exe -ErrorAction SilentlyContinue).Source }
if (-not $guardCodex) { throw 'Codex CLI not found. Open settings and run Quick setup.' }
$guardCodexHome = if ($guardExisting.codex_home) { $guardExisting.codex_home } elseif ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
New-Item -ItemType Directory -Path $guardData -Force | Out-Null
$guardInstall = $guardExisting
$guardInstall.plugin_root=$guardRoot;$guardInstall.python_exe=$guardPython;$guardInstall.codex_exe=$guardCodex
$guardInstall.codex_home=$guardCodexHome;$guardInstall.installed_at=[DateTime]::UtcNow.ToString('o')
$guardInstall | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $guardData 'installation.json') -Encoding UTF8
if (-not $NoStartup) {
    $guardShell = New-Object -ComObject WScript.Shell
    $guardShortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Quota Guard.lnk'
    $guardShortcut = $guardShell.CreateShortcut($guardShortcutPath)
    $guardShortcut.TargetPath = Join-Path (Split-Path $guardPython -Parent) 'pythonw.exe'
    $guardShortcut.Arguments = '"' + (Join-Path $guardRoot 'scripts/guard.py') + '" start'
    $guardShortcut.WorkingDirectory = $guardRoot
    $guardShortcut.WindowStyle = 7
    $guardShortcut.IconLocation = (Join-Path $guardRoot 'assets/app-icon.ico') + ',0'
    $guardShortcut.Description = 'Codex Auto Switch Assistant; disabling the plugin stops automatic actions'
    $guardShortcut.Save()
}
& $guardPython (Join-Path $guardRoot 'scripts/guard.py') start
Write-Output '额度守护运行环境已安装；插件栏开关控制全局自动管理。'
