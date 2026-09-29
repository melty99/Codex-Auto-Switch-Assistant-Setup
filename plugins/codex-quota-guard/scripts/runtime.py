"""Local transports and configuration. No credentials are exported or logged."""
from __future__ import annotations
import hashlib, json, os, queue, shutil, subprocess, threading, time, tomllib, uuid
from pathlib import Path
from core import DEFAULTS, atomic_json, read_json, validate_settings

PLUGIN_KEY = 'codex-quota-guard@personal'
ROOT = Path(__file__).resolve().parent.parent

def data_dir():
    override=os.environ.get('CODEX_QUOTA_GUARD_DATA')
    if override:return Path(override)
    documents=Path.home()/'Documents'
    if os.name=='nt':
        import ctypes
        buffer=ctypes.create_unicode_buffer(32768)
        if ctypes.windll.shell32.SHGetFolderPathW(None,5,None,0,buffer)==0:documents=Path(buffer.value)
    return documents/'Codex'/'QuotaGuard'

def cc_switch_dir():
    configured=read_json(data_dir()/'installation.json',{}).get('cc_switch_dir')
    return Path(configured or os.environ.get('CC_SWITCH_HOME') or Path.home()/'.cc-switch')

def discover_node():
    configured=read_json(data_dir()/'installation.json',{}).get('node_exe')
    if configured and Path(configured).is_file():return configured
    found=shutil.which('node')
    if found:return found
    base=Path.home()/'.cache/codex-runtimes'
    choices=list(base.glob('*/dependencies/node/bin/node.exe'))+list(base.glob('*/dependencies/node/node.exe'))
    if choices:return str(max(choices,key=lambda p:p.stat().st_mtime))
    raise RuntimeError('未找到 Node.js，请运行一键配置。')

def codex_home():
    installed=read_json(data_dir()/'installation.json',{}).get('codex_home')
    return Path(installed or os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))

def hidden_flags(): return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def discover_codex():
    configured = read_json(data_dir()/'installation.json', {}).get('codex_exe')
    base=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'OpenAI/Codex/bin'
    pinned=Path(configured) if configured else None
    # Explicit external runtimes stay pinned. Managed desktop copies follow
    # installation changes instead of keeping an older surviving binary forever.
    if pinned and pinned.is_file() and (not load_settings()['auto_adapt'] or not pinned.resolve().is_relative_to(base.resolve())):
        return str(pinned)
    candidates = [p for p in base.glob('*/codex.exe') if p.is_file()]
    if candidates: return str(max(candidates, key=lambda x:x.stat().st_mtime))
    value = shutil.which('codex')
    if not value: raise RuntimeError('找不到 Codex 可执行文件')
    return value

def load_settings(): return validate_settings(read_json(data_dir()/'settings.json', DEFAULTS))

def read_toml(path):
    try: return tomllib.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError: return {}

def plugin_enabled(cwd=None):
    try:
        config = read_toml(codex_home()/'config.toml')
        plugin_key=read_json(data_dir()/'installation.json',{}).get('plugin_key',PLUGIN_KEY)
        if config.get('plugins', {}).get(plugin_key, {}).get('enabled') is not True: return False
        if any(x.get('enabled') is False and 'codex-quota-guard' in str(x.get('path', ''))
               for x in config.get('skills', {}).get('config', [])): return False
        if cwd:
            p = Path(cwd)
            # A local disable is always honored, even for an untrusted project.
            for folder in [p, *p.parents]:
                project = read_toml(folder/'.codex/config.toml')
                if project.get('plugins', {}).get(plugin_key, {}).get('enabled') is False: return False
        return True
    except (ValueError, OSError, TypeError): return False

def discover_threads():
    """Metadata only, read-only. Never edit the Codex database or replay history."""
    import sqlite3
    db = codex_home()/'state_5.sqlite'
    if not db.exists(): return []
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True, timeout=2) as conn:
        return [r[0] for r in conn.execute(
            "SELECT id FROM threads WHERE archived=0 AND (agent_path IS NULL OR agent_path='/root') "
            "AND (thread_source IS NULL OR thread_source NOT LIKE '%subagent%') "
            "AND (originator IS NULL OR originator NOT LIKE '%quota_guard%') ORDER BY updated_at_ms DESC")]

