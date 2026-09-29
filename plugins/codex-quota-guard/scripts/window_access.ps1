# Enumerate the verified process's HWNDs, including DWM-cloaked virtual desktops.
# Do not enumerate UIA RootElement: that tree can omit other virtual desktops.
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class QuotaGuardWindows {
    private delegate bool WindowCallback(IntPtr hwnd, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumWindows(WindowCallback callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern bool EnumChildWindows(IntPtr parent, WindowCallback callback, IntPtr parameter);
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out int value, int size);
    public static IntPtr[] ForProcess(int processId) {
        var windows = new List<IntPtr>();
        EnumWindows((hwnd, parameter) => {
            uint owner; GetWindowThreadProcessId(hwnd, out owner);
            if (owner == processId) windows.Add(hwnd);
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }
    public static string ClassName(IntPtr hwnd) {
        var text = new StringBuilder(256); GetClassName(hwnd, text, text.Capacity);
        return text.ToString();
    }
    public static IntPtr[] Children(IntPtr parent, string className) {
        var windows = new List<IntPtr>();
        EnumChildWindows(parent, (hwnd, parameter) => {
            if (ClassName(hwnd) == className) windows.Add(hwnd);
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }
    public static bool IsCloaked(IntPtr hwnd) {
        int value;
        return DwmGetWindowAttribute(hwnd, 14, out value, sizeof(int)) == 0 && (value & 2) != 0;
    }
}
'@
function Get-WebViewRoot($HostRoot) {
    # WebView2 disconnects its UIA parent when the Tauri host is shell-cloaked.
    # The renderer remains accessible through an HWND *inside that host*.
    # Never search all Edge windows, use screen coordinates, or move desktops.
    $handles = @([QuotaGuardWindows]::Children([IntPtr]$HostRoot.Current.NativeWindowHandle, 'Chrome_WidgetWin_1'))
    if ($handles.Count -ne 1) { throw '无法唯一识别 CC Switch 内嵌页面；请重新打开 CC Switch。' }
    return [System.Windows.Automation.AutomationElement]::FromHandle($handles[0])
}
function Get-ProcessWindowRoots([int]$TargetProcessId, [string]$ClassName, [switch]$IncludeHidden) {
    foreach ($hwnd in [QuotaGuardWindows]::ForProcess($TargetProcessId)) {
        if ($ClassName -and [QuotaGuardWindows]::ClassName($hwnd) -ne $ClassName) { continue }
        # A shell-cloaked app still exists. Native visibility excludes closed
        # popup/utility windows without requiring the current virtual desktop.
        if (-not $IncludeHidden -and -not [QuotaGuardWindows]::IsWindowVisible($hwnd) -and -not [QuotaGuardWindows]::IsCloaked($hwnd)) { continue }
        $element = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
        if ($null -ne $element -and $element.Current.ProcessId -eq $TargetProcessId) {
            $element
        }
    }
}
