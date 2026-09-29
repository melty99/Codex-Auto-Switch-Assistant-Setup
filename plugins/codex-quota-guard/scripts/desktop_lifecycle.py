"""Graceful quit through the verified app's File menu; reopen local chats."""
import base64
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
import datetime
import tempfile
import xml.etree.ElementTree as ET
from runtime import hidden_flags,load_settings


def desktop_version(path):
    # Require the installed Microsoft Store package identity, including publisher.
    prefix=str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'WindowsApps').replace('/','\\').rstrip('\\')+'\\'
    value=str(path).replace('/','\\')
    if not value.lower().startswith(prefix.lower()): return None
    match=re.fullmatch(r'OpenAI\.Codex_(\d+\.\d+\.\d+\.\d+)_(?:x64|arm64)__2p2nqsd0c76g0\\app\\(?:ChatGPT|Codex)\.exe',value[len(prefix):],re.I)
    return match[1] if match else None


def desktop_instance():
    user=ctypes.windll.user32; kernel=ctypes.windll.kernel32
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; kernel.OpenProcess.restype=w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes=[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    callback=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
    found=[]
    @callback
    def visit(hwnd,_):
        cls=ctypes.create_unicode_buffer(256); user.GetClassNameW(hwnd,cls,256)
        if cls.value not in ('Electron_NotifyIconHostWindow','OwlElectron_NotifyIconHostWindow'): return True
        pid=w.DWORD(); user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        handle=kernel.OpenProcess(0x1000,False,pid.value)
        if not handle: return True
        try:
            path=ctypes.create_unicode_buffer(32768); size=w.DWORD(len(path))
            if not kernel.QueryFullProcessImageNameW(handle,0,path,ctypes.byref(size)): return True
            version=desktop_version(path.value)
            if version and (load_settings()['auto_adapt'] or version=='26.924.2738.0'):
                found.append(dict(pid=pid.value,hwnd=int(hwnd),exe=path.value))
        finally: kernel.CloseHandle(handle)
        return True
    user.EnumWindows(visit,0)
    if len(found)>1: raise RuntimeError('发现多个 Codex 桌面实例，不能确定退出目标。')
    return found[0] if found else None


def quit_desktop(instance, probe=False):
    current=desktop_instance()
    if current!=instance: raise RuntimeError('Codex 桌面实例已改变，未请求退出。')
    script=(Path(__file__).with_name('window_access.ps1').read_text(encoding='utf-8')+'\n'+
            Path(__file__).with_name('quit-desktop.ps1').read_text(encoding='utf-8'))
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD];kernel.CloseHandle.argtypes=[w.HANDLE]
    process_handle=kernel.OpenProcess(0x100000,False,instance['pid'])
    if not process_handle: raise RuntimeError('无法监测 Codex 退出结果，未请求退出。')
    try:
        try:
            result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
              env=dict(os.environ,QUOTA_GUARD_DESKTOP_PID=str(instance['pid']),QUOTA_GUARD_PROBE='1' if probe else '0'),
              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=25,creationflags=hidden_flags())
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Codex 退出菜单操作超时，结果未确认；未重复退出或切换账号。') from exc
        outcome=result.stdout.decode('utf-8',errors='replace').strip()
        if result.returncode or outcome!='ok':
            reason={'file_menu_unavailable':'未找到可操作的 Codex 文件菜单，请打开主窗口',
                    'quit_menu_ambiguous':'发现多个退出菜单项',
                    'quit_menu_unavailable':'文件菜单中没有可确认的退出项',
                    'quit_invoke_uncertain':'退出命令的执行结果不明确，未重复请求',
                    'menu_access_failed':'菜单访问接口不可用'}.get(outcome,'退出菜单检查失败')
            raise RuntimeError(reason+'；未切换账号，未强制结束进程。')
        if probe: return
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            if kernel.WaitForSingleObject(process_handle,0)==0: return
            time.sleep(.3)
        raise RuntimeError('Codex 未确认退出，可能等待退出确认；请检查，程序不会强制结束。')
    finally:
        kernel.CloseHandle(process_handle)


