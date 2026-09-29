import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import DEFAULTS,atomic_json
from guard import Guard
from task_queue import TaskQueue,QueueStore,native_queue_clear
from test_guard import FakeDesktop

TID='11111111-2222-4333-8444-555555555555'

class Desktop(FakeDesktop):
    def __init__(self):
        super().__init__();self.thread.update(id=TID,title='Test',status='completed',runtime_status='idle',message_id='old')
        self.texts=[];self.ambiguous=False;self.lag=False
    def snapshots(self): return {TID:dict(self.thread)}
    def action(self,op,t,**kwargs):
        if op=='interrupt':
            self.calls.append(op);self.thread.update(status='interrupted',runtime_status='idle')
            return {'ok':True,'interruptedTurnId':t['turn_id']}
        if op!='queue_start':
            result=super().action(op,t,**kwargs)
            if op=='resume': self.thread['message_id']=kwargs['operation_id']
            return result
        self.calls.append(op);self.texts.append(kwargs['text'])
        if self.lose_reply: raise TimeoutError('uncertain')
        turn='q'+str(len(self.calls))
        if not self.lag: self.thread.update(turn_id=turn,message_id=kwargs['operation_id'],status='inProgress',runtime_status='active')
        return {} if self.ambiguous else {'turn':{'id':turn}}

class QueueTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.start(patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':str(self.root)}))
        self.start(patch('guard.plugin_enabled',return_value=True))
        self.start(patch('task_queue.codex_home',return_value=self.root))
        atomic_json(self.root/'.codex-global-state.json',{'queued-follow-ups':{}})
        self.g=Guard();self.g.desktop=Desktop();self.g.account='test'
        self.q=self.g.task_queue;self.store=self.q.store;self.high()
    def start(self,p):
        value=p.start();self.addCleanup(p.stop);return value
    def high(self):
        self.g.quota={'checked_at':time.time(),'windows':{k:dict(remaining=80,resets_at=time.time()+10000) for k in ('5h','week')}}
        self.g.checked_high=2;self.g.last_resume=0
    def add(self,text='next'):
        return self.store.add(self.g.desktop.thread,text)
    def state(self): return self.store.read()['threads'][TID]
    def dispatch(self):
        self.q.tick()
        if TID in self.q.idle_since:
            key,_=self.q.idle_since[TID];self.q.idle_since[TID]=(key,time.time()-120)
        self.q.tick()
    def finish(self): self.g.desktop.thread.update(status='completed',runtime_status='idle')
    def test_two_items_are_sent_serially_once(self):
        self.add('first\nmultiline');self.add('second');self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first\nmultiline'])
        self.q.tick();self.dispatch();self.assertEqual(len(self.g.desktop.texts),1)
        self.finish();self.high();self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first\nmultiline','second'])
        self.finish();self.q.tick();self.q.tick()
        self.assertEqual([i['phase'] for i in self.state()['items']],['completed','completed'])
    def test_failed_interrupted_waiting_and_goals_block_dispatch(self):
        self.add()
        base=dict(self.g.desktop.thread)
        for update in [dict(status='failed'),dict(status='interrupted'),dict(waiting=True),dict(goal_status='active'),dict(goal_status='paused'),dict(runtime_status='active'),dict(is_child=True)]:
            self.g.desktop.thread=dict(base,**update);self.dispatch()
        self.assertEqual(self.g.desktop.calls,[])
    def test_low_stale_missing_week_or_unverified_quota_blocks(self):
        self.add()
        for change in ('low','week','missing','stale','unverified'):
            self.high()
            if change=='low': self.g.quota['windows']['5h']['remaining']=1
            if change=='week': self.g.quota['windows']['week']['remaining']=0
            if change=='missing': self.g.quota['windows'].pop('week')
            if change=='stale': self.g.quota['checked_at']=0
            if change=='unverified': self.g.checked_high=0
            self.dispatch()
        self.assertEqual(self.g.desktop.calls,[])
    def test_one_good_sample_sends_without_waiting_for_second_poll(self):
        self.add('first');self.add('second');self.g.checked_high=1
        self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first'])
        self.assertEqual(self.g.checked_high,0)
        self.finish();self.g.last_resume=0;self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first'])
        self.high();self.g.checked_high=1;self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first','second'])
    def test_plugin_disabled_and_user_paused_queue_block(self):
        self.add()
        with patch('guard.plugin_enabled',return_value=False): self.dispatch()
        self.store.set_enabled(TID,False);self.dispatch()
        self.assertEqual(self.g.desktop.calls,[])
        self.store.set_enabled(TID,True,self.g.desktop.thread);self.dispatch()
        self.assertEqual(len(self.g.desktop.calls),1)
    def test_native_queue_blocks_and_missing_schema_fails_closed(self):
        self.add()
        for state in [{'queued-follow-ups':{TID:[{'text':'native'}]}},{},{'queued-follow-ups':[]}]:
            atomic_json(self.root/'.codex-global-state.json',state);self.dispatch()
        self.assertEqual(self.g.desktop.calls,[])
        atomic_json(self.root/'.codex-global-state.json',{'queued-follow-ups':{}});self.dispatch()
        self.assertEqual(len(self.g.desktop.calls),1)
    def test_manual_input_pauses_queue_instead_of_following_it(self):
        self.add();self.g.desktop.thread['turn_id']='manual'
        self.dispatch();self.assertFalse(self.state()['enabled']);self.assertEqual(self.g.desktop.calls,[])
        self.store.set_enabled(TID,True,self.g.desktop.thread);self.dispatch()
        self.assertEqual(len(self.g.desktop.calls),1)
    def test_quota_resume_before_next_item_preserves_dependency(self):
        self.add();self.g.desktop.thread.update(status='inProgress',runtime_status='active')
        self.g.pause_one(self.g.desktop.thread);self.dispatch();self.assertEqual(self.g.desktop.calls,['interrupt'])
        self.g.journal.data['tasks'][TID]['paused_at']=time.time()-120
        self.g.resume_one(self.g.journal.records()[TID]);self.q.tick()
        self.assertTrue(self.state()['enabled']);self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
        self.finish();self.high();self.dispatch();self.assertEqual(self.g.desktop.calls[-1],'queue_start')
    def test_running_queue_item_can_be_paused_and_resumed(self):
        self.add('first');self.add('second');self.dispatch()
        # Pause before the queue has even observed its own successful submission.
        self.g.pause_one(self.g.desktop.thread)
        self.g.journal.data['tasks'][TID]['paused_at']=time.time()-120
        self.high();self.g.resume_one(self.g.journal.records()[TID]);self.q.tick()
        self.assertTrue(self.state()['enabled']);self.assertEqual(self.g.desktop.texts,['first'])
        self.finish();self.high();self.dispatch();self.assertEqual(self.g.desktop.texts,['first','second'])
    def test_lost_reply_never_retries_even_after_restart(self):
        self.add();self.g.desktop.lose_reply=True;self.dispatch()
        self.assertEqual(self.state()['items'][0]['phase'],'needs_review')
        self.g.task_queue=TaskQueue(self.g);self.g.task_queue.tick()
        self.assertEqual(len(self.g.desktop.calls),1)
    def test_restart_during_send_intent_requires_review(self):
        ident=self.add()
        self.store.change(TID,lambda q:q['items'][0].update(phase='send_pending'))
        self.q=TaskQueue(self.g);self.dispatch()
        self.assertFalse(self.state()['enabled']);self.assertEqual(self.g.desktop.calls,[])
        with self.assertRaises(ValueError):self.store.set_enabled(TID,True,self.g.desktop.thread)
        self.store.resolve(TID,ident);self.store.set_enabled(TID,True,self.g.desktop.thread)
        self.dispatch();self.assertEqual(self.g.desktop.calls,[])
    def test_restart_running_item_tracks_completion_without_resending(self):
        self.add('first');self.add('second');self.dispatch()
        self.q=TaskQueue(self.g);self.q.tick();self.finish();self.high();self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first','second'])
    def test_delayed_old_snapshot_cannot_be_mistaken_for_completion(self):
        self.add('first');self.add('second');self.g.desktop.lag=True;self.g.desktop.ambiguous=True
        self.dispatch();self.finish();self.high();self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first'])
        self.store.change(TID,lambda q:q['items'][0].update(sent_at=time.time()-40))
        self.dispatch();self.assertFalse(self.state()['enabled'])
    def test_pause_between_intent_and_send_prevents_dispatch(self):
        self.add();original=self.g.current;count=0
        def current(tid):
            nonlocal count
            count+=1
            if count==3:self.store.set_enabled(TID,False)
            return original(tid)
        with patch.object(self.g,'current',side_effect=current):self.dispatch()
        self.assertEqual(self.g.desktop.calls,[]);self.assertEqual(self.state()['items'][0]['phase'],'queued')
    def test_reorder_edit_remove_only_unsent_items(self):
        first=self.add('first');second=self.add('second')
        self.store.move(TID,second,-1);self.store.edit(TID,second,'edited');self.store.remove(TID,first)
        self.dispatch();self.assertEqual(self.g.desktop.texts,['edited'])
        for operation in [lambda:self.store.edit(TID,second,'bad'),lambda:self.store.remove(TID,second),lambda:self.store.move(TID,second,1)]:
            with self.assertRaises(ValueError):operation()
    def test_reusing_completed_queue_binds_to_current_task(self):
        self.add('first');self.dispatch();self.finish();self.q.tick()
        self.g.desktop.thread['turn_id']='later';self.add('next');self.high();self.dispatch()
        self.assertEqual(self.g.desktop.texts,['first','next'])
    def test_deleted_queued_item_is_never_dispatched(self):
        first=self.add('delete me');second=self.add('keep me')
        self.store.delete(TID,first)
        self.assertEqual([i['id'] for i in self.state()['items']],[second])
        self.dispatch();self.assertEqual(self.g.desktop.texts,['keep me'])
    def test_delete_finished_records_preserves_queue_state_and_anchor(self):
        for phase in ('completed','cancelled','resolved'):
            ident=self.add(phase)
            self.store.change(TID,lambda q:q['items'][-1].update(phase=phase))
            before=self.state()
            self.store.delete(TID,ident)
            self.assertEqual(self.state()['items'],[])
            self.assertEqual(self.state()['anchor'],before['anchor'])
            self.assertEqual(self.state()['enabled'],before['enabled'])
    def test_delete_rechecks_claimed_and_uncertain_items(self):
        ident=self.add()
        for phase in ('send_pending','running','needs_review','unknown'):
            self.store.change(TID,lambda q:q['items'][0].update(phase=phase))
            before=self.state()
            with self.assertRaises(ValueError):self.store.delete(TID,ident)
            self.assertEqual(self.state(),before)
    def test_manual_switch_in_progress_prevents_dispatch(self):
        self.add();atomic_json(self.root/'manual-switch.json',{'phase':'switching','updated_at':time.time()})
        self.dispatch();self.assertEqual(self.g.desktop.calls,[])
    def test_item_text_is_only_in_queue_file_not_guard_status_or_logs(self):
        self.add('private instruction');self.dispatch();self.g.report()
        self.assertNotIn('private instruction',(self.root/'status.json').read_text(encoding='utf-8'))
        self.assertNotIn('private instruction',(self.root/'events.jsonl').read_text(encoding='utf-8'))
    def test_missing_native_state_blocks(self):
        (self.root/'.codex-global-state.json').unlink()
        self.assertFalse(native_queue_clear(TID))
    def test_failed_queue_item_requires_manual_review_before_next(self):
        self.add('first');self.add('second');self.dispatch()
        self.g.desktop.thread.update(status='failed',runtime_status='idle');self.high();self.dispatch()
        self.assertFalse(self.state()['enabled'])
        self.assertEqual(self.state()['items'][0]['phase'],'needs_review')
        self.assertEqual(self.g.desktop.texts,['first'])
    def test_manual_stop_does_not_send_next_item(self):
        self.add('first');self.add('second');self.dispatch()
        self.g.desktop.thread.update(status='interrupted',runtime_status='idle');self.high();self.dispatch()
        self.assertFalse(self.state()['enabled']);self.assertEqual(self.g.desktop.texts,['first'])

if __name__=='__main__':unittest.main()
