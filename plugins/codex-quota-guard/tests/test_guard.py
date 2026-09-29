import pathlib, sys, tempfile, unittest, os, time
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from core import DEFAULTS,atomic_json
from guard import Guard

class FakeDesktop:
    connected=True; compatible=True
    def __init__(self):
        self.thread={'id':'a','turn_id':'t','status':'inProgress','runtime_status':'active','owner':'owner','model_provider':'openai','cwd':'C:/test'}
        self.calls=[]; self.lose_reply=False
    def snapshot(self,tid): return dict(self.thread)
    def snapshots(self): return {'a':dict(self.thread)}
    def action(self,op,t,**kwargs):
        self.calls.append(op)
        if op=='interrupt':
            self.thread.update(status='interrupted',runtime_status='idle'); return {'ok':True,'interruptedTurnId':'t'}
        if op=='resume':
            self.thread.update(status='inProgress',runtime_status='active',turn_id='next')
            if self.lose_reply: raise TimeoutError('reply lost')
            return {'result':{'turn':{'id':'next'}}}

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':self.temp.name}); self.env.start()
        self.gate=patch('guard.plugin_enabled',return_value=True); self.gate.start()
        self.g=Guard(); self.g.desktop=FakeDesktop(); self.g.account='test'
    def tearDown(self): self.gate.stop(); self.env.stop(); self.temp.cleanup()
    def high(self):
        self.g.quota={'checked_at':time.time(),'windows':{k:{'remaining':80,'resets_at':time.time()+1000} for k in ('5h','week')}}
        self.g.checked_high=2
    def test_pause_then_resume_exactly_once(self):
        self.g.pause_one(self.g.desktop.thread)
        r=self.g.journal.data['tasks']['a']; self.assertEqual(r['phase'],'paused'); r['paused_at']=time.time()-120
        self.high(); self.g.resume_one(r); self.g.resume_one(r)
        self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
        self.assertEqual(self.g.journal.data['tasks']['a']['phase'],'resumed')
    def test_lost_response_never_retried(self):
        self.g.pause_one(self.g.desktop.thread); r=self.g.journal.data['tasks']['a']; r['paused_at']=time.time()-120
        self.high(); self.g.desktop.lose_reply=True; self.g.resume_one(r)
        self.assertEqual(self.g.journal.data['tasks']['a']['phase'],'needs_review')
        self.g.tick_actions(); self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
    def test_plugin_disable_blocks_actions(self):
        with patch('guard.plugin_enabled',return_value=False): self.g.pause_one(self.g.desktop.thread)
        self.assertEqual(self.g.desktop.calls,[])
    def test_user_new_turn_cancels_auto_resume(self):
        self.g.pause_one(self.g.desktop.thread); self.g.desktop.thread['turn_id']='user-next'; self.high(); self.g.tick_actions()
        self.assertEqual(self.g.journal.data['tasks']['a']['phase'],'superseded')
        self.assertEqual(self.g.desktop.calls,['interrupt'])
    def test_one_good_sample_not_enough(self):
        self.g.pause_one(self.g.desktop.thread); r=self.g.journal.data['tasks']['a']; r['paused_at']=time.time()-120
        self.high(); self.g.checked_high=1; self.g.resume_one(r); self.assertEqual(self.g.desktop.calls,['interrupt'])
    def test_switch_account_resumes_using_new_account_not_old_balance(self):
        self.g.account='account-a'
        self.g.quota={'checked_at':time.time(),'windows':{k:{'remaining':0,'resets_at':time.time()+1000} for k in ('5h','week')}}
        self.g.tick_actions()
        r=self.g.journal.data['tasks']['a']; r['paused_at']=time.time()-120
        self.assertEqual(r['paused_account'],'account-a')
        self.g.checked_high=10  # Never reuse a previous account's good samples.
        result={'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':20,'resetsAt':time.time()+1000},
                              'secondary':{'windowDurationMins':10080,'usedPercent':99,'resetsAt':time.time()+1000}}}
        with patch.object(self.g.reader,'read',return_value=('account-b',result)):
            self.g.fetch_quota(); self.g.tick_actions()
            self.assertEqual(self.g.checked_high,1)
            self.assertEqual(self.g.desktop.calls,['interrupt'])
            self.g.fetch_quota(); self.g.tick_actions(); self.g.tick_actions()
        self.assertEqual(self.g.desktop.calls,['interrupt','resume'])
        self.assertEqual(self.g.journal.data['tasks']['a']['resumed_account'],'account-b')
    def test_switched_account_week_exhausted_blocks_resume(self):
        self.g.pause_one(self.g.desktop.thread)
        self.g.journal.data['tasks']['a']['paused_at']=time.time()-120
        result={'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':0,'resetsAt':time.time()+1000},
                              'secondary':{'windowDurationMins':10080,'usedPercent':100,'resetsAt':time.time()+1000}}}
        with patch.object(self.g.reader,'read',return_value=('account-b',result)):
            self.g.fetch_quota(); self.g.fetch_quota(); self.g.tick_actions()
        self.assertEqual(self.g.desktop.calls,['interrupt'])
        self.assertEqual(self.g.checked_high,0)
    def test_manual_switch_suppresses_auto_actions(self):
        atomic_json(self.g.directory/'manual-switch.json',{'phase':'switching','updated_at':time.time()})
        self.g.quota={'checked_at':time.time(),'windows':{k:{'remaining':0,'resets_at':time.time()+1000} for k in ('5h','week')}}
        self.g.tick_actions()
        self.assertEqual(self.g.desktop.calls,[])
    def test_check_command_discards_old_account_quota(self):
        self.high()
        atomic_json(self.g.directory/'commands/test.json',{'action':'check'})
        self.g.commands()
        self.assertIsNone(self.g.quota); self.assertEqual(self.g.checked_high,0)
    def test_auto_switch_after_confirmed_pause_once_per_event(self):
        settings=dict(DEFAULTS,auto_switch=True)
        atomic_json(self.g.directory/'settings.json',settings); self.g.settings=settings
        def low():
            self.g.quota={'checked_at':time.time(),'windows':{k:{'remaining':0,'resets_at':time.time()+1000} for k in ('5h','week')}}
        low()
        with patch('manual_switch.switch_to_available') as switch:
            self.g.tick_actions()
            self.assertEqual(self.g.desktop.calls,['interrupt'])
            switch.assert_called_once()
            self.assertTrue(switch.call_args.kwargs['automatic'])
            self.assertIsNone(self.g.quota)
            low(); self.g.tick_actions()
            switch.assert_called_once()
    def test_auto_switch_rechecks_disable_and_waiting_state(self):
        settings=dict(DEFAULTS,auto_switch=True)
        atomic_json(self.g.directory/'settings.json',settings); self.g.settings=settings
        self.g.pause_one(self.g.desktop.thread)
        self.g.quota={'checked_at':time.time(),'windows':{k:{'remaining':0,'resets_at':time.time()+1000} for k in ('5h','week')}}
        self.assertTrue(self.g.can_auto_switch())
        self.g.desktop.thread['waiting']=True; self.assertFalse(self.g.can_auto_switch())
        self.g.desktop.thread['waiting']=False
        atomic_json(self.g.directory/'settings.json',dict(settings,auto_switch=False))
        self.assertFalse(self.g.can_auto_switch())
    def test_query_completion_schedules_next_dynamic_interval(self):
        for remaining,interval in [(80,180),(19,60),(9,30)]:
            result={'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':100-remaining,'resetsAt':time.time()+1000},
                                  'secondary':{'windowDurationMins':10080,'usedPercent':10,'resetsAt':time.time()+1000}}}
            with patch.object(self.g.reader,'read',return_value=('a',result)):self.g.fetch_quota()
            self.assertAlmostEqual(self.g.next_poll-time.time(),interval,delta=1)
    def test_paused_tasks_use_short_recovery_verification_interval(self):
        self.g.pause_one(self.g.desktop.thread);self.high()
        self.assertEqual(self.g.poll_delay(),30)

if __name__=='__main__': unittest.main()
