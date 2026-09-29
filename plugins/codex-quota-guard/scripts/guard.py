from __future__ import annotations
import argparse, json, os, sys, threading, time, uuid
from pathlib import Path
from core import *
from runtime import *

RESUME_TEXT = ('[Codex Auto Switch Assistant：恢复 {operation}] 上次任务因订阅额度不足由守护程序中断，现已满足恢复阈值。'
               '请检查最后一次工具调用及已有文件，继续此前尚未完成的任务。不要重复已成功完成的操作；'
               '若某个外部操作结果不明确，先检查状态再决定是否重试。保留原任务范围和权限，原有审批仍需按规则处理。')
CHECKPOINT_TEXT = ('[Codex Auto Switch Assistant] 当前订阅额度接近暂停阈值。请在方便的安全边界简短记录已完成工作、'
                   '未完成事项、关键文件和下一步，必要时保存到当前工作目录已有的进度文件，然后继续。'
                   '不要为此扩大任务范围或执行额外外部操作；守护程序会在达到阈值后请求中断。')

class Guard:
    def __init__(self):
        self.directory=data_dir(); self.directory.mkdir(parents=True,exist_ok=True)
        self.settings=load_settings(); self.journal=Journal(self.directory/'tasks.json'); self.journal.recover_uncertain()
        self.desktop=None; self.reader=QuotaReader(); self.quota=None; self.account=None
        self.error=None; self.next_poll=0; self.next_discovery=0; self.last_resume=0
        self.checkpoints=set(); self.checked_high=0; self.stop=threading.Event(); self.lock=threading.RLock()
        self.worker=None; self.last_discovered=set(); self.quota_inflight=False
        from reset_recovery import ResetRecovery
        self.recovery=ResetRecovery(self)
        from update_monitor import UpdateMonitor
        self.updates=UpdateMonitor(self)
        from task_queue import TaskQueue
        self.task_queue=TaskQueue(self)

    def report(self):
        snapshots=self.desktop.snapshots() if self.desktop else {}
        enabled=plugin_enabled() and self.settings['enabled']
        state='disabled' if not enabled else 'connecting' if not self.desktop or not self.desktop.connected else 'incompatible' if not self.desktop.compatible else 'monitoring'
        atomic_json(self.directory/'status.json',{
            'heartbeat':time.time(),'pid':os.getpid(),'state':state,'account':self.account,
            'quota':self.quota,'decision':quota_decision(self.quota,self.settings),
            'error':self.error,'plugin_enabled':plugin_enabled(),'settings':self.settings,
            'threads':list(snapshots.values()),'tasks':list(self.journal.records().values()),
            'source':str(ROOT),'version':'0.1.0',
            'poll_interval':self.poll_delay(),'next_poll':self.next_poll,
        })

    def log(self, action, tid=None, detail=None):
        path=self.directory/'events.jsonl'
        if path.exists() and path.stat().st_size>2_000_000:
            os.replace(path,self.directory/'events.previous.jsonl')
        with path.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':time.time(),'action':action,'thread':tid,'detail':detail},ensure_ascii=False)+'\n')

    def poll_delay(self):
        settings=load_settings();interval=poll_interval(self.quota,settings)
        if settings['dynamic_poll'] and any(r.get('phase')=='paused' for r in self.journal.records().values()):
            interval=min(interval,settings['poll_low_seconds'])
        return interval

    def fetch_quota(self):
        self.quota_inflight=True
        try:
            previous_runtime=self.reader.exe_stamp
            account,result=self.reader.read(); quota=normalize_quota(result,self.settings['limit_id'])
            with self.lock:
                if account!=self.account or previous_runtime!=self.reader.exe_stamp: self.checked_high=0
                self.account=account; self.quota=quota; self.error=None
                self.checked_high=self.checked_high+1 if quota_decision(quota,self.settings)=='resume' else 0
                self.next_poll=time.time()+self.poll_delay()
        except Exception as e:
            with self.lock:
                self.quota=None; self.checked_high=0; self.error='额度查询失败：'+str(e)[:240]
                self.next_poll=time.time()+poll_interval(None,load_settings())
        finally: self.quota_inflight=False

    def current(self, tid):
        if not self.desktop or not self.desktop.connected or not self.desktop.compatible: return None
        try: return self.desktop.snapshot(tid)
        except Exception: return None

    def permitted(self, thread):
        fresh=load_settings()
        return (fresh['enabled'] and plugin_enabled(thread.get('cwd'))
                and thread['id'] not in self.settings['excluded_threads']
                and thread.get('model_provider') in ('openai',None))

    def pause_one(self, thread):
        t=self.current(thread['id'])
        if not t or not is_active(t) or not self.permitted(t): return
        existing=self.journal.data['tasks'].get(t['id'],{})
        if existing.get('turn_id')==t['turn_id'] and existing.get('phase') in ('pause_pending','paused','needs_review'): return
        self.journal.intent(t,'pause',self.account)
        try:
            result=self.desktop.action('interrupt',t)
            if result.get('interruptedTurnId')!=t['turn_id']: raise RuntimeError('中断轮次未确认')
            deadline=time.time()+15
            while time.time()<deadline:
                observed=self.current(t['id'])
                if observed and observed.get('turn_id')==t['turn_id'] and observed.get('status')=='interrupted' and observed.get('runtime_status')!='active':
                    self.journal.mark(t['id'],'paused'); self.log('paused',t['id']); return
                time.sleep(.25)
            raise RuntimeError('已发送中断，但未确认停止状态')
        except Exception as e:
            self.journal.mark(t['id'],'needs_review',note=str(e)[:240]); self.log('pause_unconfirmed',t['id'])

    def resume_one(self, record):
        t=self.current(record['id'])
        if not t or not self.permitted(t) or not resume_eligible(record,t) or not load_settings()['auto_resume']: return
        if quota_decision(self.quota,self.settings)!='resume' or self.checked_high<2: return
        if time.time()-record['paused_at']<self.settings['cooldown_seconds']: return
        operation=self.journal.intent(t,'resume',self.account)
        try:
            if self.journal.records().get(t['id'],{}).get('phase')!='resume_pending': return
            if not self.permitted(t) or not load_settings()['auto_resume']:
                self.journal.mark(t['id'],'paused'); return
            self.desktop.action('resume',t,operation_id=operation['operation_id'],text=RESUME_TEXT.format(operation=operation['operation_id']))
            self.journal.mark(t['id'],'resumed',resumed_at=time.time(),resumed_account=self.account)
            self.last_resume=time.time(); self.next_poll=0; self.checked_high=0
            self.log('resumed',t['id'])
        except Exception as e:
            self.journal.mark(t['id'],'needs_review',note='继续指令结果未确认；不会自动重发。'+str(e)[:180]); self.log('resume_unconfirmed',t['id'])

    def tick_actions(self):
        if self.recovery.tick(): return
        switching=read_json(self.directory/'manual-switch.json',{})
        if switching.get('phase') in ('scanning','switching') and time.time()-switching.get('updated_at',0)<300:
            return
        if not self.desktop or not self.desktop.connected or not self.desktop.compatible: return
        decision=quota_decision(self.quota,self.settings)
        threads=self.desktop.snapshots()
        # A user action supersedes an automatic pause; never revive a newer turn.
        for tid,record in list(self.journal.data['tasks'].items()):
            t=threads.get(tid)
            if record.get('phase')=='paused' and t and t.get('turn_id') and t['turn_id']!=record['turn_id']:
                self.journal.mark(tid,'superseded',note='用户或其他程序已继续该聊天')
        if decision=='pause':
            for t in threads.values():
                if is_active(t) and self.permitted(t): self.pause_one(t)
            self.auto_switch_if_needed()
        elif decision=='resume' and self.settings['auto_resume'] and time.time()-self.last_resume>=self.settings['resume_spacing_seconds']:
            for record in list(self.journal.data['tasks'].values()):
                if record.get('phase')=='paused':
                    before=self.last_resume; self.resume_one(record)
                    if self.last_resume!=before: break
        elif decision=='checkpoint':
            for t in threads.values():
                key=(self.account,t['id'],t.get('turn_id'))
                if is_active(t) and self.permitted(t) and key not in self.checkpoints:
                    self.checkpoints.add(key)
                    try: self.desktop.action('checkpoint',t,text=CHECKPOINT_TEXT); self.log('checkpoint_requested',t['id'])
                    except Exception: self.log('checkpoint_unconfirmed',t['id'])
        self.task_queue.tick()

    def can_auto_switch(self):
        fresh=load_settings()
        if self.stop.is_set() or not fresh['enabled'] or not fresh['auto_switch'] or not fresh['auto_resume'] or not plugin_enabled(): return False
        if not self.desktop or not self.desktop.connected or not self.desktop.compatible: return False
        if quota_decision(self.quota,fresh)!='pause': return False
        threads=self.desktop.snapshots()
        if any(t.get('status')=='inProgress' or t.get('runtime_status')=='active' or t.get('waiting') for t in threads.values()): return False
        return any(tid in threads and resume_eligible(r,threads[tid]) and self.permitted(threads[tid])
                   for tid,r in self.journal.records().items())

    def auto_switch_if_needed(self):
        if not self.can_auto_switch(): return
        records=self.journal.records()
        key=[self.account,sorted((tid,r['turn_id']) for tid,r in records.items() if r.get('phase')=='paused')]
        path=self.directory/'auto-switch-attempt.json'
        previous=read_json(path,{})
        key=json.loads(json.dumps(key))
        if previous.get('key')==key or time.time()-previous.get('at',0)<300: return
        # Durable intent prevents repeated clicks after a timeout or process restart.
        atomic_json(path,{'key':key,'at':time.time()})
        try:
            from manual_switch import switch_to_available
            switch_to_available(automatic=True,allowed=self.can_auto_switch)
            self.log('auto_switch_attempt_completed')
        except Exception as exc:
            self.log('auto_switch_needs_review',detail=type(exc).__name__)
        finally:
            with self.lock: self.quota=None; self.checked_high=0; self.next_poll=0

    def actions_worker(self):
        try: self.tick_actions()
        except Exception as e:
            self.error='任务控制异常：'+str(e)[:240]
            self.log('controller_error',detail=type(e).__name__)

    def commands(self):
        folder=self.directory/'commands'; folder.mkdir(exist_ok=True)
        for path in list(folder.glob('*.json')):
            try:
                cmd=read_json(path,{})
                if cmd.get('action')=='check':
                    self.next_poll=0; self.quota=None; self.checked_high=0
                elif cmd.get('action')=='check_updates': self.updates.request_check()
                elif cmd.get('action')=='cancel_resume':
                    tid=cmd.get('thread')
                    if tid in self.journal.data['tasks']:
                        self.journal.mark(tid,'cancelled',note='用户取消自动恢复'); self.log('cancelled',tid)
                elif cmd.get('action')=='cancel_wait':
                    plan=read_json(self.directory/'reset-wait.json',{})
                    if plan.get('id') and cmd.get('plan_id',plan['id'])==plan['id']:
                        atomic_json(self.directory/'reset-cancel.json',{'id':plan['id']})
                        self.recovery.cancel(plan,'用户取消等待重置计划，不再自动启动或继续这些任务。')
                elif cmd.get('action')=='stop': self.stop.set()
                elif cmd.get('action')=='reload_paths': self.paths_reload_requested=True
            finally: path.unlink(missing_ok=True)

    def run(self):
        self.log('service_started')
        try:
            while not self.stop.is_set():
                try:
                    new=load_settings()
                    if new!=self.settings: self.settings=new; self.next_poll=0; self.checked_high=0
                    self.commands()
                    if getattr(self,'paths_reload_requested',False) and not self.quota_inflight and (self.worker is None or not self.worker.is_alive()):
                        self.reader.close()
                        if self.desktop:self.desktop.close();self.desktop=None
                        self.last_discovered=set();self.next_discovery=0;self.next_poll=0;self.quota=None;self.checked_high=0
                        self.paths_reload_requested=False
                    if not plugin_enabled() or not self.settings['enabled']:
                        if self.worker is None or not self.worker.is_alive(): self.recovery.tick()
                        if self.desktop: self.desktop.close(); self.desktop=None; self.last_discovered=set()
                        self.quota=None; self.checked_high=0
                    else:
                        self.updates.maybe_check()
                        if not self.desktop or self.desktop.proc.closed:
                            if self.desktop: self.desktop.close()
                            self.desktop=Desktop(); self.next_discovery=0; self.last_discovered=set()
                        if self.desktop.connected and time.time()>=self.next_discovery:
                            ids=set(discover_threads())|set(self.journal.data['tasks'])|set(self.task_queue.store.read()['threads'])
                            registrations=self.directory/'registrations'
                            if registrations.exists():
                                for p in registrations.glob('*.json'):
                                    item=read_json(p,{})
                                    if item.get('session_id'): ids.add(item['session_id'])
                            self.desktop.follow(sorted(ids-self.last_discovered))
                            # Retry missing owners (for chats opened after a desktop restart).
                            missing=ids-set(self.desktop.snapshots())
                            if missing: self.desktop.follow(sorted(missing))
                            self.last_discovered=ids; self.next_discovery=time.time()+60
                        if not self.quota_inflight and time.time()>=self.next_poll:
                            self.next_poll=time.time()+self.poll_delay()
                            threading.Thread(target=self.fetch_quota,daemon=True).start()
                        if self.worker is None or not self.worker.is_alive():
                            self.worker=threading.Thread(target=self.actions_worker,daemon=True); self.worker.start()
                    self.report()
                except Exception as e:
                    self.error='监控异常：'+str(e)[:240]
                    try: self.report()
                    except Exception: pass
                plan=read_json(self.directory/'reset-wait.json',{})
                self.stop.wait(.5 if plan.get('phase') in ('requested','closed','opening','recovering') else 2)
        finally:
            if self.desktop: self.desktop.close()
            self.reader.close(); self.log('service_stopped')

