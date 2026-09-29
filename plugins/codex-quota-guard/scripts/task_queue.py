"""User-authored per-chat queue, persisted outside Codex's native send queue."""
import copy
from contextlib import contextmanager
import os
import time
import uuid
from core import atomic_json,read_json,quota_decision
from runtime import data_dir,codex_home,load_settings

PHASES={'queued':'待执行','send_pending':'发送中','running':'执行中','completed':'已结束',
        'needs_review':'需检查','cancelled':'已移除','resolved':'已手动处理'}
DELETABLE_PHASES=frozenset(('queued','completed','cancelled','resolved'))

def finished(thread):
    return (thread.get('status')=='completed' and thread.get('runtime_status') in ('idle','notLoaded')
            and not thread.get('waiting') and not thread.get('is_child') and not thread.get('ephemeral')
            and thread.get('goal_status') in (None,'complete','completed'))

def native_queue_clear(tid):
    try:
        state=read_json(codex_home()/'.codex-global-state.json',None)
        if not isinstance(state,dict): return False
        queues=state.get('queued-follow-ups')
        return isinstance(queues,dict) and not queues.get(tid)
    except (ValueError,OSError): return False

class QueueStore:
    def __init__(self,directory=None):
        self.directory=directory or data_dir();self.path=self.directory/'task-queues.json'
    def read(self):
        value=read_json(self.path,{'version':1,'threads':{}})
        if value.get('version')!=1 or not isinstance(value.get('threads'),dict): raise ValueError('队列记录格式不兼容')
        return value
    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True,exist_ok=True)
        with (self.directory/'task-queues.lock').open('a+b') as handle:
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            if os.name=='nt':
                import msvcrt
                deadline=time.monotonic()+5
                while True:
                    try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1);break
                    except OSError:
                        if time.monotonic()>deadline: raise RuntimeError('队列正在更新，请稍后重试。')
                        time.sleep(.05)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX)
            yield
    def change(self,tid,action):
        with self.locked():
            value=self.read();queue=value['threads'].get(tid)
            if queue is None: raise ValueError('队列不存在')
            result=action(queue);queue['updated_at']=time.time();atomic_json(self.path,value)
            return result
    def add(self,thread,text):
        text=text.strip()
        if not text or len(text)>100000: raise ValueError('请输入 1–100000 字符的任务内容。')
        tid=str(uuid.UUID(thread['id']));ident=uuid.uuid4().hex
        with self.locked():
            value=self.read();queue=value['threads'].setdefault(tid,dict(id=tid,title=thread.get('title',tid),
                enabled=True,anchor=thread.get('turn_id'),items=[],note='等待当前任务结束及额度达标。'))
            if not any(i['phase'] in ('queued','running','send_pending','needs_review') for i in queue['items']):
                queue['anchor']=thread.get('turn_id')
            queue['items'].append(dict(id=ident,text=text,phase='queued',created_at=time.time()))
            queue['updated_at']=time.time();atomic_json(self.path,value)
        return ident
    def set_enabled(self,tid,enabled,thread=None):
        def change(q):
            if enabled and any(i['phase'] in ('send_pending','needs_review') for i in q['items']):
                raise ValueError('请先在队列窗口检查并标记结果不明的条目。')
            if enabled and not q['enabled'] and not any(i['phase']=='running' for i in q['items']):
                if not thread: raise ValueError('需先打开原聊天，确认当前任务状态。')
                q['anchor']=thread.get('turn_id')
            q['enabled']=enabled;q['note']='等待当前任务结束及额度达标。' if enabled else '用户已暂停队列；已发送的任务继续保持原状。'
        self.change(tid,change)
    def edit(self,tid,ident,text):
        text=text.strip()
        if not text or len(text)>100000: raise ValueError('请输入 1–100000 字符的任务内容。')
        def change(q):
            item=next(i for i in q['items'] if i['id']==ident)
            if item['phase']!='queued': raise ValueError('只能编辑尚未发送的条目。')
            item['text']=text
        self.change(tid,change)
    def move(self,tid,ident,delta):
        def change(q):
            positions=[n for n,i in enumerate(q['items']) if i['phase']=='queued']
            current=next((n for n in positions if q['items'][n]['id']==ident),None)
            if current is None: raise ValueError('只能调整尚未发送的条目。')
            index=positions.index(current);target=index+delta
            if 0<=target<len(positions):
                other=positions[target];q['items'][current],q['items'][other]=q['items'][other],q['items'][current]
        self.change(tid,change)
    def remove(self,tid,ident):
        def change(q):
            item=next(i for i in q['items'] if i['id']==ident)
            if item['phase']!='queued': raise ValueError('只能移除尚未发送的条目。')
            item['phase']='cancelled'
        self.change(tid,change)
    def resolve(self,tid,ident):
        def change(q):
            item=next(i for i in q['items'] if i['id']==ident)
            if item['phase']!='needs_review': raise ValueError('此操作只用于已检查的结果不明条目。')
            item['phase']='resolved';q['enabled']=False;q['note']='已标记为手动处理；确认当前聊天后可启用队列。'
        self.change(tid,change)
    def delete(self,tid,ident):
        def change(q):
            item=next((i for i in q['items'] if i['id']==ident),None)
            if item is None: raise ValueError('条目已不存在，请刷新队列。')
            if item['phase'] not in DELETABLE_PHASES:
                raise ValueError('不能删除发送中、执行中或结果待确认的条目。')
            q['items'].remove(item)
        # Recheck under the same lock used to claim send intents.
        self.change(tid,change)
    def recover(self):
        for tid in self.read()['threads']:
            def recover(q):
                for item in q['items']:
                    if item['phase']=='send_pending':
                        item['phase']='needs_review';q['enabled']=False;q['note']='上次发送结果未确认，不自动重发。'
            self.change(tid,recover)

