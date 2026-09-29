import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import desktop_lifecycle as d
from service_host import launch_detached

class ShutdownProofTests(unittest.TestCase):
    def setUp(self):
        self.identity=dict(pid=123,created=999,exe='verified')
        p=patch('desktop_lifecycle.desktop_version',return_value='26.1.0.0');p.start();self.addCleanup(p.stop)
    def test_live_original_is_not_exit(self):
        with patch.object(d,'process_creation',return_value=999),patch.object(d.subprocess,'run') as run:
            self.assertFalse(d.confirmed_shutdown(self.identity));run.assert_not_called()
    def test_absent_original_and_package_confirm_exit(self):
        for stamp in (None,1001):
            with patch.object(d,'process_creation',return_value=stamp),patch.object(d,'desktop_instance',return_value=None),patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'absent\r\n')):
                self.assertTrue(d.confirmed_shutdown(self.identity))
    def test_new_instance_or_inaccessible_process_cannot_confirm_exit(self):
        with patch.object(d,'process_creation',return_value=None),patch.object(d,'desktop_instance',return_value={'pid':456}):
            self.assertFalse(d.confirmed_shutdown(self.identity))
        for outcome in ((0,b'running'),(1,b''),(0,b'')):
            with patch.object(d,'process_creation',return_value=None),patch.object(d,'desktop_instance',return_value=None),patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],outcome[0],outcome[1])):
                self.assertFalse(d.confirmed_shutdown(self.identity))
        self.assertFalse(d.confirmed_shutdown(None))

class ServiceHostTests(unittest.TestCase):
    def test_scheduler_launch_has_explicit_runtime_paths_and_no_desktop_actions(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':folder}),patch('service_host.subprocess.run',return_value=subprocess.CompletedProcess([],0,b'ok\r\n')) as run:
            launch_detached()
            values=json.loads(run.call_args.kwargs['env']['QUOTA_GUARD_SERVICE'])
            self.assertEqual(values['data'],folder)
            self.assertTrue(values['script'].endswith('guard.py'))
            import base64
            command=base64.b64decode(run.call_args.args[0][-1]).decode('utf-16-le')
            self.assertIn('daemon --independent',command)
            self.assertIn('$task.Principal.RunLevel=0',command)
            self.assertNotIn('Stop-Process',command)
    def test_failed_scheduler_does_not_fall_back_to_child_daemon(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'CODEX_QUOTA_GUARD_DATA':folder}),patch('service_host.subprocess.run',return_value=subprocess.CompletedProcess([],1,b'')):
            with self.assertRaises(RuntimeError):launch_detached()

if __name__=='__main__':unittest.main()