def lock_instance():
    path=data_dir()/'service.lock'; path.parent.mkdir(parents=True,exist_ok=True)
    f=path.open('a+b'); f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
    if os.name=='nt':
        import msvcrt
        try: msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError: f.close(); return None
    else:
        import fcntl
        try: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError: f.close(); return None
    return f

def hook():
    try:
        event=json.load(sys.stdin)
        if not plugin_enabled(event.get('cwd')) or not load_settings()['enabled']: return
        tid=event.get('session_id')
        if not isinstance(tid,str) or not all(c in '0123456789abcdef-' for c in tid.lower()): return
        atomic_json(data_dir()/'registrations'/f'{tid}.json',{
            'session_id':tid,'cwd':event.get('cwd'),'event':event.get('hook_event_name'),'at':time.time()})
        ensure_service()
        if event.get('hook_event_name')=='SessionStart':
            print(json.dumps({'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':
                 'Codex Auto Switch Assistant已为本机聊天启用。额度由后台程序监测，无需每轮调用工具。若收到额度恢复消息，请检查中断现场后继续原任务；不要重复未确认的外部操作。用户可在插件栏关闭此插件。'}}))
    except Exception: pass  # A monitor failure must never block an unrelated user task.

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['daemon','hook','settings','start','status','probe'])
    p.add_argument('--independent',action='store_true');p.add_argument('--data-dir');p.add_argument('--codex-home');a=p.parse_args()
    if a.data_dir:os.environ['CODEX_QUOTA_GUARD_DATA']=a.data_dir
    if a.codex_home:os.environ['CODEX_HOME']=a.codex_home
    if a.command=='daemon':
        if os.name=='nt' and not a.independent:
            from service_host import launch_detached
            launch_detached();return
        handle=lock_instance()
        if handle is None: return
        try: Guard().run()
        finally: handle.close()
    elif a.command=='hook': hook()
    elif a.command=='settings':
        from settings_ui import run_ui
        run_ui()
    elif a.command=='start': ensure_service()
    elif a.command=='status': print(json.dumps(read_json(data_dir()/'status.json',{}),ensure_ascii=False,indent=2))
    elif a.command=='probe':
        reader=QuotaReader()
        try:
            account,result=reader.read()
            print(json.dumps({'account_fingerprint':account,'quota':normalize_quota(result)},ensure_ascii=False,indent=2))
        finally: reader.close()

if __name__=='__main__': main()
