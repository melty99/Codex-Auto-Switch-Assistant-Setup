"""Simulated quotas and desktop lifecycle; never switch or close a real app."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import DEFAULTS,atomic_json,read_json
from guard import Guard
from reset_recovery import countdown_reset,earliest_reset
from desktop_lifecycle import wake_task_xml,open_chat
from test_guard import FakeDesktop
from test_manual_switch import card

THREAD='11111111-2222-4333-8444-555555555555'

class Desktop(FakeDesktop):
    def __init__(self):
        super().__init__();self.thread['id']=THREAD
    def snapshots(self): return {THREAD:dict(self.thread)}


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.start(patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':self.temp.name}))
        self.now=time.time()
        self.start(patch('reset_recovery.time.time',side_effect=lambda:self.now))
        self.start(patch('guard.plugin_enabled',return_value=True))
        self.start(patch('reset_recovery.plugin_enabled',return_value=True))
        self.start(patch('runtime.ensure_service'))
        self.remove=self.start(patch('desktop_lifecycle.remove_wake',return_value=True))
        self.register=self.start(patch('desktop_lifecycle.register_wake'))
        self.quit=self.start(patch('desktop_lifecycle.quit_desktop'))
        self.start(patch('desktop_lifecycle.shutdown_identity',return_value={'pid':123,'created':99,'exe':'test-desktop.exe'}))
        self.open=self.start(patch('desktop_lifecycle.open_chat'))
        self.launch=self.start(patch('desktop_lifecycle.open_desktop',return_value=456))
        self.instance=self.start(patch('desktop_lifecycle.desktop_instance',return_value={'pid':123,'exe':'test-desktop.exe'}))
        self.g=Guard();self.g.desktop=Desktop();self.g.account='fingerprint-a'
        self.r=self.g.recovery
        self.a=dict(id='a',account='a',name='Account A')
        self.b=dict(id='b',account='b',name='Account B')
        self.active=self.a;self.providers=[self.a,self.b]
        self.inventory=self.start(patch('manual_switch.provider_inventory',side_effect=lambda:(self.active,self.providers)))
        self.cards={'a':card(100,10,True),'b':card(100,10)}
        self.ui=self.start(patch('manual_switch.ui_request',side_effect=self.ui_action))
        self.exact=self.quota(0)
        self.query=self.start(patch.object(self.r,'query',side_effect=lambda:('fingerprint-'+self.active['account'],self.exact)))
        self.g.pause_one(self.g.desktop.thread)
        self.plan=dict(version=1,id=uuid.uuid4().hex,phase='waiting',automatic=False,
                       active=self.a,target=self.a,account='fingerprint-a',created_at=self.now,
                       resets_at=self.now+3600,wake_at=self.now+3900,next_check=0,
                       reset_estimates={'a':self.now+3600,'b':self.now+4000},
                       threads={THREAD:'t'},good_samples=0,wake_task='CodexQuotaGuard-Reset-'+str(uuid.uuid4()))
        self.persist()
    def start(self,p):
        obj=p.start();self.addCleanup(p.stop);return obj
    def quota(self,five,week=90):
        return dict(checked_at=self.now,windows={k:dict(remaining=v,resets_at=self.now+20000)
                    for k,v in [('5h',five),('week',week)]})
    def persist(self): atomic_json(self.r.path,self.plan)
    def ui_action(self,action,provider,**kwargs):
        if action=='switch': self.active=next(p for p in self.providers if p['account']==provider['account'])
        return self.cards[provider['account']]
    def poll(self):
        self.plan['next_check']=0;return self.r.monitor_wait(self.plan)
    def state(self): return read_json(self.r.path)
    def test_earliest_reset_excludes_week_exhausted_and_invalid_time(self):
        candidates=[dict(id='a',quota={'week':0},reset_estimate=self.now+10),
                    dict(id='b',quota={'week':10},reset_estimate=self.now+200),
                    dict(id='c',quota={'week':50},reset_estimate=self.now+100),
                    dict(id='d',quota={'week':90},reset_estimate=float('nan'))]
        self.assertEqual(earliest_reset(candidates)['id'],'c')
        self.assertEqual(countdown_reset(card(),now=0),8400)
    def test_early_reset_requires_two_new_samples(self):
        self.exact=self.quota(80)
        self.assertFalse(self.poll());self.assertTrue(self.poll())
        self.assertTrue(self.plan['early_recovery'])
        self.assertEqual([c.args[0] for c in self.ui.call_args_list],['refresh','refresh'])
    def test_poll_interval_prevents_repeated_requests(self):
        self.poll();self.r.monitor_wait(self.plan)
        self.query.assert_called_once()
    def test_restart_toggle_keeps_monitoring_and_rechecks_after_enable(self):
        self.exact=self.quota(80)
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=False))
        for _ in range(3):self.assertFalse(self.poll())
        self.assertEqual(self.query.call_count,3)
        self.assertEqual(self.state()['phase'],'waiting')
        self.assertEqual(self.plan['good_samples'],0)
        self.assertEqual(self.g.journal.records()[THREAD]['phase'],'paused')
        self.assertFalse((self.g.directory/'reset-cancel.json').exists())
        self.launch.assert_not_called()
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=True))
        self.assertFalse(self.poll());self.assertTrue(self.poll())
    def test_restart_toggle_blocks_other_account_activation(self):
        self.instance.return_value=None;self.cards['b']=card(5,10)
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=False))
        self.poll();self.poll()
        self.assertEqual(self.active,self.a)
        self.assertFalse(any(c.args[0]=='switch' for c in self.ui.call_args_list))
    def test_toggle_during_opening_holds_plan_without_timeout_or_cancellation(self):
        self.plan.update(phase='opening',mode='restart',wait_reset=True,opening_at=self.now,opened=[],last_open=0)
        self.persist();atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=False))
        self.r.tick();self.now+=1000;self.r.tick()
        self.launch.assert_not_called();self.assertEqual(self.state()['phase'],'opening')
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=True))
        self.r.tick();self.launch.assert_called_once()
        self.assertEqual(self.state()['opening_at'],self.now)
    def test_wait_toggle_does_not_block_normal_switch_or_revive_cancelled_plan(self):
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=False))
        self.assertTrue(self.r.resume_enabled({'mode':'restart','wait_reset':False}))
        self.r.cancel(self.plan,'User cancelled')
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_auto_resume=True))
        self.assertFalse(self.r.tick());self.assertEqual(self.state()['phase'],'cancelled')
    def test_natural_reset_obeys_five_minute_buffer(self):
        self.plan['resets_at']=self.now-299;self.plan['wake_at']=self.now+1
        self.exact=self.quota(80)
        self.assertFalse(self.poll());self.assertFalse(self.poll())
        self.now+=1
        self.assertFalse(self.poll());self.assertTrue(self.poll())
        self.assertFalse(self.plan['early_recovery'])
    def test_failed_sample_breaks_confirmation_streak(self):
        self.exact=self.quota(80);self.assertFalse(self.poll())
        self.query.side_effect=RuntimeError('temporary network failure')
        self.assertFalse(self.poll());self.assertEqual(self.plan['good_samples'],0)
        self.query.side_effect=lambda:('fingerprint-a',self.exact)
        self.assertFalse(self.poll());self.assertTrue(self.poll())
    def test_week_exhausted_and_exact_threshold_block_recovery(self):
        for five,week in [(80,0),(10,90)]:
            self.exact=self.quota(five,week)
            self.assertFalse(self.poll());self.assertFalse(self.poll())
        self.open.assert_not_called()
    def test_other_account_early_reset_is_selected_and_verified(self):
        self.instance.return_value=None
        self.cards['b']=card(5,10)
        self.query.side_effect=lambda:('fingerprint-'+self.active['account'],self.quota(95 if self.active==self.b else 0))
        self.assertFalse(self.poll());self.assertTrue(self.poll())
        self.assertEqual(self.active,self.b)
        self.assertEqual([c.args[0] for c in self.ui.call_args_list].count('switch'),1)
        self.assertEqual(self.plan['target']['account'],'b')
        self.assertEqual(self.query.call_count,2)  # Both checks preceded activation.
    def test_restored_quota_uses_saved_priority(self):
        self.instance.return_value=None
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,selection_strategy='priority',account_priority=['b','a']))
        self.exact=self.quota(99);self.cards['b']=card(50,10)
        self.assertFalse(self.poll());self.assertTrue(self.poll())
        self.assertEqual(self.active,self.b)
    def test_identity_change_cancels_without_opening(self):
        self.query.side_effect=lambda:('different-person',self.quota(90))
        self.assertFalse(self.poll())
        self.assertEqual(self.state()['phase'],'cancelled')
        self.assertEqual(self.g.journal.records()[THREAD]['phase'],'cancelled')
        self.open.assert_not_called()
    def test_service_stop_preserves_plan_and_records(self):
        self.g.stop.set();self.assertTrue(self.r.tick())
        self.assertEqual(self.state()['phase'],'waiting')
        self.assertEqual(self.g.journal.records()[THREAD]['phase'],'paused')
        self.remove.assert_not_called()
    def test_disabling_wait_cancels_task_and_watchdog(self):
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_for_reset=False))
        self.assertFalse(self.r.tick())
        self.assertEqual(self.state()['phase'],'cancelled')
        self.remove.assert_called_once()
    def test_cancel_during_query_prevents_further_actions(self):
        def query():
            atomic_json(self.g.directory/'reset-cancel.json',{'id':self.plan['id']})
            return 'fingerprint-a',self.quota(90)
        self.query.side_effect=query
        self.assertTrue(self.r.tick())
        self.assertEqual(self.state()['phase'],'cancelled')
        self.open.assert_not_called();self.quit.assert_not_called()
    def test_expired_queued_request_does_not_pause_or_switch(self):
        self.plan.update(phase='requested',created_at=self.now-301,threads={});self.persist()
        self.assertFalse(self.r.tick())
        self.assertEqual(self.state()['phase'],'cancelled')
        self.ui.assert_not_called();self.quit.assert_not_called()
    def test_prepare_persists_tasks_and_scheduler_before_quitting(self):
        self.plan.update(phase='requested',target=self.b);self.persist()
        self.exact=self.quota(0)
        self.exact['windows']['5h']['resets_at']=self.now+1000
        def quit_app(instance):
            saved=self.state()
            self.assertEqual(saved['phase'],'closing')
            self.assertEqual(saved['threads'],{THREAD:'t'})
            self.assertTrue(saved['wake_task'])
            self.register.assert_called_once()
            self.assertEqual(self.active,self.a)
            self.instance.return_value=None
        self.quit.side_effect=quit_app
        self.assertTrue(self.r.tick())
        self.assertEqual(self.state()['phase'],'waiting')
        self.assertEqual(self.state()['wake_at'],self.now+1300)
        self.quit.assert_called_once()
    def test_failed_scheduler_never_quits(self):
        self.plan.update(phase='requested');self.persist()
        self.exact['windows']['5h']['resets_at']=self.now+1000
        self.register.side_effect=RuntimeError('scheduler failed')
        self.assertTrue(self.r.tick())
        self.assertEqual(self.state()['phase'],'needs_review');self.quit.assert_not_called()
    def test_unconfirmed_quit_never_repeats(self):
        self.plan.update(phase='requested');self.persist()
        self.exact['windows']['5h']['resets_at']=self.now+1000
        self.quit.side_effect=RuntimeError('exit unconfirmed')
        self.r.tick();self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review');self.quit.assert_called_once()
    def test_restart_after_confirmed_exit_returns_to_monitoring(self):
        self.plan.update(phase='closing');self.persist();self.instance.return_value=None
        self.r.tick()
        self.assertEqual(self.state()['phase'],'waiting');self.quit.assert_not_called()
    def test_restart_closing_uses_process_proof_without_second_quit(self):
        self.restart_plan();self.plan.update(phase='closing',shutdown_identity={'pid':123,'created':99})
        self.persist();self.instance.return_value=None
        with patch('desktop_lifecycle.confirmed_shutdown',return_value=True) as proof:
            self.r.tick()
        proof.assert_called_once_with(self.plan['shutdown_identity'])
        self.quit.assert_not_called();self.launch.assert_called_once()
        self.assertEqual(self.active,self.b)
    def test_restart_closing_without_proof_never_switches(self):
        self.restart_plan();self.plan.update(phase='closing');self.persist()
        with patch('desktop_lifecycle.confirmed_shutdown',return_value=False):self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.assertEqual(self.state()['review_phase'],'closing')
        self.ui.assert_not_called();self.launch.assert_not_called();self.quit.assert_not_called()
    def test_open_original_chat_once_then_existing_resume_controller(self):
        self.plan.update(phase='opening',opened=[],opening_at=self.now,last_open=0,desktop_open_sent=True);self.persist()
        self.assertTrue(self.r.tick());self.assertFalse(self.r.tick())
        self.open.assert_called_once_with(THREAD)
        self.assertEqual(self.state()['phase'],'recovering')
        self.assertEqual(self.g.desktop.calls,['interrupt'])
        self.g.quota=self.quota(90);self.g.checked_high=2
        self.g.journal.data['tasks'][THREAD]['paused_at']=self.now-120
        self.g.tick_actions();self.g.tick_actions()
        self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
        self.assertEqual(self.state()['phase'],'complete')
    def test_user_new_turn_is_never_resumed(self):
        self.g.desktop.thread['turn_id']='user-new'
        self.assertFalse(self.r.tick())
        self.assertEqual(self.g.journal.records()[THREAD]['phase'],'superseded')
        self.open.assert_not_called()
    def test_terminal_plan_retries_watchdog_cleanup(self):
        self.plan['phase']='complete';self.persist();self.r.tick();self.r.tick()
        self.remove.assert_called_once()
        self.assertIsNone(self.state()['wake_task'])
    def test_xml_watchdog_repeats_as_current_user_without_admin(self):
        tree=ET.fromstring(wake_task_xml(self.now+3600));ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        def text(path): return tree.find(path,ns).text
        self.assertEqual(text('.//t:Interval'),'PT1M')
        self.assertEqual(text('.//t:LogonType'),'InteractiveToken')
        self.assertEqual(text('.//t:RunLevel'),'LeastPrivilege')
        self.assertTrue(text('.//t:Arguments').endswith('guard.py" start'))
    def test_protocol_launcher_validates_chat_id(self):
        with patch('desktop_lifecycle.os.startfile') as launch:
            open_chat(THREAD)
            launch.assert_called_once_with('codex://threads/'+THREAD)
            with self.assertRaises(ValueError): open_chat('bad/../../other')
            launch.assert_called_once()

    def restart_plan(self,wait=False):
        self.plan.update(phase='requested',mode='restart',wait_reset=wait,target=self.b)
        self.persist();self.cards['b']=card(100 if wait else 10,10)
        self.exact=self.quota(0 if wait else 80)
        self.exact['windows']['5h']['resets_at']=self.now+1000
        self.order=[]
        def quit_app(instance):
            self.assertEqual(self.active,self.a)
            self.assertEqual(self.state()['phase'],'closing')
            self.assertTrue(self.state()['threads']);self.register.assert_called_once()
            self.order.append('quit');self.instance.return_value=None
        def ui(action,provider,**kwargs):
            if action=='switch':
                self.assertIsNone(self.instance.return_value)
                self.order.append('switch')
            return self.ui_action(action,provider,**kwargs)
        self.quit.side_effect=quit_app;self.ui.side_effect=ui
    def test_restart_quits_before_switch_then_reopens_after_confirmation(self):
        self.restart_plan();self.assertTrue(self.r.tick())
        self.assertEqual(self.order,['quit','switch']);self.assertEqual(self.state()['phase'],'opening')
        self.open.assert_not_called()
        self.launch.assert_called_once_with('test-desktop.exe');self.query.assert_not_called()
        self.instance.return_value={'pid':456};self.r.tick()
        self.open.assert_called_once_with(THREAD)
        self.assertEqual(self.state()['phase'],'opening')
    def test_restart_does_not_require_wait_toggle_if_target_ready(self):
        self.restart_plan();atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,wait_for_reset=False))
        self.r.tick();self.assertEqual(self.order,['quit','switch'])
        self.assertEqual(self.state()['phase'],'opening')
    def test_restart_all_low_obeys_reset_plus_five_minutes(self):
        self.restart_plan(wait=True);self.r.tick()
        self.assertEqual(self.order,['quit','switch'])
        self.assertEqual(self.state()['wake_at'],self.now+1300)
        self.now+=1001;self.exact=self.quota(80);self.r.tick()
        self.open.assert_not_called()
        self.now+=300;self.exact=self.quota(80);self.r.tick()
        self.open.assert_not_called()
        self.now+=31;self.exact=self.quota(80);self.r.tick()
        self.launch.assert_called_once()
        self.instance.return_value={'pid':456};self.r.tick()
        self.open.assert_called_once()
    def test_restart_failed_quit_never_switches(self):
        self.restart_plan();self.quit.side_effect=RuntimeError('not closed')
        self.r.tick();self.r.tick()
        self.assertEqual(self.active,self.a);self.assertEqual(self.state()['phase'],'needs_review')
        self.quit.assert_called_once()
    def test_automatic_restart_uses_confirmed_exit_before_account_activation(self):
        self.restart_plan();self.plan['automatic']=True;self.persist()
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,auto_switch=True))
        self.r.tick()
        self.assertEqual(self.order,['quit','switch'])
        self.launch.assert_called_once();self.query.assert_not_called()
        self.instance.return_value={'pid':456};self.r.tick()
        self.open.assert_called_once_with(THREAD)
    def test_automatic_menu_failure_never_switches_or_retries_exit(self):
        self.restart_plan();self.plan['automatic']=True;self.persist()
        atomic_json(self.g.directory/'settings.json',dict(DEFAULTS,auto_switch=True))
        self.quit.side_effect=RuntimeError('quit_menu_unavailable')
        self.r.tick();self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.assertEqual(self.active,self.a)
        self.quit.assert_called_once();self.open.assert_not_called()
        self.assertFalse(any(c.args[0]=='switch' for c in self.ui.call_args_list))
    def test_restart_candidate_drops_before_exit_does_not_close(self):
        self.restart_plan();self.cards['b']=card(100,10);self.r.tick()
        self.quit.assert_not_called();self.assertEqual(self.active,self.a)
    def test_restart_detects_user_new_task_before_exit(self):
        self.restart_plan()
        def register(*args):self.g.desktop.thread.update(status='inProgress',runtime_status='active',turn_id='manual')
        self.register.side_effect=register;self.r.tick()
        self.quit.assert_not_called();self.assertEqual(self.active,self.a)
    def test_restart_crash_in_activation_never_repeats_click(self):
        self.restart_plan();self.plan['phase']='activating';self.persist();self.r.tick();self.r.tick()
        self.ui.assert_not_called();self.assertEqual(self.state()['phase'],'needs_review')
    def test_restart_closed_phase_can_continue_without_quitting_twice(self):
        self.restart_plan();self.plan['phase']='closed';self.persist();self.instance.return_value=None
        self.r.tick();self.quit.assert_not_called();self.assertEqual(self.order,['switch'])
    def test_restart_cancelled_records_do_not_switch_after_close(self):
        self.restart_plan();self.plan['phase']='closed';self.persist();self.instance.return_value=None
        self.g.journal.mark(THREAD,'cancelled');self.r.tick()
        self.ui.assert_not_called();self.assertEqual(self.state()['phase'],'complete')
    def test_manual_idle_restart_opens_app_without_resuming_old_tasks(self):
        self.restart_plan();self.g.journal.mark(THREAD,'resumed')
        self.g.desktop.thread.update(status='completed',runtime_status='idle')
        self.instance.return_value={'pid':123,'exe':'test-desktop.exe'}
        def quit_idle(instance):
            self.assertEqual(self.active,self.a)
            self.assertEqual(self.state()['phase'],'closing')
            self.assertTrue(self.state()['reopen_only'])
            self.assertEqual(self.state()['threads'],{})
            self.register.assert_called_once()
            self.order.append('quit');self.instance.return_value=None
        self.quit.side_effect=quit_idle
        self.r.tick()
        self.assertEqual(self.order,['quit','switch']);self.assertTrue(self.state()['reopen_only'])
        self.launch.assert_called_once_with('test-desktop.exe');self.query.assert_not_called()
        self.instance.return_value={'pid':456,'exe':'test-desktop.exe'}
        self.r.tick();self.assertEqual(self.state()['phase'],'complete')
        self.r.tick();self.launch.assert_called_once()
        self.open.assert_not_called();self.assertNotIn('resume',self.g.desktop.calls)
    def test_reopened_desktop_blocks_waiting_account_switch(self):
        self.cards['b']=card(5,10)
        self.assertFalse(self.poll())
        self.now+=31;self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.assertFalse(any(c.args[0]=='switch' for c in self.ui.call_args_list))
        self.assertEqual(self.active,self.a)
        self.open.assert_not_called()
    def test_launcher_failure_is_not_retried_or_reported_complete(self):
        self.restart_plan();self.launch.side_effect=RuntimeError('Windows launch failed')
        self.r.tick();self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.launch.assert_called_once();self.open.assert_not_called();self.query.assert_not_called()
    def test_different_desktop_process_does_not_confirm_relaunch(self):
        self.restart_plan();self.r.tick()
        self.instance.return_value={'pid':999}
        self.r.tick();self.open.assert_not_called()
        self.now+=91;self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.launch.assert_called_once();self.open.assert_not_called()
    def test_manual_restart_uncertain_activation_never_retries(self):
        self.restart_plan();base=self.ui.side_effect
        def fail(action,provider,**kwargs):
            if action=='switch':raise RuntimeError('uncertain activation')
            return base(action,provider,**kwargs)
        self.ui.side_effect=fail;self.r.tick();self.r.tick()
        self.assertEqual(self.state()['phase'],'needs_review')
        self.assertEqual(sum(c.args[0]=='switch' for c in self.ui.call_args_list),1)
        self.quit.assert_called_once();self.open.assert_not_called()
    def test_manual_full_restart_observes_original_chat_before_resume(self):
        self.restart_plan();self.r.tick()
        self.instance.return_value={'pid':456};self.r.tick()
        self.assertEqual(self.state()['phase'],'opening')
        self.r.tick();self.assertEqual(self.state()['phase'],'recovering')
        self.g.quota=self.quota(80);self.g.checked_high=2
        self.g.journal.data['tasks'][THREAD]['paused_at']=self.now-120
        self.g.tick_actions();self.g.tick_actions()
        self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
        self.assertEqual(self.state()['phase'],'complete')

if __name__=='__main__': unittest.main()
