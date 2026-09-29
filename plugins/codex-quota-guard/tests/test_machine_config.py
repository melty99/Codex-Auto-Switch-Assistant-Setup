"""Configuration isolation, path discovery, and language persistence."""
import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from core import atomic_json,read_json
import machine_config as config
import runtime
import i18n

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'data'
        self.env=patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':str(self.data),'CODEX_HOME':str(self.root/'codex'),'LOCALAPPDATA':str(self.root/'Local')});self.env.start();self.addCleanup(self.env.stop)
        self.home=self.root/'codex';self.home.mkdir()
        (self.home/'config.toml').write_text('[plugins."codex-quota-guard@community"]\nenabled=false\n',encoding='utf-8')
        self.cc=self.root/'cc';self.cc.mkdir();(self.cc/'cc-switch.db').write_bytes(b'fixture')
        atomic_json(self.cc/'settings.json',{'codexConfigDir':str(self.home)})
        self.values=dict(codex_home=str(self.home),cc_switch_dir=str(self.cc))
        for key,name in [('python_exe','python.exe'),('node_exe','node.exe'),('codex_exe','codex.exe'),('desktop_exe','ChatGPT.exe'),('cc_switch_exe','cc-switch.exe')]:
            path=self.root/name;path.write_bytes(b'not executable');self.values[key]=str(path)
        self.p1=patch('desktop_lifecycle.desktop_version',return_value='1.2.3.4');self.p1.start();self.addCleanup(self.p1.stop)
        self.p2=patch('machine_config.subprocess.run',return_value=SimpleNamespace(returncode=0));self.run=self.p2.start();self.addCleanup(self.p2.stop)
    def test_save_preserves_thresholds_and_plugin_disable(self):
        atomic_json(self.data/'settings.json',{'pause_5h':5,'auto_switch':False})
        before=(self.home/'config.toml').read_bytes()
        with patch('machine_config.install_startup') as startup,patch('runtime.ensure_service'):
            saved=config.save(self.values)
        self.assertEqual(saved['plugin_key'],'codex-quota-guard@community')
        self.assertFalse(runtime.plugin_enabled())
        self.assertEqual((self.home/'config.toml').read_bytes(),before)
        self.assertEqual(read_json(self.data/'settings.json'),{'pause_5h':5,'auto_switch':False})
        startup.assert_called_once()
        self.assertEqual(read_json(next((self.data/'commands').glob('*.json')))['action'],'reload_paths')
    def test_conflicting_homes_do_not_write_installation(self):
        atomic_json(self.cc/'settings.json',{'codexConfigDir':str(self.root/'other')})
        with self.assertRaisesRegex(ValueError,'不同'):config.save(self.values)
        self.assertFalse((self.data/'installation.json').exists())
    def test_active_recovery_plan_blocks_reconfiguration(self):
        atomic_json(self.data/'reset-wait.json',{'phase':'waiting'})
        with self.assertRaisesRegex(ValueError,'仍在进行'):config.save(self.values)
        self.assertFalse((self.data/'installation.json').exists())
    def test_startup_failure_restores_previous_installation(self):
        old={'codex_home':'existing'};atomic_json(self.data/'installation.json',old)
        with patch('machine_config.install_startup',side_effect=RuntimeError('startup unavailable')):
            with self.assertRaises(RuntimeError):config.save(self.values)
        self.assertEqual(read_json(self.data/'installation.json'),old)
    def test_missing_runtime_is_reported_before_execution(self):
        self.values['node_exe']=str(self.root/'missing.exe')
        with self.assertRaisesRegex(ValueError,'node_exe'):config.validate(self.values)
        self.run.assert_not_called()
    def test_discovery_uses_new_machine_and_existing_custom_home(self):
        exe=self.root/'Local/OpenAI/Codex/bin/new/codex.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'fixture')
        app=self.root/'package/app';app.mkdir(parents=True);(app/'ChatGPT.exe').write_bytes(b'fixture')
        atomic_json(self.data/'installation.json',{'codex_home':'Z:/old-user/.codex','cc_switch_dir':str(self.cc),'node_exe':self.values['node_exe']})
        with patch('machine_config.windows_inventory',return_value={'desktop':[{'path':str(app.parent)}],'cc_switch':[self.values['cc_switch_exe']]}):found=config.detect()
        self.assertEqual(found['codex_home'],str(self.home))
        self.assertEqual(found['codex_exe'],str(exe));self.assertEqual(found['plugin_key'],'codex-quota-guard@community')
        self.assertEqual(found['cc_switch_exe'],self.values['cc_switch_exe'])
    def test_english_status_formatting(self):
        with patch.object(i18n,'_language','en'):
            self.assertEqual(i18n.tr('待 3 项'),'3 queued')
            self.assertEqual(i18n.tr('账号已切换，正在直接重开 Codex。'),'Account switched; reopening Codex immediately.')
            self.assertEqual(i18n.tr('person@example.com'),'person@example.com')

if __name__=='__main__':unittest.main()
