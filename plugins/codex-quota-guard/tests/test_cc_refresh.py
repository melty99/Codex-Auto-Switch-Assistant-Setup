"""Execute the real PowerShell refresh function with fake UIA snapshots."""
import os,subprocess,unittest
from pathlib import Path


@unittest.skipUnless(os.name=='nt','Windows adapter')
class RefreshAdapterTests(unittest.TestCase):
    def test_msaa_refresh_scope_disabled_and_ambiguous_cards(self):
        script=r'''
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition ([IO.File]::ReadAllText($env:QUIET_SOURCE,[Text.Encoding]::UTF8)) -ReferencedAssemblies Accessibility
$form=[Windows.Forms.Form]::new()
$card=[Windows.Forms.Panel]::new();$form.Controls.Add($card)
$title=[Windows.Forms.Label]::new();$title.Text='Test account';$card.Controls.Add($title)
$refresh=[Windows.Forms.Button]::new();$refresh.Text='刷新';$card.Controls.Add($refresh)
$active=[Windows.Forms.Button]::new();$active.Text='使用中';$card.Controls.Add($active)
$script:clicks=0;$refresh.Add_Click({$script:clicks++})
try {
 $null=$form.Handle;$null=$card.Handle;$null=$title.Handle;$null=$refresh.Handle;$null=$active.Handle
 [QuotaGuardQuietRefresh]::Run($form.Handle,'Test account')
 if($script:clicks -ne 1){throw 'Refresh did not execute exactly once'}
 $refresh.Enabled=$false
 $failed=$false;try{[QuotaGuardQuietRefresh]::Run($form.Handle,'Test account')}catch{$failed=$true}
 if(-not $failed -or $script:clicks -ne 1){throw 'Disabled refresh accepted'}
 $refresh.Enabled=$true
 $duplicate=[Windows.Forms.Label]::new();$duplicate.Text='Test account';$card.Controls.Add($duplicate);$null=$duplicate.Handle
 $failed=$false;try{[QuotaGuardQuietRefresh]::Run($form.Handle,'Test account')}catch{$failed=$true}
 if(-not $failed -or $script:clicks -ne 1){throw 'Ambiguous title accepted'}
 $duplicate.Dispose()
 $active.Text='Unrelated'
 $failed=$false;try{[QuotaGuardQuietRefresh]::Run($form.Handle,'Test account')}catch{$failed=$true}
 if(-not $failed -or $script:clicks -ne 1){throw 'Unverified card accepted'}
 Write-Output 'PASS'
}finally{$form.Dispose()}
'''
        import base64
        env=dict(os.environ,QUIET_SOURCE=str(Path(__file__).resolve().parents[1]/'scripts/quiet_refresh.cs'))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],env=env,capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.strip(),b'PASS')

    def test_error_card_recovery_and_strict_post_refresh_validation(self):
        script=r'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$source=[IO.File]::ReadAllText($env:TEST_ADAPTER,[Text.Encoding]::UTF8)
$ast=[System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw ($errors.Message -join '; ') }
$function=$ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'RefreshQuota'},$true)[0]
Invoke-Expression $function.Extent.Text
function Card($name) { return $name }
function Button($card,$name) { if($name -ne '刷新'){throw 'Unexpected button'};return $name }
function InvokeQuietRefresh($name) { if($name -ne 'fake'){throw 'Wrong account'};$script:clicks++ }
function InvokeButton($button) { throw 'Foreground invocation is forbidden for refresh' }
function Start-Sleep { param($Milliseconds) }
function Snapshot($name,[switch]$AllowUnavailableQuota) {
 $script:reads++
 if($script:reads -eq 1) {
  if(-not $AllowUnavailableQuota){throw 'Error card requires permissive precheck'}
  return @{refreshing=$false;quotaReady=$false;queryError=$true;texts=@('查询错误')}
 }
 if($script:fail) { return @{refreshing=$false;quotaReady=$false;queryError=$true;texts=@('查询错误')} }
 if(-not $AllowUnavailableQuota){$script:strict++}
 return @{refreshing=$false;quotaReady=$true;queryError=$false;texts=@('刚刚','5小时','7天')}
}
$script:reads=0;$script:clicks=0;$script:strict=0;$script:fail=$false
$result=RefreshQuota 'fake'
if(-not $result.quotaReady -or $script:clicks -ne 1 -or $script:strict -ne 1){throw 'Recovery failed'}
$script:reads=0;$script:clicks=0;$script:strict=0;$script:fail=$true
$failed=$false
try { $null=RefreshQuota 'fake' } catch { $failed=$true }
if(-not $failed -or $script:clicks -ne 1 -or $script:strict -ne 0){throw 'Failed quota must not succeed'}
Write-Output 'PASS'
'''
        import base64
        env=dict(os.environ,TEST_ADAPTER=str(Path(__file__).resolve().parents[1]/'scripts/cc_switch_ui.ps1'))
        shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],env=env,capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout.strip(),b'PASS')


if __name__=='__main__':unittest.main()
