"""Native task queue editor; saves instructions locally until safe dispatch."""
import tkinter as tk
from tkinter import ttk
from i18n import tr,TextVar,messagebox,localize
from task_queue import QueueStore,PHASES,DELETABLE_PHASES
from runtime import ensure_service
from ui_theme import BG,INK,MUTED,SOFT,LINE,FONT


def text_panel(parent,height,readonly=False):
    panel=tk.Frame(parent,bg=LINE,highlightthickness=0,padx=1,pady=1)
    inside=tk.Frame(panel,bg=SOFT if readonly else BG);inside.pack(fill='both',expand=True)
    text=tk.Text(inside,wrap='word',font=(FONT,11),height=height,relief='flat',bd=0,
                 bg=SOFT if readonly else BG,fg=INK,insertbackground=INK,
                 padx=14,pady=12,spacing1=3,spacing3=5,undo=not readonly,
                 selectbackground='#dcecff',selectforeground=INK,highlightthickness=0)
    bar=ttk.Scrollbar(inside,orient='vertical',command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side='right',fill='y');text.pack(fill='both',expand=True)
    if readonly:text.configure(state='disabled')
    else:
        text.bind('<FocusIn>',lambda _:panel.configure(bg='#b8c0c5'))
        text.bind('<FocusOut>',lambda _:panel.configure(bg=LINE))
    return panel,text


def edit_task(parent,tid,get_thread,on_change=lambda:None,item=None):
    title='编辑待执行任务' if item else '添加待执行任务'
    dialog=tk.Toplevel(parent);dialog.title(title);dialog.configure(bg=BG)
    dialog.geometry('780x550');dialog.minsize(700,480);dialog.transient(parent)
    frame=ttk.Frame(dialog,padding=28);frame.pack(fill='both',expand=True)
    frame.columnconfigure(0,weight=1);frame.rowconfigure(3,weight=1)
    ttk.Label(frame,text=title,font=(FONT,17,'bold')).grid(row=0,column=0,sticky='w')
    ttk.Label(frame,text='每次保存加入一项；多行内容属于同一项任务。',foreground=MUTED).grid(row=1,column=0,sticky='w',pady=(8,22))
    ttk.Label(frame,text='任务内容',font=(FONT,10,'bold')).grid(row=2,column=0,sticky='w',pady=(0,8))
    panel,text=text_panel(frame,10);panel.grid(row=3,column=0,sticky='nsew')
    if item:text.insert('1.0',item['text'])
    ttk.Label(frame,text='内容保存在本机队列；上一项结束且额度达标后才发送到原聊天。',
              foreground=MUTED,wraplength=640).grid(row=4,column=0,sticky='w',pady=(14,18))
    def save():
        try:
            store=QueueStore()
            if item:store.edit(tid,item['id'],text.get('1.0','end-1c'))
            else:
                thread=get_thread(tid)
                if not thread:raise ValueError('请先打开该聊天，让守护程序读取当前状态。')
                store.add(thread,text.get('1.0','end-1c'))
            on_change();dialog.destroy();ensure_service()
        except Exception as exc:messagebox.showerror('队列未保存',str(exc),parent=dialog if dialog.winfo_exists() else parent)
    buttons=ttk.Frame(frame);buttons.grid(row=5,column=0,sticky='ew')
    ttk.Label(buttons,text='Ctrl + Enter',foreground=MUTED,font=(FONT,9)).pack(side='left')
    ttk.Button(buttons,text='保存修改' if item else '加入队列',style='Primary.TButton',command=save).pack(side='right')
    ttk.Button(buttons,text='取消',style='Quiet.TButton',command=dialog.destroy).pack(side='right',padx=(0,8))
    dialog.bind('<Control-Return>',lambda _:(save(),'break')[-1])
    dialog.bind('<Escape>',lambda _:dialog.destroy())
    text.focus_set();localize(dialog)
    return dialog


