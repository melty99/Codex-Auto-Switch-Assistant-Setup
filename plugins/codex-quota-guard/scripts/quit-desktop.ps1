# Select only the verified Codex process's application menu. Never use coordinates.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Get-AppMenuItems {
    $seen = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($root in @(Get-ProcessWindowRoots ([int]$env:QUOTA_GUARD_DESKTOP_PID))) {
        $cloaked = [QuotaGuardWindows]::IsCloaked([IntPtr]$root.Current.NativeWindowHandle)
        # Application menus are near the document root. Avoid traversing chat
        # content/sidebar branches, which can be huge or contain cyclic nodes.
        $queue = [System.Collections.Generic.Queue[object]]::new()
        $queue.Enqueue(@{element=$root;depth=0})
        $visited = 0
        while ($queue.Count -and $visited -lt 256) {
            $entry=$queue.Dequeue();$item=$entry.element;$visited++
            if (-not $seen.Add(($item.GetRuntimeId() -join ','))) { continue }
            $type=$item.Current.ControlType
            if ($type -eq [System.Windows.Automation.ControlType]::MenuItem -and
                $item.Current.ProcessId -eq [int]$env:QUOTA_GUARD_DESKTOP_PID -and
                (-not $item.Current.IsOffscreen -or $cloaked)) { $item }
            if ($entry.depth -ge 16 -or $item.Current.ClassName -match 'MainContentSurface|thread-scroll-layout|app-shell-left-panel|ProseMirror' -or
                $type -in @([System.Windows.Automation.ControlType]::Text,[System.Windows.Automation.ControlType]::Edit,[System.Windows.Automation.ControlType]::Button,[System.Windows.Automation.ControlType]::Image)) { continue }
            foreach ($child in @($item.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition))) {
                $queue.Enqueue(@{element=$child;depth=($entry.depth+1)})
            }
        }
    }
}
function Get-MenuLabel($item) {
    # UIA includes keyboard shortcuts in accessible names on the Windows app.
    (($item.Current.Name -replace '&', '') -replace '\s+(?:(?:Ctrl|Control|Alt|Shift|Cmd|Command)\+)+[A-Za-z0-9]+\s*$', '').Trim()
}
$expanded = $null
$invoked = $false
$restored = @()
try {
    $windows = @(Get-ProcessWindowRoots ([int]$env:QUOTA_GUARD_DESKTOP_PID) 'Chrome_WidgetWin_1')
    foreach ($window in $windows) {
        if ($window.Current.ClassName -ne 'Chrome_WidgetWin_1') { continue }
        $pattern = $null
        if ($window.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$pattern) -and
            $pattern.Current.WindowVisualState -eq [System.Windows.Automation.WindowVisualState]::Minimized) {
            $pattern.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Normal)
            $restored += $pattern
        }
    }
    if ($restored.Count) { Start-Sleep -Milliseconds 300 }
    $files = @(Get-AppMenuItems | Where-Object {
        (Get-MenuLabel $_) -match '^(File|文件|檔案)$' -and $_.Current.IsEnabled
    })
    if ($files.Count -eq 0) { throw 'file_menu_unavailable' }
    # Multiple Codex windows can each expose File. Open one menu, then require a
    # unique visible Quit command belonging to the verified process.
    $expanded = $files[0].GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
    $expanded.Expand()
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $exits = @(Get-AppMenuItems | Where-Object {
            (Get-MenuLabel $_) -match '^(Exit|Quit(?: ChatGPT| Codex)?|退出(?: ChatGPT| Codex)?|結束(?: ChatGPT| Codex)?)$' -and
            $_.Current.IsEnabled
        })
        if ($exits.Count -gt 1) { throw 'quit_menu_ambiguous' }
        if ($exits.Count -eq 1) { break }
        Start-Sleep -Milliseconds 150
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($exits.Count -ne 1) { throw 'quit_menu_unavailable' }
    $invoke = $exits[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    if ($env:QUOTA_GUARD_PROBE -ne '1') {
        # Even if Invoke throws after delivery, do not retry an uncertain action.
        $invoked = $true
        $invoke.Invoke()
    }
    Write-Output 'ok'
} catch {
    $code = if ($invoked) { 'quit_invoke_uncertain' } elseif ($_.Exception.Message -in @('file_menu_unavailable','quit_menu_ambiguous','quit_menu_unavailable')) { $_.Exception.Message } else { 'menu_access_failed' }
    Write-Output $code
    exit 1
} finally {
    if ($null -ne $expanded -and -not $invoked) {
        try { $expanded.Collapse() } catch { }
    }
    if (-not $invoked) {
        foreach ($pattern in $restored) {
            try { $pattern.SetWindowVisualState([System.Windows.Automation.WindowVisualState]::Minimized) } catch { }
        }
    }
}
