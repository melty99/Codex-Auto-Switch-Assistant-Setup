"""Verify native window scoping; no real application windows are changed."""
import base64,os,subprocess,unittest
from pathlib import Path


@unittest.skipUnless(os.name=='nt','Windows adapter')
class WindowAccessTests(unittest.TestCase):
    def test_menu_scan_handles_cloaking_cycles_and_foreign_processes(self):
        script=r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type 'public class QuotaGuardWindows {public static bool IsCloaked(System.IntPtr h){return h.ToInt64()==2;}}'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseInput([IO.File]::ReadAllText($env:MENU_ADAPTER,[Text.Encoding]::UTF8),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors.Message -join '; ')}
$fn=$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-AppMenuItems'},$true)[0]
Invoke-Expression $fn.Extent.Text
function Node($id,$type,$owner=123,$offscreen=$false,$cls='') {
 $node=[pscustomobject]@{Id=$id;Current=@{ControlType=$type;ProcessId=$owner;IsOffscreen=$offscreen;ClassName=$cls;NativeWindowHandle=1};Children=@()}
 $node|Add-Member ScriptMethod GetRuntimeId {return @($this.Id)}
 $node|Add-Member ScriptMethod FindAll {param($scope,$condition) $script:reads++;return $this.Children}
 return $node
}
function Get-ProcessWindowRoots {param($id) return $script:root}
$env:QUOTA_GUARD_DESKTOP_PID='123'
$t=[System.Windows.Automation.ControlType]
$script:root=Node 1 $t::Window
$doc=Node 2 $t::Document
$menu=Node 3 $t::MenuItem
$hidden=Node 4 $t::MenuItem 123 $true
$foreign=Node 5 $t::MenuItem 999
$content=Node 6 $t::Group 123 $false 'MainContentSurface'
$decoy=Node 7 $t::MenuItem
$content.Children=@($decoy)
$script:root.Children=@($doc)
$doc.Children=@($menu,$hidden,$foreign,$content,$script:root,$menu)
$script:reads=0
$items=@(Get-AppMenuItems)
if($items.Count -ne 1 -or $items[0].Id -ne 3 -or $script:reads -gt 6){throw 'Visibility, PID or cycle guard failed'}
$script:root.Current.NativeWindowHandle=2
$script:reads=0
$items=@(Get-AppMenuItems)
if($items.Count -ne 2 -or $items.Id -notcontains 4 -or $items.Id -contains 5 -or $items.Id -contains 7){throw 'Shell-cloaked menu scan failed'}
Write-Output 'PASS'
'''
        env=dict(os.environ,MENU_ADAPTER=str(Path(__file__).resolve().parents[1]/'scripts/quit-desktop.ps1'))
        self.run_powershell(script,env)

    def run_powershell(self,script,env):
        shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],env=env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.strip(),b'PASS')

    def test_native_enumeration_is_scoped_to_process_and_parent(self):
        script=r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Invoke-Expression ([IO.File]::ReadAllText($env:WINDOW_ADAPTER,[Text.Encoding]::UTF8))
Add-Type @'
using System;using System.Runtime.InteropServices;
public class TestWindows {
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr CreateWindowEx(int ex,string cls,string name,uint style,int x,int y,int width,int height,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr param);
 [DllImport("user32.dll")] public static extern bool DestroyWindow(IntPtr hwnd);
 public static IntPtr Make(IntPtr parent) {return CreateWindowEx(0,"Static","",parent==IntPtr.Zero?0x80000000u:0x40000000u,0,0,1,1,parent,IntPtr.Zero,IntPtr.Zero,IntPtr.Zero);}
}
'@
$a=[TestWindows]::Make([IntPtr]::Zero);$b=[TestWindows]::Make([IntPtr]::Zero)
try {
 if($a -eq [IntPtr]::Zero -or $b -eq [IntPtr]::Zero){throw 'Could not create test windows'}
 $childA=[TestWindows]::Make($a);$childB=[TestWindows]::Make($b)
 $mine=@([QuotaGuardWindows]::ForProcess($PID))
 if($mine -notcontains $a -or $mine -notcontains $b -or $mine -contains $childA){throw 'Process window scope invalid'}
 if(@([QuotaGuardWindows]::ForProcess(-1)).Count){throw 'Foreign process leaked'}
 $children=@([QuotaGuardWindows]::Children($a,'Static'))
 if($children.Count -ne 1 -or $children[0] -ne $childA -or $children -contains $childB){throw 'Child escaped host boundary'}
 if(@([QuotaGuardWindows]::Children($a,'Chrome_WidgetWin_1')).Count){throw 'Wrong renderer class matched'}
 $visible=@(Get-ProcessWindowRoots $PID 'Static')
 if(@($visible|Where-Object {$_.Current.NativeWindowHandle -in @($a.ToInt64(),$b.ToInt64())}).Count){throw 'Hidden utility window was accepted'}
 $hidden=@(Get-ProcessWindowRoots $PID 'Static' -IncludeHidden)
 if(@($hidden|Where-Object {$_.Current.NativeWindowHandle -in @($a.ToInt64(),$b.ToInt64())}).Count -ne 2){throw 'Opt-in hidden window lookup failed'}
 $failed=$false
 try {$null=Get-WebViewRoot ([pscustomobject]@{Current=@{NativeWindowHandle=$a.ToInt64()}})}catch{$failed=$true}
 if(-not $failed){throw 'Missing renderer accepted'}
 Write-Output 'PASS'
} finally {
 $null=[TestWindows]::DestroyWindow($a);$null=[TestWindows]::DestroyWindow($b)
}
'''
        env=dict(os.environ,WINDOW_ADAPTER=str(Path(__file__).resolve().parents[1]/'scripts/window_access.ps1'))
        shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],env=env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.strip(),b'PASS')


if __name__=='__main__':unittest.main()
