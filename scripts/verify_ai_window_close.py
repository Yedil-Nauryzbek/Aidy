"""Close only a hidden Win32 window created by this verification process.

No user windows, applications, or documents are selected or changed.
"""
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'PythonCore'))
from aidy.ai_windows import Windows, process_name
from aidy.assistant import Aidy

HELPER = r'''
import ctypes,json,os
from ctypes import wintypes
user=ctypes.WinDLL('user32',use_last_error=True)
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
callback=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM)
class WNDCLASS(ctypes.Structure):
    _fields_=[('style',wintypes.UINT),('proc',callback),('class_extra',ctypes.c_int),('window_extra',ctypes.c_int),('instance',wintypes.HINSTANCE),('icon',wintypes.HICON),('cursor',wintypes.HANDLE),('background',wintypes.HBRUSH),('menu',wintypes.LPCWSTR),('name',wintypes.LPCWSTR)]
user.DefWindowProcW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
user.DefWindowProcW.restype=ctypes.c_ssize_t
user.DestroyWindow.argtypes=[wintypes.HWND]
user.CreateWindowExW.restype=wintypes.HWND
user.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p]
kernel.GetModuleHandleW.restype=wintypes.HMODULE
@callback
def proc(hwnd,msg,wp,lp):
    if msg==0x10:
        user.DestroyWindow(hwnd)
        return 0
    if msg==2:
        user.PostQuitMessage(0)
        return 0
    return user.DefWindowProcW(hwnd,msg,wp,lp)
instance=kernel.GetModuleHandleW(None)
name='AidyCloseVerification'+str(os.getpid())
wc=WNDCLASS();wc.proc=proc;wc.instance=instance;wc.name=name
assert user.RegisterClassW(ctypes.byref(wc))
hwnd=user.CreateWindowExW(0,name,'Aidy verification only',0,0,0,100,100,None,None,instance,None)
assert hwnd
print(json.dumps({'hwnd':int(hwnd),'pid':os.getpid(),'title':'Aidy verification only'}),flush=True)
msg=wintypes.MSG()
while user.GetMessageW(ctypes.byref(msg),None,0,0)>0:
    user.TranslateMessage(ctypes.byref(msg));user.DispatchMessageW(ctypes.byref(msg))
'''


def main():
    child = subprocess.Popen([getattr(sys, '_base_executable', sys.executable), '-u', '-c', HELPER], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        received = queue.Queue()
        threading.Thread(target=lambda: received.put(child.stdout.readline()), daemon=True).start()
        window = json.loads(received.get(timeout=5))
        assert window['pid'] == child.pid
        host = Mock()
        # The target list contains exactly our hidden verification window.
        host.apps = []
        host._CLOSE_EVERYTHING_PROTECTED = set()
        host._is_protected_process_for_close.return_value = False
        host._send_wm_close = Aidy._send_wm_close.__get__(host)
        windows = Windows(host)
        windows.targets = {'fixture': dict(window, process=process_name(child.pid))}
        windows.bind_one('fixture')
        result = windows.close_frozen(threading.Event(), lambda: True)
        assert result.status == 'verified', result
        assert child.wait(timeout=3) == 0
        print('PASS: real HWND/PID verification, WM_CLOSE dispatch, window destruction and child cleanup.')
        print('Only the hidden test window was closed; no user application was targeted.')
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        child.stdout.close()
        child.stderr.close()


if __name__ == '__main__':
    main()