class JsonProcess:
    def __init__(self, args, on_event=None, env=None):
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding='utf-8', bufsize=1, creationflags=hidden_flags(), env=env)
        self.on_event = on_event or (lambda m:None)
        self.pending = {}; self.lock = threading.RLock(); self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try: msg=json.loads(line)
                except ValueError: continue
                key=msg.get('id')
                with self.lock: target=self.pending.get(key)
                if target is not None and ('result' in msg or 'error' in msg): target.put(msg)
                elif 'id' in msg and 'method' in msg:
                    # Never approve requests from a standalone quota reader.
                    self.write({'id':msg['id'],'error':{'code':-32601,'message':'Read-only quota client'}})
                else: self.on_event(msg)
        finally:
            self.closed=True
            with self.lock:
                for target in self.pending.values(): target.put({'error':'process disconnected'})
            self.on_event({'event':'process_exit'})

    def write(self, message):
        with self.lock:
            if self.closed: raise RuntimeError('process disconnected')
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False)+'\n'); self.proc.stdin.flush()

    def call(self, message, timeout=30):
        ident=uuid.uuid4().hex; target=queue.Queue()
        with self.lock: self.pending[ident]=target
        try:
            self.write(dict(message,id=ident)); response=target.get(timeout=timeout)
            if 'error' in response: raise RuntimeError(str(response['error']))
            return response['result']
        except queue.Empty: raise TimeoutError('操作超时，结果未确认') from None
        finally:
            with self.lock: self.pending.pop(ident,None)

    def close(self):
        try: self.proc.stdin.close()
        except (OSError, ValueError): pass
        try: self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired: self.proc.terminate()

class Desktop:
    def __init__(self):
        self.threads={}; self.connected=False; self.compatible=True; self.lock=threading.RLock()
        self.proc=JsonProcess([discover_node(),str(ROOT/'scripts/desktop_bridge.cjs')],self.event)

    def event(self, msg):
        with self.lock:
            event=msg.get('event')
            if event=='connected': self.connected=True
            elif event in ('disconnected','error','process_exit'): self.connected=False; self.threads.clear()
            elif event=='incompatible': self.compatible=False; self.threads.clear()
            elif event=='unavailable': self.threads.pop(msg.get('id'),None)
            elif event=='thread': self.threads[msg['thread']['id']]=msg['thread']

    def follow(self, ids):
        for i in range(0,len(ids),100): self.proc.call({'op':'follow','threads':ids[i:i+100]},timeout=10)

    def snapshots(self):
        with self.lock: return {k:dict(v) for k,v in self.threads.items()}

    def snapshot(self, tid): return self.proc.call({'op':'snapshot','thread':tid},timeout=5)
    def action(self, op, t, **kwargs):
        return self.proc.call({'op':op,'thread':t['id'],'expected_turn':t['turn_id'],**kwargs},timeout=40)
    def close(self): self.proc.close()

class QuotaReader:
    def __init__(self):
        self.process=None; self.auth_stamp=None; self.exe_stamp=None

    def _ensure(self):
        auth=codex_home()/'auth.json'
        stamp=(auth.stat().st_mtime_ns,auth.stat().st_size) if auth.exists() else None
        exe=discover_codex();stat=Path(exe).stat();exe_stamp=(exe,stat.st_mtime_ns,stat.st_size)
        if self.process and (self.process.closed or stamp!=self.auth_stamp or exe_stamp!=self.exe_stamp): self.close()
        if self.process is None:
            self.process=JsonProcess([exe,'app-server','--listen','stdio://'],env=dict(os.environ,CODEX_HOME=str(codex_home())))
            try:
                self.process.call({'method':'initialize','params':{'clientInfo':{'name':'codex_quota_guard','version':'0.1.0','title':'Codex Auto Switch Assistant'},'capabilities':{'experimentalApi':True}}},timeout=40)
                self.process.write({'method':'initialized'})
                self.auth_stamp=stamp
                self.exe_stamp=exe_stamp
            except Exception:
                self.close(); raise

    def call(self, method, params=None):
        self._ensure()
        return self.process.call({'method':method,**({'params':params} if params is not None else {})},timeout=40)

    def read(self):
        self._ensure()
        account=self.call('account/read',{'refreshToken':False}).get('account')
        if not account or account.get('type') not in ('chatgpt','chatgptAuthTokens'):
            raise RuntimeError('当前登录不是 ChatGPT 订阅账号')
        # A non-secret fingerprint is sufficient to identify a change in account.
        identity=json.dumps(account,sort_keys=True)
        fingerprint=hashlib.sha256(identity.encode()).hexdigest()[:12]
        result=self.call('account/rateLimits/read')
        after=self.call('account/read',{'refreshToken':False}).get('account')
        if after!=account: raise RuntimeError('查询期间登录身份变化，等待下次核验')
        return fingerprint,result

    def close(self):
        if self.process: self.process.close(); self.process=None

def ensure_service():
    directory=data_dir(); directory.mkdir(parents=True,exist_ok=True)
    status=read_json(directory/'status.json',{})
    if time.time()-status.get('heartbeat',0)<12: return
    installation=read_json(directory/'installation.json',{})
    script=Path(installation.get('plugin_root',str(ROOT)))/'scripts/guard.py'
    import sys
    exe=Path(installation.get('python_exe') or sys.executable); pythonw=exe.with_name('pythonw.exe')
    if pythonw.exists(): exe=pythonw
    env=dict(os.environ,CODEX_HOME=str(codex_home()))
    # Service owns its own lifetime, not the model turn or a hook subprocess.
    flags=hidden_flags()
    if os.name=='nt': flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(exe),str(script),'daemon'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,creationflags=flags,env=env,close_fds=True)
