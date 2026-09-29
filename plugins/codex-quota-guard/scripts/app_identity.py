"""Assistant window identity and bundled icons; no runtime imaging dependency."""
import os
from pathlib import Path
import tkinter as tk

ASSETS=Path(__file__).resolve().parents[1]/'assets'

def set_app_identity():
    if os.name=='nt':
        import ctypes
        shell=ctypes.windll.shell32
        shell.SetCurrentProcessExplicitAppUserModelID.argtypes=[ctypes.c_wchar_p]
        shell.SetCurrentProcessExplicitAppUserModelID.restype=ctypes.c_long
        shell.SetCurrentProcessExplicitAppUserModelID('CodexAutoSwitchAssistant.Settings')

def install_icon(app):
    # Keep the Tk image alive; default=True also supplies future queue/dialogs.
    app._assistant_icon=tk.PhotoImage(master=app,file=str(ASSETS/'app-icon.png'))
    app.iconphoto(True,app._assistant_icon)
    if os.name=='nt':
        app.iconbitmap(str(ASSETS/'app-icon.ico'))
        app.iconbitmap(default=str(ASSETS/'app-icon.ico'))
