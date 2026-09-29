"""Discover and apply this computer's paths, with editable missing values."""
import queue,threading,tkinter as tk
from tkinter import ttk,filedialog
from i18n import TextVar,tr,localize,messagebox

FIELDS=[('desktop_exe','Codex 桌面程序'),('codex_exe','Codex CLI 程序'),('codex_home','Codex 配置目录'),
        ('cc_switch_exe','CC Switch 程序'),('cc_switch_dir','CC Switch 数据目录'),('python_exe','Python 运行时'),('node_exe','Node.js 运行时')]

def open_configuration(parent,on_change=lambda:None):
    from machine_config import detect,save
    dialog=tk.Toplevel(parent);dialog.title('一键配置');dialog.geometry('940x560');dialog.transient(parent)
    frame=ttk.Frame(dialog,padding=18);frame.pack(fill='both',expand=True);frame.columnconfigure(1,weight=1)
    ttk.Label(frame,text='自动识别本机路径；缺失项可手动选择。不会读取或复制登录令牌。',wraplength=880).grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,14))
    values={};buttons=[];metadata={};events=queue.Queue();busy=False
    for row,(key,label) in enumerate(FIELDS,1):
        ttk.Label(frame,text=label).grid(row=row,column=0,sticky='w',padx=(0,12),pady=7)
        var=tk.StringVar();values[key]=var
        ttk.Entry(frame,textvariable=var).grid(row=row,column=1,sticky='ew')
        def browse(k=key,v=var):
            path=(filedialog.askdirectory if k.endswith(('_dir','_home')) else filedialog.askopenfilename)(parent=dialog,title=tr('选择路径'))
            if path:v.set(path)
        button=ttk.Button(frame,text='浏览…',command=browse);button.grid(row=row,column=2,padx=(8,0));buttons.append(button)
    status=TextVar(value='正在检测本机运行环境…')
    ttk.Label(frame,textvariable=status,wraplength=880).grid(row=8,column=0,columnspan=3,sticky='w',pady=15)
    actions=ttk.Frame(frame);actions.grid(row=9,column=0,columnspan=3,sticky='e')
    def start(action):
        nonlocal busy
        if busy:return
        busy=True
        for button in buttons:button.state(['disabled'])
        payload=dict(metadata,**{k:v.get().strip() for k,v in values.items()})
        status.set('正在检测本机运行环境…' if action=='detect' else '正在验证并保存配置…')
        def worker():
            try:
                if action=='detect':
                    found=detect()
                    try:save(found);events.put(('saved',found))
                    except (ValueError,OSError,RuntimeError) as exc:events.put(('detected',found,str(exc)))
                else:events.put(('saved',save(payload)))
            except Exception as exc:events.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True).start()
    buttons.extend([ttk.Button(actions,text='重新检测',command=lambda:start('detect')),ttk.Button(actions,text='保存配置',command=lambda:start('save'))])
    for button in buttons[-2:]:button.pack(side='left',padx=5)
    def poll():
        nonlocal busy,metadata
        if not dialog.winfo_exists():return
        try:
            while True:
                result=events.get_nowait();busy=False
                for button in buttons:button.state(['!disabled'])
                if result[0]=='error':status.set('配置未完成：'+result[1])
                else:
                    metadata=result[1]
                    for key,var in values.items():var.set(metadata.get(key,''))
                    if result[0]=='saved':status.set('配置已保存，后台将重新连接。自动换号开关和阈值保持不变。');on_change()
                    else:status.set('请检查或补全路径：'+result[2])
        except queue.Empty:pass
        dialog.after(150,poll)
    def close():
        if busy:messagebox.showinfo('正在配置','请等待检测或保存结束。',parent=dialog)
        else:dialog.destroy()
    dialog.protocol('WM_DELETE_WINDOW',close)
    localize(dialog);start('detect');poll();return dialog
