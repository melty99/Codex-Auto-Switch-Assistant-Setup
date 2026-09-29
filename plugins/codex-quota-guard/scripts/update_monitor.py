"""Read-only update tracking. Release data is never executable adapter code."""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from core import atomic_json,read_json
from runtime import data_dir,discover_codex,hidden_flags,load_settings,plugin_enabled

SOURCES={
    'codex':'https://developers.openai.com/codex/changelog/rss.xml',
    'cc_switch':'https://api.github.com/repos/farion1231/cc-switch/releases/latest',
}

def release_info(key,payload):
    if key=='cc_switch':
        item=json.loads(payload)
        version=item.get('tag_name','');url=item.get('html_url','')
        if not re.fullmatch(r'v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?',version): raise ValueError('Invalid release version')
        if not url.startswith('https://github.com/farion1231/cc-switch/releases/tag/'): raise ValueError('Invalid release URL')
        return dict(title='CC Switch '+version,version=version,url=url,published=item.get('published_at'))
    root=ET.fromstring(payload)
    items=root.findall('./channel/item')
    if not items: raise ValueError('Missing changelog entries')
    item=items[0];title=item.findtext('title');url=item.findtext('link') or ''
    if not title or urllib.parse.urlparse(url).hostname not in ('developers.openai.com','learn.chatgpt.com'):
        raise ValueError('Invalid official changelog')
    return dict(title=title[:250],url=url,published=item.findtext('pubDate'))

def fetch_release(key):
    request=urllib.request.Request(SOURCES[key],headers={'User-Agent':'CodexQuotaGuard/0.1 (release-monitor)','Accept':'application/json, application/xml, text/xml'})
    with urllib.request.urlopen(request,timeout=12) as response:
        if urllib.parse.urlparse(response.geturl()).hostname not in ('developers.openai.com','learn.chatgpt.com','api.github.com'):
            raise ValueError('Unexpected release source')
        payload=response.read(2_000_001)
        if len(payload)>2_000_000: raise ValueError('Release response too large')
    return release_info(key,payload)

def local_versions():
    script=r'''
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$cc=@(Get-Process cc-switch -ErrorAction SilentlyContinue | ForEach-Object {
  @{version=(Get-Item -LiteralPath $_.Path).VersionInfo.ProductVersion;path=$_.Path;pid=$_.Id}
})
$packages=@(Get-AppxPackage -Name OpenAI.Codex | ForEach-Object {
  @{version=$_.Version.ToString();path=$_.InstallLocation}
})
@{cc_switch=$cc;codex_installed=$packages} | ConvertTo-Json -Depth 4 -Compress
'''
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,creationflags=hidden_flags())
    if result.returncode: raise RuntimeError('无法读取本机版本信息')
    observed=json.loads(result.stdout.decode('utf-8-sig'))
    from desktop_lifecycle import desktop_instance,desktop_version
    desktop=desktop_instance()
    observed['codex_running']=dict(version=desktop_version(desktop['exe']),path=desktop['exe']) if desktop else None
    executable=discover_codex()
    result=subprocess.run([executable,'--version'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=8,creationflags=hidden_flags())
    if result.returncode: raise RuntimeError('Codex CLI 版本查询失败')
    observed['cli']=dict(path=executable,version=result.stdout.decode('utf-8').strip()[:150])
    return observed

def version_key(observed):
    # PID changes alone are restarts, not software upgrades.
    return json.dumps(
        {k:([{x:y for x,y in v.items() if x!='pid'} for v in value] if isinstance(value,list) else value)
         for k,value in observed.items()},sort_keys=True)

class UpdateMonitor:
    def __init__(self,guard):
        self.guard=guard;self.path=data_dir()/'compatibility.json';self.worker=None;self.next_local=0
    def permitted(self):
        settings=load_settings()
        return not self.guard.stop.is_set() and plugin_enabled() and settings['enabled'] and settings['update_tracking']
    def maybe_check(self):
        if self.permitted() and time.time()>=self.next_local and (self.worker is None or not self.worker.is_alive()):
            self.next_local=time.time()+60
            self.worker=threading.Thread(target=self.check,daemon=True);self.worker.start()
    def request_check(self):
        self.next_local=0
        # Only the worker writes the state file. A marker cannot overwrite a
        # concurrent release result, and survives a guard restart.
        atomic_json(data_dir()/'updates-request.json',{'at':time.time()})
    def check(self):
        previous=read_json(self.path,{})
        state=dict(previous,checked_at=time.time(),error=None)
        try:
            observed=local_versions()
            state['installed']=observed
            if version_key(observed)!=version_key(previous.get('installed',{})):
                history=previous.get('changes',[])
                state['changes']=(history+[dict(at=time.time(),versions=observed)])[-20:]
                state['changed_at']=time.time()
            desktop=self.guard.desktop
            state['desktop_protocol']=('incompatible' if desktop and not desktop.compatible else
                'compatible' if desktop and desktop.connected and desktop.snapshots() else 'waiting')
            state['auto_adapt']=load_settings()['auto_adapt']
            if not self.permitted(): return
            try:
                from manual_switch import provider_inventory,ui_request
                active,providers=provider_inventory()
                result=ui_request('probe',active,names=[p['name'] for p in providers])
                state['cc_ui']=dict(state='compatible',version=result.get('version'),adapter=result.get('adapter'))
            except Exception:
                state['cc_ui']=dict(state='waiting',message='界面结构暂未确认；检查 CC Switch 的 Codex 页面、账号标签和窗口状态。')
        except Exception:
            state['error']='本机版本检查失败，将在下一轮重试；不会使用旧结果放行操作。'
            state['cc_ui']={'state':'waiting'};state['desktop_protocol']='waiting'
        if not self.permitted(): return
        request=read_json(data_dir()/'updates-request.json',{}).get('at',0)
        releases=dict(previous.get('releases',{}))
        for key in SOURCES:
            old=releases.get(key,{})
            if time.time()<old.get('next_check',0) and request<=old.get('attempted_at',0): continue
            if not self.permitted(): return
            item=dict(old,attempted_at=time.time())
            try:
                item.update(fetch_release(key),checked_at=time.time(),error=None,next_check=time.time()+21600)
            except Exception:
                item.update(error='发布信息查询失败，保留上次结果；15 分钟后重试。',next_check=time.time()+900)
            releases[key]=item
        state['releases']=releases
        if self.permitted(): atomic_json(self.path,state)
