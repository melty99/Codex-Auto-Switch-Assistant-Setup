"""Launch monitoring from Task Scheduler, outside the Codex desktop process job."""
import base64,json,os,subprocess,sys
from pathlib import Path


def launch_detached():
    from runtime import data_dir,codex_home,ROOT,hidden_flags
    from core import read_json
    config=read_json(data_dir()/'installation.json',{})
    exe=Path(config.get('python_exe') or sys.executable)
    if exe.with_name('pythonw.exe').is_file():exe=exe.with_name('pythonw.exe')
    root=Path(config.get('plugin_root',str(ROOT)))
    script=r'''
$ErrorActionPreference='Stop'
$c=$env:QUOTA_GUARD_SERVICE | ConvertFrom-Json
$service=New-Object -ComObject Schedule.Service
$service.Connect()
$folder=$service.GetFolder('\')
$task=$service.NewTask(0)
$task.RegistrationInfo.Description='Codex Auto Switch Assistant independent background monitor'
$task.Principal.UserId=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$task.Principal.LogonType=3
$task.Principal.RunLevel=0
$task.Settings.MultipleInstances=2
$task.Settings.ExecutionTimeLimit='PT0S'
$task.Settings.DisallowStartIfOnBatteries=$false
$task.Settings.StopIfGoingOnBatteries=$false
$task.Settings.AllowDemandStart=$true
$action=$task.Actions.Create(0)
$action.Path=$c.python
$action.Arguments='"'+$c.script+'" daemon --independent --data-dir "'+$c.data+'" --codex-home "'+$c.home+'"'
$action.WorkingDirectory=$c.root
$name='CodexAutoSwitchAssistant-Service-'+[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$registered=$folder.RegisterTaskDefinition($name,$task,6,$task.Principal.UserId,$null,3)
$null=$registered.Run($null)
Write-Output 'ok'
'''
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    values=dict(python=str(exe),script=str(root/'scripts/guard.py'),root=str(root),data=str(data_dir()),home=str(codex_home()))
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
                          env=dict(os.environ,QUOTA_GUARD_SERVICE=json.dumps(values)),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,creationflags=hidden_flags())
    if result.returncode or result.stdout.strip()!=b'ok':
        raise RuntimeError('无法启动独立后台任务；未使用随 Codex 退出的子进程，请检查 Windows 任务计划程序。')
