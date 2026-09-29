"""Durable reset+5min wait, independent of the desktop and model lifetime."""
import math
import re
import time
import uuid
from core import atomic_json,read_json,normalize_quota,quota_decision,is_active,resume_eligible,poll_interval
from runtime import data_dir,load_settings,plugin_enabled,QuotaReader

ACTIVE_PHASES={'requested','preparing','closing','closed','activating','waiting','switching_recovery','opening','recovering','needs_review'}


def countdown_reset(snapshot,now=None):
    now=time.time() if now is None else now
    texts=snapshot.get('texts',[])
    indices=[i for i,t in enumerate(texts) if t.rstrip(':：').strip()=='5小时']
    if len(indices)!=1: return None
    for text in texts[indices[0]+1:indices[0]+6]:
        m=re.fullmatch(r'(?:(\d+)h)?(\d+)m',text)
        if m:
            seconds=int(m[1] or 0)*3600+int(m[2])*60
            if 0<=seconds<=5*3600:
                return now+seconds
    return None


def earliest_reset(candidates,now=None):
    now=time.time() if now is None else now
    eligible=[c for c in candidates if c['quota']['week']>0 and isinstance(c.get('reset_estimate'),(int,float))
              and math.isfinite(c['reset_estimate']) and now-60<=c['reset_estimate']<=now+5*3600+60]
    return min(eligible,key=lambda c:(c['reset_estimate'],-c['quota']['week'],c['id'])) if eligible else None


def request_wait(active,target,automatic,candidates=()):
    return request_restart(active,target,automatic,candidates,wait_reset=True)


def request_restart(active,target,automatic,candidates=(),wait_reset=False):
    path=data_dir()/'reset-wait.json'
    previous=read_json(path,{})
    if previous.get('phase') in ACTIVE_PHASES: raise RuntimeError('已有等待重置计划，请先取消或等待完成。')
    atomic_json(path,dict(version=1,id=uuid.uuid4().hex,phase='requested',automatic=automatic,mode='restart',wait_reset=wait_reset,
         created_at=time.time(),active={k:active[k] for k in ('id','account','name')},
         target={k:target[k] for k in ('id','account','name')},reset_estimate=target.get('reset_estimate'),
         reset_estimates={c['account']:c.get('reset_estimate') for c in candidates},
         message='等待后台保存任务，正常退出后换号并重开。'))


