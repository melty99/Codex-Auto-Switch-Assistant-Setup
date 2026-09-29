"""Exercise quit acknowledgments with fake Win32/process calls; no real exit."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import desktop_lifecycle as d

class QuitTests(unittest.TestCase):
    def setUp(self):
        self.instance={'pid':123,'hwnd':456,'exe':'verified-desktop.exe'}
        self.kernel=MagicMock();self.kernel.OpenProcess.return_value=77
        self.kernel.WaitForSingleObject.return_value=0
        self.patches=[patch.object(d,'desktop_instance',return_value=self.instance),
                      patch.object(d.ctypes.windll,'kernel32',self.kernel),
                      patch.object(d.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'ok\r\n'))]
        self.current,self.win,self.run=[p.start() for p in self.patches]
        for p in self.patches:self.addCleanup(p.stop)
    def test_quit_waits_for_actual_process_exit(self):
        self.kernel.WaitForSingleObject.side_effect=[258,0]
        with patch.object(d.time,'sleep') as sleep:d.quit_desktop(self.instance)
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.kwargs['env']['QUOTA_GUARD_PROBE'],'0')
        self.assertEqual(self.kernel.WaitForSingleObject.call_count,2)
        sleep.assert_called_once();self.kernel.CloseHandle.assert_called_once_with(77)
    def test_probe_never_waits_or_invokes_quit(self):
        d.quit_desktop(self.instance,probe=True)
        self.assertEqual(self.run.call_args.kwargs['env']['QUOTA_GUARD_PROBE'],'1')
        self.kernel.WaitForSingleObject.assert_not_called()
    def test_process_identity_change_never_opens_menu(self):
        self.current.return_value=dict(self.instance,pid=789)
        with self.assertRaisesRegex(RuntimeError,'实例已改变'):d.quit_desktop(self.instance)
        self.run.assert_not_called();self.kernel.OpenProcess.assert_not_called()
    def test_uncertain_invoke_is_not_retried(self):
        self.run.return_value=subprocess.CompletedProcess([],1,b'quit_invoke_uncertain\r\n')
        with self.assertRaisesRegex(RuntimeError,'执行结果不明确'):d.quit_desktop(self.instance)
        self.run.assert_called_once();self.kernel.WaitForSingleObject.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)
    def test_misleading_stdout_does_not_confirm_exit(self):
        self.run.return_value=subprocess.CompletedProcess([],0,b'not ok')
        with self.assertRaises(RuntimeError):d.quit_desktop(self.instance)
        self.kernel.WaitForSingleObject.assert_not_called()
    def test_missing_menu_reports_actionable_reason(self):
        self.run.return_value=subprocess.CompletedProcess([],1,b'file_menu_unavailable')
        with self.assertRaisesRegex(RuntimeError,'请打开主窗口'):d.quit_desktop(self.instance)
        self.kernel.CloseHandle.assert_called_once_with(77)
    def test_menu_timeout_never_retries_and_closes_monitor_handle(self):
        self.run.side_effect=subprocess.TimeoutExpired('menu',25)
        with self.assertRaisesRegex(RuntimeError,'操作超时'):d.quit_desktop(self.instance)
        self.run.assert_called_once();self.kernel.WaitForSingleObject.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)

if __name__=='__main__':unittest.main()