def process_creation(pid):
    """A process's creation FILETIME distinguishes it from a reused PID."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
    kernel.GetProcessTimes.argtypes=[w.HANDLE,*([ctypes.POINTER(w.FILETIME)]*4)]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return None
        raise RuntimeError('无法核验原 Codex 进程状态，未切换。')
    try:
        values=[w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*(ctypes.byref(v) for v in values)):
            raise RuntimeError('无法读取 Codex 进程创建时间，未切换。')
        return (values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
    finally:kernel.CloseHandle(handle)


def shutdown_identity(instance):
    created=process_creation(instance['pid'])
    if created is None:raise RuntimeError('退出前 Codex 进程已改变。')
    return dict(pid=instance['pid'],created=created,exe=instance['exe'])


def confirmed_shutdown(identity):
    """Verify process exit after a guard restart, without sending another quit."""
    if not identity or not isinstance(identity.get('pid'),int) or not identity.get('created') or not desktop_version(identity.get('exe','')):
        return False
    if process_creation(identity['pid'])==identity['created']:return False
    if desktop_instance() is not None:return False
    # A package process may still be starting with no tray/window yet.
    script=r'''
$ErrorActionPreference='Stop'
$items=@(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe' OR Name='Codex.exe'")
foreach($p in $items) {
 if (-not $p.ExecutablePath) { throw 'Process identity unavailable' }
 if ($p.ExecutablePath -match '\\WindowsApps\\OpenAI\.Codex_[^\\]+\\app\\(?:ChatGPT|Codex)\.exe$') { Write-Output 'running'; exit 0 }
}
Write-Output 'absent'
'''
    shell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,creationflags=hidden_flags())
    return result.returncode==0 and result.stdout.strip()==b'absent'


def open_chat(thread_id):
    # A validated local chat ID is the only variable component in this protocol URL.
    ident=str(uuid.UUID(thread_id))
    os.startfile('codex://threads/'+ident)


def open_desktop(executable):
    if not desktop_version(executable):
        raise RuntimeError('无法确认 Codex 官方应用身份，未启动。')
    existing=desktop_instance()
    if existing: return existing['pid']
    # Launch the registered package as Windows does from Start. Avoid launching
    # a Store executable directly with a model/CLI process's environment.
    return activate_desktop_package()


def activate_desktop_package():
    """Windows IApplicationActivationManager; returns the OS-confirmed PID.

    https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-iapplicationactivationmanager-activateapplication
    """
    ole=ctypes.WinDLL('ole32')
    ole.CoUninitialize.argtypes=[];ole.CoUninitialize.restype=None
    ole.CoInitializeEx.argtypes=[ctypes.c_void_p,w.DWORD];ole.CoInitializeEx.restype=ctypes.c_long
    ole.CoCreateInstance.argtypes=[ctypes.c_void_p,ctypes.c_void_p,w.DWORD,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype=ctypes.c_long
    initialized=ole.CoInitializeEx(None,2)
    if initialized<0 and initialized!=-2147417850:
        raise RuntimeError(f'Windows 启动接口初始化失败：0x{initialized & 0xffffffff:08X}')
    manager=ctypes.c_void_p()
    clsid=ctypes.create_string_buffer(uuid.UUID('45BA127D-10A8-46EA-8AB7-56EA9078943C').bytes_le)
    iid=ctypes.create_string_buffer(uuid.UUID('2e941141-7f97-4756-ba1d-9decde894a3d').bytes_le)
    try:
        result=ole.CoCreateInstance(clsid,None,4,iid,ctypes.byref(manager))
        if result<0: raise RuntimeError(f'Windows 应用启动接口不可用：0x{result & 0xffffffff:08X}')
        table=ctypes.cast(manager,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        activate=ctypes.WINFUNCTYPE(ctypes.c_long,ctypes.c_void_p,w.LPCWSTR,w.LPCWSTR,w.DWORD,ctypes.POINTER(w.DWORD))(table[3])
        pid=w.DWORD()
        result=activate(manager,'OpenAI.Codex_2p2nqsd0c76g0!App',None,2,ctypes.byref(pid))
        if result<0 or not pid.value:
            raise RuntimeError(f'Windows 未能启动 Codex：0x{result & 0xffffffff:08X}；未重复启动。')
        return pid.value
    finally:
        if manager.value:
            table=ctypes.cast(manager,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            ctypes.WINFUNCTYPE(w.ULONG,ctypes.c_void_p)(table[2])(manager)
        if initialized>=0: ole.CoUninitialize()


def wake_task_xml(wake_at):
    import sys
    ns='http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('',ns)
    def add(parent,name,text=None,**attrs):
        node=ET.SubElement(parent,'{'+ns+'}'+name,attrs);node.text=text;return node
    task=ET.Element('{'+ns+'}Task',{'version':'1.2'})
    trigger=add(add(task,'Triggers'),'TimeTrigger')
    repetition=add(trigger,'Repetition')
    add(repetition,'Interval','PT1M');add(repetition,'StopAtDurationEnd','false')
    add(trigger,'StartBoundary',datetime.datetime.fromtimestamp(min(wake_at,time.time()+60)).astimezone().isoformat(timespec='seconds'))
    add(trigger,'Enabled','true')
    principal=add(add(task,'Principals'),'Principal',id='CurrentUser')
    add(principal,'UserId',os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME'])
    add(principal,'LogonType','InteractiveToken');add(principal,'RunLevel','LeastPrivilege')
    settings=add(task,'Settings')
    for k,v in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),
                ('StartWhenAvailable','true'),('Enabled','true'),('ExecutionTimeLimit','PT5M')]: add(settings,k,v)
    action=add(add(task,'Actions',Context='CurrentUser'),'Exec')
    add(action,'Command',str(Path(sys.executable).with_name('pythonw.exe')))
    add(action,'Arguments','"'+str(Path(__file__).with_name('guard.py'))+'" start')
    return ET.tostring(task,encoding='utf-16',xml_declaration=True)


def register_wake(plan_id,wake_at):
    name='CodexQuotaGuard-Reset-'+str(uuid.UUID(plan_id))
    with tempfile.TemporaryDirectory(prefix='quota-guard-wake-') as tmp:
        path=Path(tmp)/'task.xml';path.write_bytes(wake_task_xml(wake_at))
        result=subprocess.run(['schtasks.exe','/Create','/TN',name,'/XML',str(path),'/F'],
             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=hidden_flags(),timeout=20)
    if result.returncode: raise RuntimeError('Windows 定时唤起任务注册失败，未退出 Codex。')
    return name


def remove_wake(name):
    if not isinstance(name,str) or not name.startswith('CodexQuotaGuard-Reset-'): return False
    try: uuid.UUID(name.removeprefix('CodexQuotaGuard-Reset-'))
    except ValueError: return False
    try:
        result=subprocess.run(['schtasks.exe','/Delete','/TN',name,'/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                       timeout=10,creationflags=hidden_flags())
        if result.returncode==0: return True
        # Deletion may have succeeded before a process interruption. An absent
        # task is already clean; other query failures retain the name for retry.
        query=subprocess.run(['schtasks.exe','/Query','/TN',name,'/XML'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                       timeout=10,creationflags=hidden_flags())
        missing=(b'0x80070002' in query.stderr or b'cannot find' in query.stderr.lower()
                 or '系统找不到指定的文件'.encode('gbk') in query.stderr)
        return query.returncode!=0 and missing
    except (OSError,subprocess.TimeoutExpired): return False
