$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$guardPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$guardInstallPath = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Codex/QuotaGuard/installation.json'
if (Test-Path -LiteralPath $guardInstallPath) {
    $guardConfiguredPython = (Get-Content -LiteralPath $guardInstallPath -Raw | ConvertFrom-Json).python_exe
    if ($guardConfiguredPython -and (Test-Path -LiteralPath $guardConfiguredPython)) { $guardPython=$guardConfiguredPython }
}
if (-not (Test-Path -LiteralPath $guardPython)) { $guardPython=(Get-Command python.exe -ErrorAction SilentlyContinue).Source }
if (-not $guardPython) { exit 0 }
if (-not (Test-Path -LiteralPath $guardPython)) { exit 0 }
$guardPayload = [Console]::In.ReadToEnd()
$guardPayload | & $guardPython (Join-Path $PSScriptRoot 'guard.py') hook