class TaskQueue:
    def __init__(self,guard):
        self.guard=guard;self.store=QueueStore(guard.directory);self.store.recover();self.idle_since={}
    def note(self,tid,text):
        if self.store.read()['threads'][tid].get('note')!=text:
            self.store.change(tid,lambda q:q.update(note=text))
    def ready(self,t):
        g=self.guard;s=load_settings()
        switching=read_json(g.directory/'manual-switch.json',{})
        busy=switching.get('phase') in ('scanning','switching') and time.time()-switching.get('updated_at',0)<300
        return (not g.stop.is_set() and not busy and g.permitted(t) and finished(t) and native_queue_clear(t['id'])
                and quota_decision(g.quota,s)=='resume' and g.checked_high>=1
                and time.time()-g.last_resume>=s['resume_spacing_seconds'])
    def review(self,tid,text):
        def change(q):
            q['enabled']=False;q['note']=text
            for item in q['items']:
                if item['phase'] in ('running','send_pending'): item['phase']='needs_review'
        self.store.change(tid,change)
    def tick(self):
        g=self.guard
        for tid,queue in self.store.read()['threads'].items():
            if not queue['enabled']: self.idle_since.pop(tid,None);continue
            if not any(i['phase'] in ('queued','running','send_pending') for i in queue['items']): continue
            t=g.current(tid)
            if not t: self.idle_since.pop(tid,None);self.note(tid,'等待打开原聊天并建立连接。');continue
            if not g.permitted(t): self.idle_since.pop(tid,None);continue
            running=next((i for i in queue['items'] if i['phase']=='running'),None)
            anchor=queue.get('anchor')
            if t.get('turn_id')!=anchor:
                record=g.journal.records().get(tid,{})
                resumed=(record.get('turn_id')==anchor and record.get('phase') in ('resumed','resume_pending')
                         and t.get('message_id')==record.get('operation_id') and bool(t.get('message_id')))
                own=running and t.get('message_id')==running.get('operation_id')
                if resumed or own:
                    def adopt(q):
                        q['anchor']=t['turn_id']
                        if running:
                            next(i for i in q['items'] if i['id']==running['id']).update(turn_id=t['turn_id'],observed=True)
                    self.store.change(tid,adopt)
                    if running: running.update(turn_id=t['turn_id'],observed=True)
                    anchor=t['turn_id']
                elif running and not running.get('observed') and time.time()-running.get('sent_at',0)<30:
                    continue  # The last pre-send snapshot may still be cached.
                else:
                    self.review(tid,'检测到其他输入或无法确认原任务归属，已暂停队列；检查聊天后再启用。');continue
            if running:
                confirmed=(t.get('message_id')==running.get('operation_id') or
                           bool(running.get('turn_id')) and t.get('turn_id')==running['turn_id'])
                if not running.get('observed') and not confirmed:
                    if time.time()-running.get('sent_at',0)<30: continue
                    self.review(tid,'无法确认已发送条目的新轮次，请检查原聊天；不会重复发送。');continue
                pause=g.journal.records().get(tid,{})
                owned_pause=pause.get('turn_id')==t.get('turn_id') and pause.get('phase') in ('paused','pause_pending','resume_pending')
                if t.get('status')=='failed' or (t.get('status')=='interrupted' and not owned_pause):
                    self.review(tid,'本项失败或被手动中断，已暂停队列；请检查原聊天，未自动执行下一项。');continue
                def observe(q):
                    item=next(i for i in q['items'] if i['id']==running['id']);item['observed']=True
                    if finished(t): item.update(phase='completed',completed_at=time.time())
                    if not any(i['phase'] in ('queued','running','send_pending','needs_review') for i in q['items']):
                        q['note']='全部队列条目已处理。'
                if not running.get('observed') or finished(t): self.store.change(tid,observe)
                if finished(t) and not any(i['phase']=='queued' for i in self.store.read()['threads'][tid]['items']): continue
            if not finished(t):
                self.idle_since.pop(tid,None)
                self.note(tid,'等待当前任务真正结束；中断、失败、目标进行中或待输入不会发送下一项。');continue
            record=g.journal.records().get(tid,{})
            if record.get('phase') in ('pause_pending','paused','resume_pending','needs_review'):
                self.idle_since.pop(tid,None);self.note(tid,'等待原任务恢复完成或检查暂停记录。');continue
            if not native_queue_clear(tid):
                self.idle_since.pop(tid,None);self.note(tid,'Codex 自带队列仍有内容或无法读取；先处理原生待发送消息。');continue
            key=(t.get('turn_id'),t.get('message_id'))
            observed=self.idle_since.get(tid)
            if not observed or observed[0]!=key: self.idle_since[tid]=(key,time.time());continue
            if time.time()-self.idle_since[tid][1]<max(5,load_settings()['resume_spacing_seconds']): continue
            if not self.ready(t): self.note(tid,'等待当前账号额度达标；一次检查通过即可发送下一项。');continue
            def intent(q):
                if not q['enabled'] or q.get('anchor')!=t.get('turn_id'): return None
                if any(i['phase'] in ('running','send_pending','needs_review') for i in q['items']): return None
                item=next((i for i in q['items'] if i['phase']=='queued'),None)
                if item is None: q['note']='全部队列条目已处理。';return None
                item.update(phase='send_pending',operation_id=uuid.uuid4().hex)
                q['note']='正在发送下一项…';return copy.deepcopy(item)
            item=self.store.change(tid,intent)
            if not item: continue
            try:
                current=g.current(tid)
                latest=self.store.read()['threads'][tid]
                if (not latest['enabled'] or not current or current.get('turn_id')!=t.get('turn_id') or not self.ready(current)):
                    self.store.change(tid,lambda q:next(i for i in q['items'] if i['id']==item['id']).update(phase='queued'))
                    continue
                result=g.desktop.action('queue_start',current,operation_id=item['operation_id'],text=item['text'])
                payload=result.get('result',result)
                turn=(payload.get('turn') or {}).get('id')
                def sent(q):
                    target=next(i for i in q['items'] if i['id']==item['id'])
                    target.update(phase='running',sent_at=time.time(),observed=False,turn_id=turn)
                    if turn: q['anchor']=turn
                    q['note']='已发送，等待此项结束。'
                self.store.change(tid,sent)
                g.last_resume=time.time();g.next_poll=0;g.checked_high=0
                g.log('queue_sent',tid,detail=item['id']);self.idle_since.pop(tid,None)
                return
            except Exception:
                self.review(tid,'发送结果未确认，不会自动重发；请检查原聊天后标记已处理。')
