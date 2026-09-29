import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import DEFAULTS,atomic_json,read_json,validate_settings
from runtime import discover_codex,QuotaReader
from desktop_lifecycle import desktop_version
from update_monitor import release_info,version_key,UpdateMonitor

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.start(patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':str(self.root)}))
    def start(self,p):
        value=p.start();self.addCleanup(p.stop);return value
    def executable(self,path,stamp):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'test-fixture-not-executable');os.utime(path,(stamp,stamp));return path
    def test_managed_cli_discovers_new_binary_despite_old_installation_record(self):
        base=self.root/'AppData/Local/OpenAI/Codex/bin'
        old=self.executable(base/'old/codex.exe',100);new=self.executable(base/'new/codex.exe',200)
        atomic_json(self.root/'installation.json',{'codex_exe':str(old)})
        with patch('runtime.Path.home',return_value=self.root),patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'AppData/Local')}): self.assertEqual(discover_codex(),str(new))
    def test_explicit_external_cli_and_disabled_adaptation_remain_pinned(self):
        base=self.root/'AppData/Local/OpenAI/Codex/bin'
        self.executable(base/'new/codex.exe',200)
        external=self.executable(self.root/'custom/codex.exe',100)
        atomic_json(self.root/'installation.json',{'codex_exe':str(external)})
        with patch('runtime.Path.home',return_value=self.root): self.assertEqual(discover_codex(),str(external))
        old=self.executable(base/'old/codex.exe',100)
        atomic_json(self.root/'installation.json',{'codex_exe':str(old)})
        atomic_json(self.root/'settings.json',dict(DEFAULTS,auto_adapt=False))
        with patch('runtime.Path.home',return_value=self.root): self.assertEqual(discover_codex(),str(old))
    def test_reader_reconnects_when_runtime_changes(self):
        old=self.executable(self.root/'old.exe',100);new=self.executable(self.root/'new.exe',200)
        first=MagicMock(closed=False);second=MagicMock(closed=False)
        with patch('runtime.codex_home',return_value=self.root),patch('runtime.discover_codex',side_effect=[str(old),str(new)]),patch('runtime.JsonProcess',side_effect=[first,second]) as spawn:
            reader=QuotaReader();reader._ensure();reader._ensure()
        first.close.assert_called_once();self.assertEqual(spawn.call_args.args[0][0],str(new))
    def test_new_desktop_version_requires_exact_package_identity(self):
        prefix=os.environ.get('ProgramFiles','C:/Program Files')+'/WindowsApps/'
        suffix='OpenAI.Codex_27.101.1234.0_x64__2p2nqsd0c76g0/app/ChatGPT.exe'
        self.assertEqual(desktop_version(prefix+suffix),'27.101.1234.0')
        self.assertIsNone(desktop_version('C:/Downloads/'+suffix))
        self.assertIsNone(desktop_version(prefix+suffix.replace('2p2nqsd0c76g0','unknown')))
        self.assertIsNone(desktop_version(prefix+suffix.replace('ChatGPT.exe','other.exe')))
    def test_pid_change_does_not_count_as_new_version(self):
        a={'cc_switch':[dict(version='3.20.1',path='cc.exe',pid=1)]}
        b={'cc_switch':[dict(version='3.20.1',path='cc.exe',pid=2)]}
        self.assertEqual(version_key(a),version_key(b))
        b['cc_switch'][0]['version']='3.20.2';self.assertNotEqual(version_key(a),version_key(b))
    def test_release_metadata_accepts_only_official_locations(self):
        payload={'tag_name':'v3.20.4','html_url':'https://github.com/farion1231/cc-switch/releases/tag/v3.20.4'}
        self.assertEqual(release_info('cc_switch',json.dumps(payload))['version'],'v3.20.4')
        payload['html_url']='https://example.com/adapter.exe'
        with self.assertRaises(ValueError): release_info('cc_switch',json.dumps(payload))
        rss=b'<rss><channel><item><title>Codex update</title><link>https://developers.openai.com/codex/changelog/#new</link></item></channel></rss>'
        self.assertEqual(release_info('codex',rss)['title'],'Codex update')
        with self.assertRaises(ValueError): release_info('codex',rss.replace(b'developers.openai.com',b'example.com'))
    def monitor(self):
        guard=SimpleNamespace(stop=threading.Event(),desktop=SimpleNamespace(connected=True,compatible=True,snapshots=lambda:{'chat':{}}))
        monitor=UpdateMonitor(guard)
        self.start(patch('update_monitor.plugin_enabled',return_value=True))
        self.local=self.start(patch('update_monitor.local_versions',return_value={'cli':{'version':'codex-cli 1.0','path':'test.exe'}}))
        self.start(patch('manual_switch.provider_inventory',return_value=({'name':'A'},[{'name':'A'}])))
        self.start(patch('manual_switch.ui_request',return_value={'version':'3.21.0','adapter':'semantic-ui-v1'}))
        self.fetch=self.start(patch('update_monitor.fetch_release',return_value={'title':'release','url':'https://example.com'}))
        return monitor
    def test_tracking_disable_makes_no_network_or_ui_requests(self):
        monitor=self.monitor();atomic_json(self.root/'settings.json',dict(DEFAULTS,update_tracking=False))
        monitor.maybe_check();self.local.assert_not_called();self.fetch.assert_not_called()
    def test_release_failures_keep_last_success_and_schedule_retry(self):
        monitor=self.monitor()
        atomic_json(monitor.path,{'releases':{'codex':{'title':'previous','checked_at':10}}})
        self.fetch.side_effect=OSError('offline');monitor.check()
        state=read_json(monitor.path);cached=state['releases']['codex']
        self.assertEqual(cached['title'],'previous');self.assertEqual(cached['checked_at'],10)
        self.assertTrue(cached['error']);self.assertGreater(cached['next_check'],time.time()+800)
        self.assertEqual(state['desktop_protocol'],'compatible')
    def test_release_checks_are_cached_but_manual_check_overrides(self):
        monitor=self.monitor();monitor.check();self.assertEqual(self.fetch.call_count,2)
        monitor.check();self.assertEqual(self.fetch.call_count,2)
        monitor.request_check();monitor.check();self.assertEqual(self.fetch.call_count,4)
    def test_incompatible_desktop_report_does_not_claim_readiness(self):
        monitor=self.monitor();monitor.guard.desktop.compatible=False;monitor.check()
        self.assertEqual(read_json(monitor.path)['desktop_protocol'],'incompatible')
    def test_settings_require_boolean_toggles(self):
        for key in ('auto_adapt','update_tracking'):
            with self.assertRaises(ValueError): validate_settings({key:'yes'})

if __name__=='__main__': unittest.main()