def open_queue(parent,tid,get_thread,on_change=lambda:None):
    dialog=tk.Toplevel(parent);dialog.title('待执行任务队列');dialog.configure(bg=BG)
    dialog.geometry('920x780');dialog.minsize(840,740);dialog.transient(parent)
    frame=ttk.Frame(dialog,padding=28);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='待执行任务队列',font=(FONT,17,'bold')).pack(anchor='w')
    status_line=ttk.Frame(frame);status_line.pack(fill='x',pady=(9,18))
    dot=ttk.Label(status_line,text='●',foreground=MUTED);dot.pack(side='left',padx=(0,8))
    status=TextVar()
    ttk.Label(status_line,textvariable=status,wraplength=730,foreground=MUTED).pack(side='left')
    buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=(0,14))
    table=ttk.Frame(frame);table.pack(fill='both',expand=True)
    tree=ttk.Treeview(table,columns=('order','state','text'),show='headings',height=6,selectmode='browse')
    for key,label,width in [('order','顺序',65),('state','状态',120),('text','任务内容',630)]:
        tree.heading(key,text=label);tree.column(key,width=width,minwidth=55,stretch=key=='text',anchor='w' if key=='text' else 'center')
    scroll=ttk.Scrollbar(table,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True)
    ttk.Label(frame,text='内容预览',font=(FONT,10,'bold')).pack(anchor='w',pady=(18,8))
    panel,preview=text_panel(frame,4,readonly=True);panel.pack(fill='x')
    controls=ttk.Frame(frame);controls.pack(fill='x',pady=(18,12))
    ttk.Label(frame,text='待执行和已处理的条目可删除；发送中、执行中或结果待确认的条目会保留。',
              foreground=MUTED,wraplength=760).pack(anchor='w')
    ttk.Label(frame,text='暂停队列不停止正在运行的任务；结果不明时请先检查原聊天，再标记已处理。',
              foreground=MUTED,wraplength=760).pack(anchor='w',pady=(5,0))
    store=QueueStore();items={};timer=None
    def selected():
        selection=tree.selection()
        if not selection or selection[0] not in items:raise ValueError('请先选择一个条目。')
        return items[selection[0]]
    def show(event=None):
        try:item=selected()
        except ValueError:item=None
        value=item['text'] if item else tr('选择条目查看完整内容。')
        if preview.get('1.0','end-1c')!=value:
            preview.configure(state='normal');preview.delete('1.0','end');preview.insert('1.0',value);preview.configure(state='disabled')
        phase=item['phase'] if item else None
        for button in editable:button.state(['!disabled'] if phase=='queued' else ['disabled'])
        delete_button.state(['!disabled'] if phase in DELETABLE_PHASES else ['disabled'])
        resolve_button.state(['!disabled'] if phase=='needs_review' else ['disabled'])
    def refresh():
        nonlocal items
        if not dialog.winfo_exists():return
        try:
            q=store.read()['threads'].get(tid,{})
            status.set(('队列已启用' if q.get('enabled',True) else '队列已暂停')+' · '+q.get('note','尚无待执行任务'))
            dot.configure(foreground='#16834a' if q.get('enabled',True) else MUTED)
            items={i['id']:i for i in q.get('items',[])}
            for ident in tree.get_children():
                if ident not in items:tree.delete(ident)
            for n,item in enumerate(items.values(),1):
                row=(n,tr(PHASES.get(item['phase'],item['phase'])),item['text'].replace('\n',' ')[:100])
                if tree.exists(item['id']):tree.item(item['id'],values=row);tree.move(item['id'],'',n-1)
                else:tree.insert('','end',iid=item['id'],values=row)
            if not tree.selection() and items:tree.selection_set(next(iter(items)))
            show()
        except Exception as exc:status.set('读取失败：'+str(exc))
    def changed():
        refresh();on_change()
    def act(fn):
        try:fn();changed()
        except Exception as exc:messagebox.showerror('队列操作未完成',str(exc),parent=dialog);refresh()
    def delete_selected(event=None):
        def perform():
            item=selected()
            if item['phase'] not in DELETABLE_PHASES:return
            if messagebox.askyesno('删除任务','删除选中的队列条目？此操作不会删除原聊天中的消息。',parent=dialog,default='no'):
                store.delete(tid,item['id'])
        act(perform);return 'break'
    ttk.Button(buttons,text='添加',style='Primary.TButton',command=lambda:edit_task(dialog,tid,get_thread,changed)).pack(side='left',padx=(0,8))
    editable=[]
    for label,fn in [('编辑',lambda:edit_task(dialog,tid,get_thread,changed,selected())),
                     ('上移',lambda:store.move(tid,selected()['id'],-1)),('下移',lambda:store.move(tid,selected()['id'],1))]:
        button=ttk.Button(buttons,text=label,command=lambda f=fn:act(f));button.pack(side='left',padx=(0,8));editable.append(button)
    delete_button=ttk.Button(buttons,text='删除',style='Danger.TButton',command=delete_selected);delete_button.pack(side='right')
    ttk.Button(controls,text='暂停队列',command=lambda:act(lambda:store.set_enabled(tid,False))).pack(side='left',padx=(0,8))
    ttk.Button(controls,text='启用队列',command=lambda:act(lambda:(store.set_enabled(tid,True,get_thread(tid)),ensure_service()))).pack(side='left',padx=(0,8))
    resolve_button=ttk.Button(controls,text='标记已处理（不重发）',style='Quiet.TButton',command=lambda:act(lambda:store.resolve(tid,selected()['id'])))
    resolve_button.pack(side='right')
    tree.bind('<<TreeviewSelect>>',show);tree.bind('<Delete>',delete_selected)
    def edit_selected(event):
        if editable[0].instate(['!disabled']):act(lambda:edit_task(dialog,tid,get_thread,changed,selected()))
    tree.bind('<Double-1>',edit_selected)
    def poll():
        nonlocal timer
        refresh();timer=dialog.after(1000,poll)
    def cleanup(event):
        if event.widget==dialog and timer is not None:dialog.after_cancel(timer)
    dialog.bind('<Destroy>',cleanup);poll();localize(dialog)
    return dialog