class ResetRecovery:
    def __init__(self,guard):
        self.guard=guard; self.path=guard.directory/'reset-wait.json'
    def save(self,plan,**changes):
        with self.guard.lock:
            if read_json(self.guard.directory/'reset-cancel.json',{}).get('id')==plan.get('id') and changes.get('phase')!='cancelled':
                raise RuntimeError('用户已取消等待计划。')
            plan.update(changes,updated_at=time.time()); atomic_json(self.path,plan)
        self.cleanup_wake(plan)
    def cleanup_wake(self,plan):
        if plan.get('phase') in ('cancelled','complete','needs_review') and plan.get('wake_task'):
            from desktop_lifecycle import remove_wake
            if remove_wake(plan['wake_task']):
                with self.guard.lock:
                    current=read_json(self.path,{})
                    if current.get('id')==plan.get('id') and current.get('phase')==plan.get('phase'):
                        plan['wake_task']=None;current['wake_task']=None;atomic_json(self.path,current)
    def permitted(self,plan):
        s=load_settings()
        needs_wait=plan.get('wait_reset',plan.get('mode')!='restart')
        return (not self.guard.stop.is_set() and plugin_enabled() and s['enabled'] and s['auto_resume'] and (not needs_wait or s['wait_for_reset'])
                and (not plan.get('automatic') or s['auto_switch'])
                and read_json(self.guard.directory/'reset-cancel.json',{}).get('id')!=plan.get('id'))
    def records(self,plan):
        records=self.guard.journal.records()
        return {tid:records[tid] for tid,turn in plan.get('threads',{}).items()
                if tid in records and records[tid].get('turn_id')==turn and records[tid].get('phase')=='paused'}
    def resume_enabled(self,plan):
        return not plan.get('wait_reset',plan.get('mode')!='restart') or load_settings()['wait_auto_resume']
    def cancel(self,plan,message):
        for tid in self.records(plan): self.guard.journal.mark(tid,'cancelled',note=message)
        self.save(plan,phase='cancelled',message=message)
    def query(self):
        reader=QuotaReader()
        try:
            account,raw=reader.read(); return account,normalize_quota(raw,load_settings()['limit_id'])
        finally: reader.close()
    def confirm_shutdown_ready(self,plan):
        g=self.guard
        if not self.permitted(plan) or (not self.records(plan) and not plan.get('reopen_only')): raise RuntimeError('退出计划已取消或没有待恢复任务。')
        if not g.desktop or not g.desktop.connected or not g.desktop.compatible: raise RuntimeError('桌面连接发生变化，未退出。')
        if any(t.get('status')=='inProgress' or t.get('runtime_status')=='active' or t.get('waiting') for t in g.desktop.snapshots().values()):
            raise RuntimeError('退出前发现新任务或待输入状态，未退出。')
        for tid,record in self.records(plan).items():
            current=g.current(tid)
            if not current or not resume_eligible(record,current) or not g.permitted(current):
                raise RuntimeError('退出前原任务状态已改变，未退出。')
    def prepare_restart(self,plan):
        from manual_switch import provider_inventory,ui_request,remaining_from_card,select_candidate
        from desktop_lifecycle import desktop_instance,quit_desktop,register_wake,shutdown_identity
        g=self.guard
        if not g.desktop or not g.desktop.connected or not g.desktop.compatible: return
        instance=desktop_instance()
        if instance is None: raise RuntimeError('未找到可确认的 Codex 桌面实例，未退出。')
        threads=g.desktop.snapshots()
        if any(t.get('waiting') or (is_active(t) and not g.permitted(t)) for t in threads.values()):
            raise RuntimeError('存在待输入或不受管理的任务，未退出。')
        self.save(plan,phase='preparing',message='保存任务暂停记录，准备退出后换号。')
        for t in threads.values():
            if is_active(t): g.pause_one(t)
        threads=g.desktop.snapshots()
        if any(t.get('status')=='inProgress' or t.get('runtime_status')=='active' or t.get('waiting') for t in threads.values()):
            raise RuntimeError('任务尚未全部确认停止，未退出。')
        owned={tid:r['turn_id'] for tid,r in g.journal.records().items()
               if tid in threads and resume_eligible(r,threads[tid]) and g.permitted(threads[tid])}
        if not owned and plan.get('automatic'): raise RuntimeError('没有可恢复的暂停任务，未退出。')
        if not owned and any(t.get('status') not in ('completed',None) for t in threads.values()):
            raise RuntimeError('存在未确认归属的中断或失败任务，请先检查，未退出。')
        for tid in owned: uuid.UUID(tid)
        self.save(plan,threads=owned,reopen_only=not bool(owned),desktop_exe=instance.get('exe'))
        active,providers=provider_inventory()
        if active!=plan['active'] or plan['target'] not in providers: raise RuntimeError('账号配置已改变，未退出。')
        snapshot=ui_request('refresh',plan['target']);quota=remaining_from_card(snapshot)
        if quota['week']<=0: raise RuntimeError('候选账号周额度已耗尽，未退出。')
        if not plan.get('wait_reset') and not select_candidate([dict(plan['target'],quota=quota)],load_settings()['resume_5h']):
            raise RuntimeError('候选账号额度已不足，未退出；请重新查询。')
        if not self.permitted(plan): raise RuntimeError('流程已关闭，未退出。')
        self.save(plan,phase='closing',shutdown_identity=shutdown_identity(instance),wake_task='CodexQuotaGuard-Reset-'+str(uuid.UUID(plan['id'])),
                  message='暂停记录已保存，正在正常退出 Codex；退出确认后才切换账号。')
        register_wake(plan['id'],time.time()+60)
        self.confirm_shutdown_ready(plan)
        quit_desktop(instance)
        self.save(plan,phase='closed',message='Codex 已确认退出，准备切换账号。')
        self.activate_after_close(plan)
    def activate_after_close(self,plan):
        from manual_switch import provider_inventory,ui_request,remaining_from_card,select_candidate
        from desktop_lifecycle import desktop_instance
        if not self.records(plan) and not plan.get('reopen_only'):
            self.save(plan,phase='complete',message='所有任务已取消自动恢复，未继续换号。');return
        if not self.permitted(plan): raise RuntimeError('流程已关闭，未切换。')
        if desktop_instance() is not None: raise RuntimeError('Codex 已重新打开，未继续换号。')
        active,providers=provider_inventory();target=plan['target']
        if active!=plan['active'] or target not in providers: raise RuntimeError('退出期间账号配置已改变，停止换号。')
        # Pre-exit refresh already queried the service. Read the current card
        # only; avoid another network refresh while the desktop is closed.
        snapshot=ui_request('inspect',target)
        try: quota=remaining_from_card(snapshot)
        except ValueError:
            snapshot=ui_request('refresh',target);quota=remaining_from_card(snapshot)
        ready=select_candidate([dict(target,quota=quota)],load_settings()['resume_5h'])
        if quota['week']<=0: raise RuntimeError('退出后候选账号周额度耗尽，需检查。')
        if not ready and not plan.get('wait_reset'):
            if not load_settings()['wait_for_reset']: raise RuntimeError('候选账号额度已下降且等待重置关闭，需检查。')
            self.save(plan,wait_reset=True)
        if not self.permitted(plan): raise RuntimeError('流程已关闭，未切换。')
        if desktop_instance() is not None: raise RuntimeError('Codex 已重新打开，未继续换号。')
        # Persist activation intent. A crash in this phase never replays a click.
        self.save(plan,phase='activating',message='Codex 已退出，正在切换账号。')
        if target['account']!=active['account']:
            ui_request('switch',target,currentName=active['name'],expectedTexts=snapshot['texts'])
        current,_=provider_inventory()
        if current!=target: raise RuntimeError('换号结果未确认，需检查。')
        self.guard.quota=None;self.guard.checked_high=0;self.guard.next_poll=0
        if ready and self.resume_enabled(plan):
            # The candidate was checked before activation. Open immediately;
            # regular quota monitoring resumes after the desktop has opened.
            self.save(plan,phase='opening',wait_reset=plan.get('wait_reset',False),opened=[],opening_at=time.time(),last_open=0,
                      message='账号已切换，正在直接重开 Codex。')
            return
        account,exact=self.query()
        windows=exact['windows']
        if '5h' not in windows or 'week' not in windows or windows['week']['remaining']<=0:
            raise RuntimeError('新账号实际额度无法确认，需检查。')
        is_ready=quota_decision(exact,load_settings())=='resume'
        if not is_ready and not load_settings()['wait_for_reset']: raise RuntimeError('新账号未达恢复阈值且等待重置关闭，需检查。')
        reset=windows['5h']['resets_at']
        if not is_ready and not time.time()<reset<=time.time()+5*3600+60: raise RuntimeError('新账号重置时间异常，需检查。')
        interval=load_settings()['poll_low_seconds'] if load_settings()['dynamic_poll'] else load_settings()['poll_seconds']
        self.save(plan,phase='waiting',account=account,wait_reset=plan.get('wait_reset',False) or not is_ready,
                  resets_at=0 if is_ready else reset,wake_at=time.time() if is_ready else reset+300,
                  next_check=time.time()+interval,last_check=time.time(),
                  good_samples=1 if is_ready else 0,ready_account=target['account'] if is_ready else None,
                  message='已退出并换号，等待再次核验额度后重开。' if is_ready else '已退出并换号，等待重置并持续监测额度。')
        self.guard.quota=None;self.guard.checked_high=0;self.guard.next_poll=0
    def monitor_wait(self,plan):
        from manual_switch import provider_inventory,ordered_accounts,ui_request,remaining_from_card,select_candidate
        from account_usage import reset_times
        now=time.time(); settings=load_settings()
        interval=settings['poll_low_seconds'] if settings['dynamic_poll'] else settings['poll_seconds']
        due=min(plan.get('next_check',0),plan.get('last_check',now)+interval)
        if now<due: return False
        # Poll throughout the wait, including before the predicted natural reset.
        self.save(plan,next_check=now+interval,last_check=now)
        active,providers=provider_inventory()
        candidates=[]; observations=[]; active_exact=None; active_account=None
        for provider in ordered_accounts(providers,[]):
            if not self.permitted(plan): return False
            try:
                if provider['account']==active['account']:
                    active_account,active_exact=self.query()
                    if active_account!=plan['account']:
                        self.cancel(plan,'当前登录身份已被更改，已取消等待计划。');return False
                    if quota_decision(active_exact,settings)=='unknown': raise ValueError('额度过期或缺失')
                    quota={k:v['remaining'] for k,v in active_exact['windows'].items()}
                    display_resets={k:v.get('resets_at') for k,v in active_exact['windows'].items()}
                    estimated=False
                else:
                    snapshot=ui_request('refresh',provider);quota=remaining_from_card(snapshot)
                    display_resets=reset_times(snapshot);estimated=True
                baseline=plan.get('reset_estimates',{}).get(provider['account'])
                if provider['account']==plan['target']['account']: baseline=plan['resets_at']
                # Natural reset gets its full grace period. A recovery clearly before
                # that boundary is an early/manual reset and can be used immediately.
                # Other cards floor their countdown to whole minutes. Add a
                # minute to that estimate so their natural grace is never short.
                rounding=0 if provider['account']==plan['target']['account'] else 60
                ready_time=(baseline is None or now<baseline-90 or now>=baseline+300+rounding)
                observations.append(dict(id=provider['id'],name=provider['name'],quota=quota,resets_at=display_resets,reset_estimated=estimated,checked_at=time.time()))
                if ready_time: candidates.append(dict(provider,quota=quota))
            except Exception:
                observations.append(dict(id=provider['id'],name=provider['name'],error='查询失败或额度不可用',checked_at=time.time()))
        atomic_json(self.guard.directory/'account-observations.json',{'checked_at':time.time(),'accounts':observations})
        if not self.resume_enabled(plan):
            self.save(plan,good_samples=0,ready_account=None,message='持续监测中：自动重启任务已关闭；重新勾选后将重新核验额度。')
            return False
        winner=select_candidate(candidates,settings['resume_5h'],settings['selection_strategy'],settings['account_priority'])
        if not winner:
            self.save(plan,good_samples=0,ready_account=None,message='持续监测中：暂无可恢复账号；自然重置后保留 5 分钟缓冲。')
            return False
        count=plan.get('good_samples',0)+1 if plan.get('ready_account')==winner['account'] else 1
        self.save(plan,good_samples=count,ready_account=winner['account'],message='检测到额度恢复，正在连续核验：'+winner['name'])
        if count<2 or not self.permitted(plan): return False
        switched=False
        if winner['account']!=active['account']:
            # Durable intent: never replay an uncertain account activation after a crash.
            current,providers_now=provider_inventory()
            if current!=active or not any(p['id']==winner['id'] and p['account']==winner['account'] for p in providers_now):
                raise RuntimeError('候选账号配置已改变，未切换。')
            snapshot=ui_request('refresh',winner)
            if not select_candidate([dict(winner,quota=remaining_from_card(snapshot))],load_settings()['resume_5h']):
                self.save(plan,good_samples=0,message='候选账号复核未达标，继续监测。');return False
            if not self.permitted(plan) or not self.resume_enabled(plan): return False
            from desktop_lifecycle import desktop_instance
            if desktop_instance() is not None: raise RuntimeError('等待期间 Codex 已重新打开，未继续换号。')
            self.save(plan,phase='switching_recovery',message='正在切换到提前恢复额度的账号。')
            ui_request('switch',winner,currentName=active['name'],expectedTexts=snapshot['texts'])
            current,_=provider_inventory()
            if current['account']!=winner['account']: raise RuntimeError('提前恢复账号切换未确认。')
            switched=True;active_account=None
        if not switched and (not active_exact or quota_decision(active_exact,load_settings())!='resume'):
            if plan['phase']=='switching_recovery': raise RuntimeError('换号后精确额度未确认，等待人工检查。')
            self.save(plan,good_samples=0);return False
        self.save(plan,target={k:winner[k] for k in ('id','account','name')},account=active_account,early_recovery=time.time()<plan['wake_at'])
        return True
    def tick(self):
        plan=read_json(self.path,{})
        if self.guard.stop.is_set(): return plan.get('phase') in ACTIVE_PHASES
        self.cleanup_wake(plan)
        if plan.get('phase') not in ACTIVE_PHASES: return False
        if not self.permitted(plan):
            self.cancel(plan,'等待恢复开关或自动管理已关闭，计划已取消。'); return False
        if plan.get('phase')=='needs_review': return True
        try:
            if self.guard.desktop:
                for tid,t in self.guard.desktop.snapshots().items():
                    if tid in plan.get('threads',{}) and t.get('turn_id') and t['turn_id']!=plan['threads'][tid]:
                        record=self.guard.journal.records().get(tid,{})
                        if record.get('phase')=='paused': self.guard.journal.mark(tid,'superseded',note='等待期间用户已接管该聊天')
            if plan['phase']=='requested':
                if time.time()-plan.get('created_at',0)>300:
                    self.cancel(plan,'旧等待请求已过期；请重新检查额度后切换账号。');return False
                if plan.get('mode')!='restart': self.save(plan,mode='restart',wait_reset=True)
                self.prepare_restart(plan)
                if plan['phase']!='opening': return True
            if plan['phase']=='closing' and plan.get('mode')=='restart':
                from desktop_lifecycle import confirmed_shutdown
                if not confirmed_shutdown(plan.get('shutdown_identity')):
                    raise RuntimeError('上次退出未确认或 Codex 已重新打开；请保持主窗口打开，重新点击手动切换以重新核验。')
                self.save(plan,phase='closed',message='后台已重新核验原进程退出，继续尚未执行的换号。')
            if plan['phase']=='closed':
                self.activate_after_close(plan)
                if plan['phase']!='opening': return True
            if plan['phase'] in ('preparing','closing','switching_recovery','activating'):
                from desktop_lifecycle import desktop_instance
                if plan['phase']=='closing' and plan.get('mode')=='restart':
                    raise RuntimeError('上次退出尚未确认，需检查；不会猜测已退出或重复换号。')
                elif plan['phase']=='closing' and desktop_instance() is None and plan.get('wake_at'):
                    self.save(plan,phase='waiting',message='后台已恢复等待计划。')
                else: raise RuntimeError('上次准备或退出结果未确认，请检查后取消计划；不会重复操作。')
            if not self.records(plan) and not plan.get('reopen_only'): self.save(plan,phase='complete',message='等待计划中的任务已恢复、取消或被接管。'); return False
            from manual_switch import provider_inventory
            active,_=provider_inventory()
            if active['account']!=plan['target']['account']:
                self.cancel(plan,'等待期间账号被更改，已取消原计划。'); return False
            if plan['phase']=='waiting':
                if not self.monitor_wait(plan):
                    if not self.guard.stop.is_set() and not self.permitted(plan):
                        self.cancel(plan,'等待计划已关闭或取消。')
                    return True
                if not self.permitted(plan) or not self.resume_enabled(plan): return True
                self.save(plan,phase='opening',opened=[],opening_at=time.time(),last_open=0,
                          message='额度已确认恢复，正在重新打开原聊天。')
                self.guard.quota=None;self.guard.checked_high=0;self.guard.next_poll=0
            if plan['phase'] in ('opening','recovering'):
                if not self.resume_enabled(plan):
                    if not plan.get('resume_hold_since'):
                        self.save(plan,resume_hold_since=time.time(),message='自动重启任务已关闭，保留计划等待重新开启。')
                    return True
                if plan.get('resume_hold_since'):
                    self.save(plan,resume_hold_since=None,opening_at=time.time())
            if plan['phase']=='opening':
                from desktop_lifecycle import open_chat,open_desktop,desktop_instance
                if not plan.get('desktop_open_sent'):
                    self.save(plan,desktop_open_sent=True,message='正在通过 Windows 应用入口重开 Codex。')
                    launched_pid=open_desktop(plan.get('desktop_exe') or (desktop_instance() or {}).get('exe',''))
                    self.save(plan,launched_pid=launched_pid)
                    self.guard.next_discovery=0
                    return True
                instance=desktop_instance()
                if not instance or (plan.get('launched_pid') and instance['pid']!=plan['launched_pid']):
                    if time.time()-plan['opening_at']>90: raise RuntimeError('未确认所启动的 Codex 主进程，请检查；不会重复启动。')
                    return True
                if plan.get('reopen_only'):
                    self.save(plan,phase='complete',message='已退出、换号并重新打开 Codex；没有发送继续指令。');return False
                # Each deep link is issued once; no model message is sent by the launcher.
                remaining=[tid for tid in self.records(plan) if tid not in plan['opened']]
                if remaining and time.time()-plan['last_open']>=1:
                    tid=remaining[0]
                    self.save(plan,opened=plan['opened']+[tid],last_open=time.time())
                    open_chat(tid)
                    return True
                threads=self.guard.desktop.snapshots() if self.guard.desktop else {}
                if not remaining and all(tid in threads for tid in self.records(plan)):
                    self.save(plan,phase='recovering',message='原聊天已打开，正在核验暂停归属并逐个继续。')
                elif time.time()-plan['opening_at']>180:
                    raise RuntimeError('原聊天未全部重新连接；请检查桌面，不会重复发送继续。')
                else: return True
            if plan['phase']=='recovering':
                return False  # Existing controller verifies original turn IDs before resume.
        except Exception as exc:
            if self.guard.stop.is_set(): return True
            if not self.permitted(plan): self.cancel(plan,'等待计划已关闭或取消。')
            else: self.save(plan,review_phase=plan['phase'],phase='needs_review',message=str(exc) if isinstance(exc,(RuntimeError,ValueError)) else '等待恢复流程异常，请检查后手动处理。')
        return True
