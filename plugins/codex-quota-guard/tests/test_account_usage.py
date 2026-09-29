import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from account_usage import read_usage,cached_usage
from account_usage import reset_times,format_reset_time
from core import atomic_json
from manual_switch import remaining_from_card
from test_manual_switch import card


class DisplayUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        p=patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':self.temp.name});p.start();self.addCleanup(p.stop)
        self.providers=[dict(id='a',name='A',account='a'),dict(id='b',name='B',account='b')]
    def test_refreshes_both_windows_without_switch(self):
        with patch('manual_switch.ui_request',return_value=card(30,40)) as request:
            rows=read_usage(self.providers)
        self.assertEqual([call.args[0] for call in request.call_args_list],['refresh','refresh'])
        self.assertEqual(set(rows[0]['quota']),{'5h','week'})
    def test_stale_refresh_results_are_not_presented_as_current(self):
        snapshot=card();snapshot['texts']=[t.replace('刚刚','10分钟前') for t in snapshot['texts']]
        with self.assertRaises(ValueError):remaining_from_card(snapshot)
        with patch('manual_switch.ui_request',return_value=snapshot):row=read_usage(self.providers[:1])[0]
        self.assertIn('error',row);self.assertNotIn('quota',row)
    def test_active_plan_owns_ui_and_failed_read_is_not_zero_quota(self):
        atomic_json(Path(self.temp.name)/'reset-wait.json',{'phase':'waiting'})
        with patch('manual_switch.ui_request') as request:
            self.assertEqual(read_usage(self.providers),[]);request.assert_not_called()
        atomic_json(Path(self.temp.name)/'reset-wait.json',{'phase':'complete'})
        with patch('manual_switch.ui_request',side_effect=[RuntimeError('unavailable'),card()]):rows=read_usage(self.providers)
        self.assertNotIn('quota',rows[0]);self.assertIn('quota',rows[1])
    def test_wait_monitor_observations_are_displayed_as_cache(self):
        atomic_json(Path(self.temp.name)/'account-observations.json',{'accounts':[dict(id='a',quota={'5h':42,'week':70},checked_at=123)]})
        self.assertTrue(cached_usage()['a']['cached'])
    def test_manual_refresh_retries_transient_failure_without_switch(self):
        with patch('manual_switch.ui_request',side_effect=[RuntimeError('offline'),card()]) as request,patch('account_usage.time.sleep'):
            rows=read_usage(self.providers[:1],retry_errors=True)
        self.assertIn('quota',rows[0]);self.assertEqual([c.args[0] for c in request.call_args_list],['refresh','refresh'])
    def test_failed_retry_stays_error_and_continues_other_accounts(self):
        with patch('manual_switch.ui_request',side_effect=[RuntimeError('offline'),RuntimeError('offline'),card()]) as request,patch('account_usage.time.sleep'):
            rows=read_usage(self.providers,retry_errors=True)
        self.assertIn('error',rows[0]);self.assertNotIn('quota',rows[0]);self.assertIn('quota',rows[1]);self.assertEqual(request.call_count,3)
    def test_error_card_with_stale_percentages_cannot_be_accepted(self):
        with patch('manual_switch.ui_request',return_value=dict(card(),queryError=True)),patch('account_usage.time.sleep'):
            row=read_usage(self.providers[:1],retry_errors=True)[0]
        self.assertIn('error',row);self.assertNotIn('quota',row)
    def test_reset_estimates_use_each_windows_countdown(self):
        result=reset_times(card(),1000)
        self.assertEqual(result,{'5h':1000+2*3600+20*60,'week':1000+4*86400+3600})
        with patch('manual_switch.ui_request',return_value=card()):row=read_usage(self.providers[:1])[0]
        self.assertEqual(row['resets_at'],reset_times(card(),row['checked_at']))
        self.assertTrue(row['reset_estimated'])
    def test_missing_invalid_or_failed_reset_is_not_borrowed_from_week(self):
        snapshot=card();snapshot['texts'].remove('2h20m')
        self.assertNotIn('5h',reset_times(snapshot,1000))
        self.assertIn('week',reset_times(snapshot,1000))
        for value in ('6h0m','2h99m','unknown'):
            snapshot=card();snapshot['texts'][5]=value
            self.assertNotIn('5h',reset_times(snapshot,1000))
        self.assertEqual(reset_times(dict(card(),queryError=True)),{})
        self.assertEqual(reset_times(dict(card(),refreshing=True)),{})
        snapshot=card();snapshot['texts'][9]='25h10m'
        self.assertEqual(reset_times(snapshot,1000)['week'],1000+25*3600+10*60)
    def test_reset_display_handles_countdown_cache_and_unknown(self):
        from i18n import tr
        stamp=1900000000
        entry={'resets_at':{'5h':stamp},'cached':True}
        self.assertEqual(format_reset_time(entry,'5h',now=stamp-9000),'≈2.5 '+tr('小时'))
        self.assertEqual(format_reset_time(dict(entry,reset_estimated=False),'5h',now=stamp-3600),'1.0 '+tr('小时'))
        weekly={'resets_at':{'week':stamp}}
        self.assertEqual(format_reset_time(weekly,'week',now=stamp-216000),'≈2.5 '+tr('天'))
        self.assertEqual(format_reset_time(weekly,'week',now=stamp-172800),'≈2.0 '+tr('天'))
        self.assertEqual(format_reset_time(entry,'week'),'—')
        self.assertEqual(format_reset_time(dict(entry,error='failed'),'5h'),'—')
        for value in (None,True,0,float('nan'),float('inf'),'tomorrow'):
            self.assertEqual(format_reset_time({'resets_at':{'5h':value}},'5h'),'—')
        from i18n import tr
        self.assertEqual(format_reset_time(entry,'5h',now=stamp+1),tr('待刷新'))


if __name__=='__main__':unittest.main()
