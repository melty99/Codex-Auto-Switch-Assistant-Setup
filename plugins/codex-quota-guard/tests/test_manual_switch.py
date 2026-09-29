import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from manual_switch import remaining_from_card, select_candidate, switch_to_available
from core import read_json


def card(used=10, week=99, active=False):
    return dict(texts=['account','刚刚','5小时',':',f'{used}%','2h20m','7天',':',f'{week}%','4d1h'],
                active=active, canSwitch=not active, refreshing=False)


class SelectionTests(unittest.TestCase):
    def test_percentages_are_used_and_rounding_is_conservative(self):
        self.assertEqual(remaining_from_card(card(10,99)), {'5h':89.5,'week':.5})
        self.assertIsNone(select_candidate([dict(id='x',quota=remaining_from_card(card(80,0)))],20))
    def test_week_exhausted_or_stale_does_not_qualify(self):
        self.assertIsNone(select_candidate([dict(id='x',quota=remaining_from_card(card(10,100)))],20))
        stale=card(); stale['texts'][1]='1 小时前'
        with self.assertRaises(ValueError): remaining_from_card(stale)
        with self.assertRaises(ValueError): remaining_from_card(dict(card(),refreshing=True))
    def test_combined_quota_does_not_prefer_depleted_week(self):
        candidates=[dict(id='a',quota={'5h':70,'week':60}),dict(id='b',quota={'5h':80,'week':2}),dict(id='c',quota={'5h':80,'week':3})]
        self.assertEqual(select_candidate(candidates,20)['id'],'a')
    def test_priority_skips_low_quota_and_preserves_stable_account_ids(self):
        candidates=[dict(id='p1',account='a',name='Renamed A',quota={'5h':20,'week':80}),
                    dict(id='p2',account='b',name='B',quota={'5h':30,'week':1}),
                    dict(id='p3',account='c',name='C',quota={'5h':80,'week':80})]
        self.assertEqual(select_candidate(candidates,20,'priority',['a','b','c'])['account'],'b')
        self.assertEqual(select_candidate(candidates,20,'quota',['a','b','c'])['account'],'c')
        self.assertEqual(select_candidate(candidates,20,'priority',['deleted','c'])['account'],'c')
    def test_order_deduplicates_and_appends_new_accounts(self):
        from manual_switch import ordered_accounts
        providers=[dict(id='1',account='a',name='A'),dict(id='2',account='b',name='B'),dict(id='3',account='a',name='A copy')]
        self.assertEqual([x['account'] for x in ordered_accounts(providers,['b'])],['b','a'])
    def test_settings_validate_strategy_and_priority(self):
        from core import validate_settings
        for overrides in [{'selection_strategy':'bad'},{'account_priority':['a','a']},{'account_priority':'a'},{'auto_switch':1}]:
            with self.assertRaises(ValueError): validate_settings(overrides)
        self.assertFalse(validate_settings({})['auto_switch'])
    def test_unknown_window_is_not_guessed(self):
        snapshot=card(); snapshot['texts'][6]='30天'
        with self.assertRaises(ValueError): remaining_from_card(snapshot)


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':self.temp.name}); self.env.start()
        self.service=patch('runtime.ensure_service'); self.service.start()
        self.plugin=patch('manual_switch.plugin_enabled',return_value=True); self.plugin.start()
        self.a=dict(id='a',name='Account A',account='a')
        self.b=dict(id='b',name='Account B',account='b')
        self.providers=[self.a,self.b]
        self.calls=[]
    def tearDown(self):
        self.service.stop(); self.plugin.stop(); self.env.stop(); self.temp.cleanup()
    def ui(self,action,provider,**kwargs):
        self.calls.append((action,provider['id']))
        return card(active=action=='inspect' or action=='switch')
    def test_manual_selection_requests_restart_without_live_switch(self):
        inventory=[(self.a,self.providers),(self.a,self.providers),(self.b,self.providers)]
        with patch('manual_switch.provider_inventory',side_effect=inventory),patch('manual_switch.ui_request',side_effect=self.ui):
            result=switch_to_available()
        self.assertIn('退出后换号',result)
        self.assertEqual(self.calls,[('inspect','a'),('refresh','b'),('refresh','b')])
        state=read_json(Path(self.temp.name)/'manual-switch.json')
        self.assertEqual(state['phase'],'restart_requested')
        plan=read_json(Path(self.temp.name)/'reset-wait.json')
        self.assertEqual(plan['mode'],'restart');self.assertFalse(plan['automatic'])
        self.assertEqual(len(list((Path(self.temp.name)/'commands').glob('*.json'))),1)
    def test_new_manual_click_replaces_verified_pre_switch_review_only(self):
        import uuid
        from core import atomic_json
        prior=dict(id=uuid.uuid4().hex,phase='needs_review',review_phase='closing',active=self.a,target=self.b)
        atomic_json(Path(self.temp.name)/'reset-wait.json',prior)
        with patch('desktop_lifecycle.desktop_instance',return_value={'pid':123}),patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request',side_effect=self.ui):
            switch_to_available()
        new=read_json(Path(self.temp.name)/'reset-wait.json')
        self.assertEqual(new['phase'],'requested');self.assertNotEqual(new['id'],prior['id'])
        self.assertEqual(len(list(Path(self.temp.name).glob('reset-review-*.json'))),1)
        self.assertNotIn(('switch','b'),self.calls)
    def test_uncertain_activation_cannot_be_cleared_by_retry(self):
        import uuid
        from core import atomic_json
        prior=dict(id=uuid.uuid4().hex,phase='needs_review',review_phase='activating',active=self.a,target=self.b)
        atomic_json(Path(self.temp.name)/'reset-wait.json',prior)
        with patch('desktop_lifecycle.desktop_instance',return_value={'pid':123}),patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request') as ui:
            with self.assertRaises(RuntimeError):switch_to_available()
        self.assertEqual(read_json(Path(self.temp.name)/'reset-wait.json'),prior);ui.assert_not_called()
    def test_new_manual_click_after_external_account_change_replans_from_current(self):
        import uuid
        from core import atomic_json
        prior=dict(id=uuid.uuid4().hex,phase='needs_review',review_phase='closing',active=self.a,target=self.b)
        atomic_json(Path(self.temp.name)/'reset-wait.json',prior)
        with patch('desktop_lifecycle.desktop_instance',return_value={'pid':123}),patch('manual_switch.provider_inventory',return_value=(self.b,self.providers)),patch('manual_switch.ui_request',side_effect=self.ui):
            switch_to_available()
        new=read_json(Path(self.temp.name)/'reset-wait.json')
        self.assertEqual(new['active'],self.b);self.assertEqual(new['target'],self.a)
        self.assertNotEqual(new['id'],prior['id'])
        self.assertNotIn(('switch','a'),self.calls)
    def test_no_eligible_account_never_clicks_switch(self):
        def ui(action,p,**kw):
            self.calls.append(action); return card(100,1,active=action=='inspect')
        with patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request',side_effect=ui):
            self.assertIn('最早重置',switch_to_available())
        self.assertNotIn('switch',self.calls)
        self.assertEqual(read_json(Path(self.temp.name)/'reset-wait.json')['phase'],'requested')
    def test_other_provider_bound_to_current_account_is_excluded(self):
        providers=[self.a,dict(self.b,account='a')]
        with patch('manual_switch.provider_inventory',return_value=(self.a,providers)),patch('manual_switch.ui_request',side_effect=self.ui):
            switch_to_available()
        self.assertEqual(self.calls,[('inspect','a'),('refresh','a')])
    def test_current_account_changed_aborts(self):
        with patch('manual_switch.provider_inventory',side_effect=[(self.a,self.providers),(self.b,self.providers)]),patch('manual_switch.ui_request',side_effect=self.ui):
            with self.assertRaisesRegex(RuntimeError,'配置已发生变化'): switch_to_available()
        self.assertNotIn(('switch','b'),self.calls)
    def test_latest_quota_drops_below_threshold_aborts(self):
        replies=[card(active=True),card(),card(100)]
        with patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request',side_effect=replies) as ui:
            with self.assertRaisesRegex(RuntimeError,'最新额度'): switch_to_available()
        self.assertNotIn('switch',[call.args[0] for call in ui.call_args_list])
    def test_repeated_click_does_not_replay_pending_plan(self):
        with patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request',side_effect=self.ui):
            switch_to_available()
            with self.assertRaisesRegex(RuntimeError,'已有等待重置计划'):switch_to_available()
        self.assertEqual(self.calls,[('inspect','a'),('refresh','b'),('refresh','b')])
    def test_manual_switch_respects_management_disable_before_scanning(self):
        from core import DEFAULTS,atomic_json
        atomic_json(Path(self.temp.name)/'settings.json',dict(DEFAULTS,enabled=False))
        with patch('manual_switch.provider_inventory') as inventory:
            with self.assertRaisesRegex(RuntimeError,'未直接切换'):switch_to_available()
        inventory.assert_not_called()
    def test_automatic_disabled_does_not_read_or_switch_accounts(self):
        with patch('manual_switch.provider_inventory') as inventory:
            with self.assertRaisesRegex(RuntimeError,'自动切换已关闭'): switch_to_available(automatic=True)
        inventory.assert_not_called()
    def test_automatic_selection_queues_restart_without_live_switch(self):
        from core import DEFAULTS,atomic_json
        atomic_json(Path(self.temp.name)/'settings.json',dict(DEFAULTS,auto_switch=True))
        with patch('manual_switch.provider_inventory',return_value=(self.a,self.providers)),patch('manual_switch.ui_request',side_effect=self.ui):
            result=switch_to_available(automatic=True)
        self.assertIn('退出后换号',result)
        self.assertNotIn(('switch','b'),self.calls)
        plan=read_json(Path(self.temp.name)/'reset-wait.json')
        self.assertEqual(plan['mode'],'restart');self.assertFalse(plan['wait_reset'])


if __name__=='__main__': unittest.main()
