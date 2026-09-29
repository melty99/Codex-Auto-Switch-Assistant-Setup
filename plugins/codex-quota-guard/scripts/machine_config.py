"""Portable machine discovery. Reads paths/metadata, never authentication tokens."""
import base64,json,os,shutil,subprocess,sys,time
from pathlib import Path
from core import read_json,atomic_json
from runtime import ROOT,data_dir,hidden_flags,read_toml

def windows_inventory():
    script=r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$apps=@(Get-AppxPackage OpenAI.Codex | ForEach-Object { @{path=$_.InstallLocation;family=$_.PackageFamilyName} })
$cc=@(Get-Process cc-switch -ErrorAction SilentlyContinue | ForEach-Object { $_.Path })
if (-not $cc.Count) {
 foreach ($base in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
  Get-ItemProperty $base -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^CC[ -]Switch' } | ForEach-Object {
   if ($_.InstallLocation) { $cc+=Join-Path $_.InstallLocation 'cc-switch.exe' }
  }
 }
}
@{desktop=$apps;cc_switch=@($cc | Select-Object -Unique)} | ConvertTo-Json -Depth 4 -Compress
'''
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],capture_output=True,timeout=20,creationflags=hidden_flags())
    if result.returncode:raise RuntimeError('无法读取本机应用信息，请手动选择路径。')
    return json.loads(result.stdout.decode('utf-8-sig'))

def first_file(paths):return next((str(Path(p)) for p in paths if p and Path(p).is_file()),'')

def plugin_root():
    previous=read_json(data_dir()/'installation.json',{}).get('plugin_root')
    if previous:
        manifest=read_json(Path(previous)/'.codex-plugin/plugin.json',{})
        if manifest.get('name')=='codex-quota-guard':return str(Path(previous).resolve())
    return str(ROOT)

def detect():
    previous=read_json(data_dir()/'installation.json',{})
    inventory=windows_inventory()
    cc_candidates=[os.environ.get('CC_SWITCH_HOME'),previous.get('cc_switch_dir'),str(Path.home()/'.cc-switch')]
    cc_home=next((Path(p) for p in cc_candidates if p and (Path(p)/'cc-switch.db').is_file()),Path.home()/'.cc-switch')
    cc_settings=read_json(cc_home/'settings.json',{})
    homes=[os.environ.get('CODEX_HOME'),previous.get('codex_home'),cc_settings.get('codexConfigDir'),str(Path.home()/'.codex')]
    valid=[str(Path(p)) for p in homes if p and (Path(p)/'config.toml').is_file()]
    home=next(iter(valid),str(Path.home()/'.codex'))
    cli_base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'OpenAI/Codex/bin'
    cli=sorted(cli_base.glob('*/codex.exe'),key=lambda p:p.stat().st_mtime,reverse=True)
    desktop=first_file(Path(a['path'])/'app'/name for a in inventory['desktop'] for name in ('ChatGPT.exe','Codex.exe'))
    cc=first_file([*inventory['cc_switch'],previous.get('cc_switch_exe'),shutil.which('cc-switch')])
    node=first_file([shutil.which('node'),previous.get('node_exe'),*Path.home().glob('.cache/codex-runtimes/*/dependencies/node/bin/node.exe'),*Path.home().glob('.cache/codex-runtimes/*/dependencies/node/node.exe')])
    keys=[k for k in read_toml(Path(home)/'config.toml').get('plugins',{}) if k.split('@')[0]=='codex-quota-guard']
    return dict(plugin_root=plugin_root(),python_exe=sys.executable,codex_exe=first_file([*cli,previous.get('codex_exe'),shutil.which('codex')]),
                codex_home=home,desktop_exe=desktop,cc_switch_exe=cc,cc_switch_dir=str(cc_home),node_exe=node,
                plugin_key=keys[0] if len(keys)==1 else previous.get('plugin_key','codex-quota-guard@personal'))

def validate(config):
    from desktop_lifecycle import desktop_version
    out=dict(config)
    for key in ('python_exe','codex_exe','desktop_exe','cc_switch_exe','node_exe'):
        path=Path(out.get(key,''))
        if not path.is_file():raise ValueError('请补全可执行文件路径：'+key)
        out[key]=str(path.resolve())
    if not desktop_version(out['desktop_exe']):raise ValueError('请选择已安装的官方 Codex Windows 应用。')
    if Path(out['cc_switch_exe']).name.lower()!='cc-switch.exe':raise ValueError('请选择 cc-switch.exe。')
    cc_dir=Path(out['cc_switch_dir']).resolve();home=Path(out['codex_home']).resolve()
    if not (cc_dir/'cc-switch.db').is_file():raise ValueError('CC Switch 数据目录中未找到 cc-switch.db。')
    if not (home/'config.toml').is_file():raise ValueError('Codex 配置目录中未找到 config.toml。')
    target=Path(read_json(cc_dir/'settings.json',{}).get('codexConfigDir') or Path.home()/'.codex').resolve()
    if target!=home:raise ValueError('Codex 与 CC Switch 管理的配置目录不同；请先在 CC Switch 中统一目录。')
    keys=[k for k in read_toml(home/'config.toml').get('plugins',{}) if k.split('@')[0]=='codex-quota-guard']
    if len(keys)!=1:raise ValueError('请先在 Codex 安装本插件，且只保留一个市场来源。')
    out.update(codex_home=str(home),cc_switch_dir=str(cc_dir),plugin_key=keys[0],plugin_root=plugin_root(),configured_at=time.time())
    # Validate tools without starting tasks or installing software.
    for key,args in [('python_exe',['-c','import tkinter,tomllib']),('node_exe',['--version']),('codex_exe',['--version'])]:
        result=subprocess.run([out[key],*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=10,creationflags=hidden_flags())
        if result.returncode:raise ValueError('运行环境检查失败：'+key)
    return out

def install_startup(config):
    script=r'''
$ErrorActionPreference='Stop'
$c=$env:QUOTA_GUARD_SETUP | ConvertFrom-Json
$python=Join-Path (Split-Path -Parent $c.python_exe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { $python=$c.python_exe }
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Quota Guard.lnk'))
$shortcut.TargetPath=$python
$shortcut.Arguments='"'+(Join-Path $c.plugin_root 'scripts/guard.py')+'" start'
$shortcut.WorkingDirectory=$c.plugin_root
$shortcut.WindowStyle=7
$shortcut.IconLocation=(Join-Path $c.plugin_root 'assets/app-icon.ico')+',0'
$shortcut.Description='Codex Auto Switch Assistant'
$shortcut.Save()
'''
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
                          env=dict(os.environ,QUOTA_GUARD_SETUP=json.dumps(config)),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,creationflags=hidden_flags())
    if result.returncode:raise RuntimeError('无法更新登录启动项，请检查配置路径。')

def save(config):
    out=validate(config)
    plan=read_json(data_dir()/'reset-wait.json',{})
    from reset_recovery import ACTIVE_PHASES
    if plan.get('phase') in ACTIVE_PHASES:raise ValueError('换号或恢复计划仍在进行，请完成或取消后再配置。')
    previous=read_json(data_dir()/'installation.json',{})
    if previous:atomic_json(data_dir()/'installation.previous.json',previous)
    atomic_json(data_dir()/'installation.json',dict(previous,**out))
    try:install_startup(out)
    except Exception:
        atomic_json(data_dir()/'installation.json',previous)
        raise
    # A running guard must reconnect its transports after paths change.
    import uuid
    atomic_json(data_dir()/'commands'/(uuid.uuid4().hex+'.json'),{'action':'reload_paths'})
    from runtime import ensure_service
    ensure_service()
    return out
