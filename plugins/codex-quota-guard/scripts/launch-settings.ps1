# Compatibility entry point; use open-settings.cmd when scripts are restricted.
& $env:ComSpec /d /c ('"' + (Join-Path $PSScriptRoot 'open-settings.cmd') + '"')
