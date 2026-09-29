"""Native Windows settings; no local HTTP server or browser credentials."""
import json, time, uuid, queue, threading, tkinter as tk
from tkinter import ttk
from i18n import tr,TextVar,messagebox,localize,load_language,language,set_language
from ui_theme import install_theme,Section,QuotaBar,BG,INK,MUTED,SOFT,FONT
from core import atomic_json, read_json, validate_settings
from runtime import data_dir, load_settings, ensure_service, plugin_enabled

def enqueue(action, **kwargs):
    atomic_json(data_dir()/'commands'/(uuid.uuid4().hex+'.json'),dict(action=action,**kwargs))

class AutoSwitchButton(tk.Canvas):
    """Persistent mode toggle, reachable by mouse and keyboard."""
    def __init__(self, parent, variable, command):
        self.variable=variable; self.command=command
        self.scale=max(1,parent.winfo_fpixels('1i')/96)
        super().__init__(parent,width=round(205*self.scale),height=round(44*self.scale),
                         bg=BG,highlightthickness=0,takefocus=True,cursor='hand2')
        self.bind('<Button-1>',self.invoke)
        self.bind('<space>',self.invoke); self.bind('<Return>',self.invoke)
        self.bind('<FocusIn>',self.draw); self.bind('<FocusOut>',self.draw)
        self.bind('<Configure>',self.draw)
        variable.trace_add('write',self.draw)
        self.draw()
    def invoke(self,event=None):
        self.focus_set(); self.command(); return 'break'
    def draw(self,*args):
        self.delete('all'); s=self.scale
        width=max(self.winfo_width(),int(self.cget('width'))); height=int(self.cget('height'))
        x,y=3*s,3*s; right,bottom=width-x,height-y; radius=(bottom-y)/2
        color='#16834a' if self.variable.get() else '#777a7d'
        self.create_polygon(x+radius,y,right-radius,y,right,y,right,bottom,
                            right-radius,bottom,x+radius,bottom,x,bottom,x,y,
                            smooth=True,splinesteps=36,fill=color,
                            outline='#a6d7bc' if self.focus_get()==self else color,width=1*s,tags='pill')
        self.create_oval(18*s,18*s,26*s,26*s,outline='white',
                         fill='white' if self.variable.get() else color,width=1.5*s)
        self.create_text(width/2+7*s,height/2,text=tr('自动切换账号'),fill='white',
                         font=(FONT,10,'bold'))

def square_checkbuttons(app, style):
    # Keep ttk's keyboard/focus and BooleanVar behavior; replace only its glyph.
    size=max(12,round(app.winfo_fpixels('1i')*14/96))
    border=max(1,round(size/14))
    empty=tk.PhotoImage(master=app,width=size,height=size)
    empty.put(MUTED,to=(0,0,size,size))
    empty.put('#ffffff',to=(border,border,size-border,size-border))
    selected=tk.PhotoImage(master=app,width=size,height=size)
    selected.put(MUTED,to=(0,0,size,size))
    selected.put('#ffffff',to=(border,border,size-border,size-border))
    inset=border+max(1,round(size/7))
    selected.put(INK,to=(inset,inset,size-inset,size-inset))
    app._checkbox_images=(empty,selected)  # Tk requires live Python references.
    style.element_create('SolidSquare.indicator','image',empty,('selected',selected),width=size+8,sticky='w')
    def replace_indicator(layout):
        return [('SolidSquare.indicator' if name=='Checkbutton.indicator' else name,
                 {key:replace_indicator(value) if key=='children' else value for key,value in options.items()})
                for name,options in layout]
    style.layout('SolidSquare.TCheckbutton',replace_indicator(style.layout('TCheckbutton')))

class ScrollPage(ttk.Frame):
    def __init__(self,parent):
        super().__init__(parent)
        canvas=tk.Canvas(self,bg=BG,highlightthickness=0)
        bar=ttk.Scrollbar(self,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        self.body=ttk.Frame(canvas,padding=(0,8,16,8));window=canvas.create_window(0,0,anchor='nw',window=self.body)
        self.body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        def wheel(event):
            target=event.widget
            while target is not None:
                if target==self:canvas.yview_scroll(-int(event.delta/120),'units');return
                target=getattr(target,'master',None)
        self.bind_all('<MouseWheel>',wheel,add='+')

def run_ui():
    load_language()
    from app_identity import set_app_identity,install_icon
    set_app_identity()
    app=tk.Tk(); app.title('Codex 自动切换小助手'); app.geometry('1080x940'); app.minsize(980,740)
    install_icon(app)
    style=install_theme(app)
    square_checkbuttons(app,style)
    outer=ttk.Frame(app,padding=(32,24,32,18)); outer.pack(fill='both',expand=True)
    values=load_settings(); variables={}
    auto_switch=tk.BooleanVar(value=values['auto_switch'])
    header=ttk.Frame(outer); header.pack(fill='x')
    header_text=ttk.Frame(header);header_text.pack(side='left',anchor='n',pady=(7,0))
    ttk.Label(header_text,text='Codex 自动切换小助手',font=(FONT,18,'bold')).pack(anchor='w')
    ttk.Label(header_text,text='Save your sleep',font=(FONT,11),foreground=MUTED).pack(anchor='w',pady=(4,0))
    ttk.Label(header_text,text='监控当前登录账号 · 换号后按新账号额度恢复',foreground=MUTED).pack(anchor='w',pady=(7,0))
    header_controls=ttk.Frame(header);header_controls.pack(side='right')
    def set_auto_switch(value):
        try:
            new=validate_settings(dict(load_settings(),auto_switch=value))
            atomic_json(data_dir()/'settings.json',new)
        except Exception as exc:
            auto_switch.set(load_settings()['auto_switch'])
            messagebox.showerror('自动切换设置未保存',str(exc),parent=app); return
        auto_switch.set(new['auto_switch'])
        text='自动切换账号已开启。' if value else '自动切换账号已关闭。'
        if value and (not new['enabled'] or not new['auto_resume'] or not plugin_enabled()):
            text+=' 需同时开启插件、自动管理和自动继续才能执行。'
        notice.set(text); account_notice.set(text)
        try: ensure_service(); enqueue('check')
        except Exception: notice.set(text+' 设置已保存，后台暂未响应，请检查运行状态。')
    auto_button=AutoSwitchButton(header_controls,auto_switch,lambda:set_auto_switch(not load_settings()['auto_switch']))
    auto_button.grid(row=0,column=1)
    def switch_language():
        set_language('en' if language()=='zh' else 'zh',app)
        language_button.configure(text='English' if language()=='zh' else '中文')
        auto_button.draw();render_accounts();refresh(schedule=False)
    language_button=ttk.Button(header_controls,command=switch_language,style='Quiet.TButton')
    language_button.grid(row=0,column=0,padx=(0,10))
    def configure_machine():
        from configuration_ui import open_configuration
        open_configuration(app,on_change=read_accounts)
    ttk.Button(header_controls,text='一键配置',command=configure_machine,style='Setup.TButton').grid(row=1,column=1,sticky='e',pady=(4,0))
    status_line=ttk.Frame(outer);status_line.pack(fill='x',pady=(12,12))
    status_dot=ttk.Label(status_line,text='●',foreground=MUTED,font=(FONT,9));status_dot.pack(side='left',padx=(0,7))
    status_text=TextVar(value='读取运行状态…'); ttk.Label(status_line,textvariable=status_text,foreground=MUTED,wraplength=930).pack(side='left')
    quota_text=TextVar(value='尚未读取额度')
    quota_row=ttk.Frame(outer);quota_row.pack(fill='x')
    quota_values={};quota_bars={}
    for index,(key,title) in enumerate((('5h','5 小时额度'),('week','每周额度'))):
        quota_row.columnconfigure(index,weight=1,uniform='quota')
        card=ttk.Frame(quota_row,style='Quota.TFrame',padding=(18,12));card.grid(row=0,column=index,sticky='ew',padx=(0,6) if index==0 else (6,0))
        line=ttk.Frame(card,style='Quota.TFrame');line.pack(fill='x')
        ttk.Label(line,text=title,style='Quota.TLabel').pack(side='left')
        quota_values[key]=TextVar(value='—')
        ttk.Label(line,textvariable=quota_values[key],style='QuotaValue.TLabel').pack(side='right')
        quota_bars[key]=QuotaBar(card)
        quota_bars[key].pack(fill='x',pady=(8,0))
    reset_text=TextVar(); ttk.Label(outer,textvariable=reset_text,foreground=MUTED,font=(FONT,9),wraplength=950).pack(anchor='w',pady=(9,0))
    tabs=ttk.Notebook(outer); tabs.pack(fill='both',expand=True,pady=(18,8))
    main_page=ScrollPage(tabs);settings_page=ScrollPage(tabs)
    tabs.add(main_page,text='主界面');tabs.add(settings_page,text='设置')
    settings_section=Section(settings_page.body,text='阈值与开关');settings=settings_section.body;settings_section.pack(fill='x',pady=(4,14))
    tasks_section=Section(main_page.body,text='任务与恢复记录');tasks=tasks_section.body
    settings.columnconfigure(0,minsize=250); settings.columnconfigure(1,minsize=160)
    accounts_section=Section(main_page.body,text='账号选择');accounts=accounts_section.body;accounts_section.pack(fill='x',pady=(4,14))
    tasks_section.pack(fill='both',expand=True,pady=(4,14))
    waiting_section=Section(settings_page.body,text='等待与监测');waiting=waiting_section.body;waiting_section.pack(fill='x',pady=(4,14))
    polling_section=Section(settings_page.body,text='动态监测');polling=polling_section.body;polling_section.pack(fill='x',pady=(4,14))
    dynamic=tk.BooleanVar(value=values['dynamic_poll']);poll_vars={}
    ttk.Checkbutton(polling,text='按当前账号五小时剩余额度动态调整检查间隔',variable=dynamic,style='SolidSquare.TCheckbutton').grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,15))
    poll_fields=[('poll_mid_below','中额度分界（低于）','%',1,100),('poll_low_below','低额度分界（低于）','%',1,99),
                 ('poll_high_seconds','高额度检查间隔','秒',10,3600),('poll_mid_seconds','中额度检查间隔','秒',10,3600),
                 ('poll_low_seconds','低额度／恢复核验间隔','秒',10,3600)]
    for row,(key,label,unit,low,high) in enumerate(poll_fields,1):
        ttk.Label(polling,text=label).grid(row=row,column=0,sticky='w',padx=(0,20),pady=10)
        poll_vars[key]=TextVar(value=str(values[key]))
        ttk.Spinbox(polling,from_=low,to=high,width=9,textvariable=poll_vars[key]).grid(row=row,column=1,sticky='w')
        ttk.Label(polling,text=unit).grid(row=row,column=2,sticky='w',padx=8)
    poll_preview=TextVar()
    def preview_poll(*args):
        try:
            mid=float(poll_vars['poll_mid_below'].get());low=float(poll_vars['poll_low_below'].get())
            high_s=int(poll_vars['poll_high_seconds'].get());mid_s=int(poll_vars['poll_mid_seconds'].get());low_s=int(poll_vars['poll_low_seconds'].get())
            poll_preview.set(f'剩余 ≥ {mid:g}%：每 {high_s} 秒\n{low:g}% ≤ 剩余 < {mid:g}%：每 {mid_s} 秒\n剩余 < {low:g}%：每 {low_s} 秒')
        except ValueError: poll_preview.set('输入有效数字后显示策略预览。')
    for var in poll_vars.values():var.trace_add('write',preview_poll)
    preview_poll()
    ttk.Label(polling,textvariable=poll_preview,wraplength=740).grid(row=6,column=0,columnspan=3,sticky='w',pady=16)
    ttk.Label(polling,text='暂停阈值在“阈值与开关”页单独设置。达到该阈值或周额度耗尽时先暂停任务。\n查询失败、达到暂停阈值、等待重置或核验恢复时，采用低额度间隔。\n关闭动态监测后，使用“阈值与开关”中的固定检查间隔。\n轮询只能在查询时发现变化；多任务同时消耗额度时可能跨过多个分界。',
              foreground=MUTED,wraplength=750).grid(row=7,column=0,columnspan=3,sticky='w',pady=12)
    polling_buttons=ttk.Frame(polling);polling_buttons.grid(row=8,column=0,columnspan=3,sticky='w',pady=12)
    updates_section=Section(settings_page.body,text='版本与适配');updates=updates_section.body;updates_section.pack(fill='x',pady=(4,14))
    tracking=tk.BooleanVar(value=values['update_tracking']);adapt=tk.BooleanVar(value=values['auto_adapt'])
    ttk.Checkbutton(updates,text='自动跟踪本机版本及官方发布信息',variable=tracking,style='SolidSquare.TCheckbutton').pack(anchor='w',pady=5)
    ttk.Checkbutton(updates,text='接口检查通过后自动沿用适配器',variable=adapt,style='SolidSquare.TCheckbutton').pack(anchor='w',pady=5)
    ttk.Label(updates,text='本机版本每分钟检查；官方发布信息每 6 小时查询。\n自动跟随 Codex CLI 路径变化，操作前重新核验界面和协议。\n协议或界面不匹配时停止对应操作、保留任务记录；不会自动生成或运行未知补丁。',
              foreground=MUTED,wraplength=750).pack(anchor='w',pady=12)
    update_status=TextVar(value='等待后台检查…')
    ttk.Label(updates,textvariable=update_status,wraplength=750).pack(anchor='w',pady=12)
    update_buttons=ttk.Frame(updates);update_buttons.pack(anchor='w',pady=12)
    ttk.Button(update_buttons,text='立即检查版本与适配',command=lambda:(ensure_service(),enqueue('check_updates'))).pack(side='left')
    wait_reset=tk.BooleanVar(value=values['wait_for_reset'])
    wait_auto=tk.BooleanVar(value=values['wait_auto_resume'])
    def save_wait_options():
        try:
            new=validate_settings(dict(load_settings(),wait_for_reset=wait_reset.get(),wait_auto_resume=wait_auto.get()))
            atomic_json(data_dir()/'settings.json',new)
            ensure_service();enqueue('check')
        except Exception as exc:
            saved=load_settings();wait_reset.set(saved['wait_for_reset']);wait_auto.set(saved['wait_auto_resume'])
            messagebox.showerror('设置未保存',str(exc),parent=app)
    ttk.Label(waiting,text='全部账号额度不足时',font=(FONT,14,'bold')).pack(anchor='w',pady=(0,16))
    ttk.Checkbutton(waiting,text='选择最早重置的账号，退出 Codex 并等待恢复',variable=wait_reset,
                    style='SolidSquare.TCheckbutton',command=save_wait_options).pack(anchor='w')
    ttk.Checkbutton(waiting,text='额度恢复时自动重启任务',variable=wait_auto,
                    style='SolidSquare.TCheckbutton',command=save_wait_options).pack(anchor='w',pady=(6,0))
    ttk.Label(waiting,text='勾选后立即保存。关闭自动重启仍持续监测并保留等待计划；重新勾选后再次核验额度。',
              foreground=MUTED,wraplength=750).pack(anchor='w',pady=(8,0))
    ttk.Label(waiting,text='保存暂停记录、正常退出 Codex 后，再切换到周额度未耗尽的账号。\n自然重置时间后保留 5 分钟缓冲，再核验额度并打开原聊天。\n\n等待期间仍按低额度间隔持续监测各账号（固定模式用固定间隔）。\n若检测到提前恢复，连续两次核验达标后可提前重新打开 Codex。\n\nWindows 定时任务每分钟检查后台是否存活；需要用户登录，\n电脑休眠或关机期间不执行，恢复运行后继续检查。',
              foreground=MUTED,wraplength=750).pack(anchor='w',pady=18)
    wait_status=TextVar(value='尚无等待计划')
    ttk.Label(waiting,textvariable=wait_status,wraplength=750).pack(anchor='w',pady=12)
    strategy=TextVar(value=values['selection_strategy'])
    account_rows=[]; accounts_loaded=False; account_order=list(values['account_priority'])
    policy_row=ttk.Frame(accounts);policy_row.pack(anchor='w')
    ttk.Radiobutton(policy_row,text='综合额度优先',variable=strategy,value='quota').pack(side='left',padx=(0,24))
    ttk.Radiobutton(policy_row,text='自定义账号优先级',variable=strategy,value='priority').pack(side='left')
    ttk.Label(accounts,text='综合考虑 5 小时与周额度；跳过当前账号、额度不足及查询失败的账号。',foreground=MUTED,wraplength=860).pack(anchor='w',pady=6)
    account_table=ttk.Frame(accounts);account_table.pack(fill='both',expand=True)
    account_tree=ttk.Treeview(account_table,columns=('rank','name','5h','reset5h','week','resetweek','state','checked'),show='headings',height=3,selectmode='browse')
    for key,label,width in [('rank','优先级',60),('name','CC Switch 账号',200),('5h','5h 剩余',110),('reset5h','5h 重置倒计时',135),('week','周剩余',125),('resetweek','周额度重置倒计时',155),('state','状态',150),('checked','额度更新时间',115)]:
        account_tree.heading(key,text=label); account_tree.column(key,width=width,minwidth=width,stretch=key=='name')
    account_tree.pack(fill='both',expand=True)
    account_scroll=ttk.Scrollbar(account_table,orient='horizontal',command=account_tree.xview)
    account_scroll.pack(fill='x');account_tree.configure(xscrollcommand=account_scroll.set)
    account_notice=TextVar(value='正在读取 CC Switch 账号…')
    ttk.Label(accounts,textvariable=account_notice,foreground=MUTED,wraplength=860).pack(anchor='w',pady=4)
    ttk.Label(accounts,text='显示剩余额度（100%－已用），自动刷新 CC Switch；≈ 表示取整值，缓存会标注。',foreground=MUTED,wraplength=860).pack(anchor='w',pady=4)
    ttk.Label(accounts,text='重置倒计时：5h 额度按小时、周额度按天显示，保留一位小数；≈ 表示估算值。',foreground=MUTED,wraplength=860).pack(anchor='w',pady=4)
    switch_status=TextVar()
    ttk.Label(accounts,textvariable=switch_status,wraplength=760).pack(anchor='w',pady=4)
    from account_usage import cached_usage,read_usage,format_reset_time
    usage=cached_usage();usage_events=queue.Queue();usage_inflight=False;usage_due=0
    refresh_notice=TextVar()
    ttk.Label(accounts,textvariable=refresh_notice,foreground=MUTED,wraplength=860).pack(anchor='w',pady=(0,4))
    def render_accounts(selected=None):
        if selected is None:selected=next(iter(account_tree.selection()),None)
        ids={row['account'] for row in account_rows}
        for item in account_tree.get_children():
            if item not in ids:account_tree.delete(item)
        for i,row in enumerate(account_rows):
            entry=usage.get(row['id'],{});quota=entry.get('quota',{}) if not entry.get('error') else {}
            def amount(key):return f"≈{quota[key]:.1f}%" if key in quota else '—'
            state=tr(row.get('state',''))
            if entry.get('error'):state+=' · '+tr('无法读取额度')
            elif entry and (entry.get('cached') or time.time()-entry.get('checked_at',0)>180):state+=' · '+tr('缓存')
            checked=time.strftime('%H:%M:%S',time.localtime(entry['checked_at'])) if entry.get('checked_at') else '—'
            display=(i+1,row['name'],amount('5h'),format_reset_time(entry,'5h'),amount('week'),format_reset_time(entry,'week'),state,checked)
            if account_tree.exists(row['account']):account_tree.item(row['account'],values=display);account_tree.move(row['account'],'',i)
            else:account_tree.insert('',tk.END,iid=row['account'],values=display)
        if selected in account_tree.get_children(): account_tree.selection_set(selected); account_tree.see(selected)
    def request_usage(manual=False):
        nonlocal usage_inflight,usage_due
        if usage_inflight:
            if manual:refresh_notice.set('额度正在刷新，请等待本次查询结束。')
            return
        if manual:
            from reset_recovery import ACTIVE_PHASES
            if read_json(data_dir()/'reset-wait.json',{}).get('phase') in ACTIVE_PHASES-{'needs_review'}:
                refresh_notice.set('换号或等待恢复流程正在操作 CC Switch，请稍后刷新。');return
        usage_due=time.time()+max(30,read_json(data_dir()/'status.json',{}).get('poll_interval') or load_settings()['poll_high_seconds'])
        if not account_rows:
            if manual:refresh_notice.set('未读取到账号，请先重新读取 CC Switch 账号。')
            return
        usage_inflight=True
        refresh_cc_button.state(['disabled'])
        if manual:refresh_notice.set('正在刷新 CC Switch；查询失败的账号会自动重试一次…')
        providers=[dict(r) for r in account_rows]
        def worker():
            try:rows=read_usage(providers,retry_errors=True) if manual else read_usage(providers)
            except Exception:rows=[]
            usage_events.put((rows,manual,len(providers)))
        threading.Thread(target=worker,daemon=True).start()
    def read_accounts():
        nonlocal account_rows,accounts_loaded
        try:
            from manual_switch import provider_inventory,ordered_accounts
            active,providers=provider_inventory()
            priorities=[r['account'] for r in account_rows] if accounts_loaded else account_order
            account_rows=ordered_accounts(providers,priorities)
            for row in account_rows: row['state']='当前账号' if row['account']==active['account'] else '候选账号'
            accounts_loaded=True; render_accounts()
            request_usage()
            account_notice.set(f'已读取 {len(account_rows)} 个账号。选中后上移或下移；保存后生效。新增账号自动排在末尾。')
        except Exception as exc:
            account_notice.set('读取失败：'+(str(exc) if isinstance(exc,(RuntimeError,ValueError)) else '请检查 CC Switch 配置。')+'；已保存的顺序保持不变。')
    def move_account(delta):
        selected=account_tree.selection()
        if not selected: return
        index=next(i for i,r in enumerate(account_rows) if r['account']==selected[0]); target=index+delta
        if 0<=target<len(account_rows):
            account_rows[index],account_rows[target]=account_rows[target],account_rows[index]
            render_accounts(selected[0])
    account_buttons=ttk.Frame(accounts); account_buttons.pack(anchor='w',pady=6)
    ttk.Button(account_buttons,text='重新读取 CC Switch 账号',command=read_accounts).pack(side='left',padx=(0,10))
    ttk.Button(account_buttons,text='上移',command=lambda:move_account(-1)).pack(side='left',padx=(0,10))
    ttk.Button(account_buttons,text='下移',command=lambda:move_account(1)).pack(side='left')
    app.after(100,read_accounts)
    enabled=tk.BooleanVar(value=values['enabled']); auto=tk.BooleanVar(value=values['auto_resume'])
    ttk.Checkbutton(settings,text='启用自动管理（插件栏开关同时有效）',variable=enabled,style='SolidSquare.TCheckbutton').grid(row=0,column=0,columnspan=3,sticky='w')
    ttk.Checkbutton(settings,text='额度恢复后自动继续被守护程序暂停的任务',variable=auto,style='SolidSquare.TCheckbutton').grid(row=1,column=0,columnspan=3,sticky='w',pady=(0,15))
    for col,label in enumerate(('额度窗口','暂停：剩余 ≤','恢复：剩余 >')):
        ttk.Label(settings,text=label,font=(FONT,10,'bold')).grid(row=2,column=col,sticky='w',padx=(0,24),pady=6)
    for row,label,suffix in ((3,'当前账号 5 小时','5h'),):
        ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=10)
        for col,prefix in ((1,'pause_'),(2,'resume_')):
            key=prefix+suffix; variables[key]=TextVar(value=str(values[key]))
            cell=ttk.Frame(settings); cell.grid(row=row,column=col,sticky='w',padx=(0,24))
            ttk.Spinbox(cell,from_=0,to=100,increment=1,width=9,textvariable=variables[key]).pack(side='left')
            ttk.Label(cell,text=' %').pack(side='left')
    ttk.Label(settings,text='当前账号周额度').grid(row=4,column=0,sticky='w',pady=10)
    ttk.Label(settings,text='耗尽（0%）时暂停并阻止恢复',foreground=MUTED).grid(row=4,column=1,columnspan=2,sticky='w')
    extra=[('checkpoint','提前保存进度阈值','%',0,100),('poll_seconds','固定检查间隔（备用）','秒',10,3600),
           ('cooldown_seconds','暂停后最短等待','秒',10,3600),('resume_spacing_seconds','任务恢复最小间隔','秒',2,300)]
    for row,(key,label,unit,lo,hi) in enumerate(extra,5):
        ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w',pady=9)
        variables[key]=TextVar(value=str(values[key]))
        cell=ttk.Frame(settings); cell.grid(row=row,column=1,columnspan=2,sticky='w')
        ttk.Spinbox(cell,from_=lo,to=hi,width=9,textvariable=variables[key]).pack(side='left'); ttk.Label(cell,text=' '+unit).pack(side='left')
    ttk.Label(settings,text='暂停与恢复阈值只针对当前账号的 5 小时额度；周额度只需未耗尽。\n切换账号后使用新账号额度，旧账号不参与判断。恢复前需连续两次查询达标。',
              foreground=MUTED,wraplength=740).grid(row=9,column=0,columnspan=3,sticky='w',pady=(14,4))
    notice=TextVar(value='设置保存后立即生效；调低恢复阈值可能使已暂停任务自动继续。')
    ttk.Label(settings,textvariable=notice,foreground=MUTED,wraplength=740).grid(row=10,column=0,columnspan=3,sticky='w',pady=8)
    def save():
        try:
            new=dict(load_settings(),enabled=enabled.get(),auto_resume=auto.get())
            new.update(selection_strategy=strategy.get())
            new.update(update_tracking=tracking.get(),auto_adapt=adapt.get())
            new['dynamic_poll']=dynamic.get()
            for key,var in poll_vars.items():new[key]=int(var.get()) if key.endswith('seconds') else float(var.get())
            if accounts_loaded: new['account_priority']=[row['account'] for row in account_rows]
            for key,var in variables.items(): new[key]=int(var.get()) if key.endswith('seconds') else float(var.get())
            new=validate_settings(new); atomic_json(data_dir()/'settings.json',new)
            ensure_service(); enqueue('check'); notice.set('已保存。监控程序将采用新阈值和账号选择规则。'); account_notice.set('账号选择规则已保存，手动和自动切换均使用此规则。')
        except Exception as exc: messagebox.showerror('设置未保存',str(exc),parent=app)
    buttons=ttk.Frame(settings); buttons.grid(row=11,column=0,columnspan=3,sticky='w',pady=8)
    ttk.Button(buttons,text='保存设置',command=save,style='Primary.TButton').pack(side='left',padx=(0,10))
    ttk.Button(account_buttons,text='保存设置',command=save).pack(side='left',padx=(14,0))
    ttk.Button(update_buttons,text='保存设置',command=save).pack(side='left',padx=(12,0))
    ttk.Button(polling_buttons,text='保存设置',command=save).pack(side='left')
    ttk.Button(buttons,text='立即检查额度',command=lambda:(ensure_service(),enqueue('check'))).pack(side='left')
    switch_events=queue.Queue()
    def manual_switch():
        switch_button.configure(state='disabled')
        notice.set('正在选择账号；之后会正常退出 Codex、换号并重开，请暂勿操作 CC Switch。')
        def worker():
            try:
                from manual_switch import switch_to_available
                result=switch_to_available(lambda text:switch_events.put(('progress',text)))
                switch_events.put(('done',result))
            except Exception as exc:
                switch_events.put(('error',str(exc) if isinstance(exc,(RuntimeError,ValueError)) else '切换未完成，请检查 CC Switch。'))
        threading.Thread(target=worker,daemon=True).start()
    main_actions=ttk.Frame(accounts);main_actions.pack(anchor='w',pady=6)
    switch_button=ttk.Button(main_actions,text='手动切换账号',command=manual_switch,style='Primary.TButton')
    switch_button.pack(side='left')
    ttk.Button(main_actions,text='立即检查额度',command=lambda:(ensure_service(),enqueue('check'))).pack(side='left',padx=10)
    refresh_cc_button=ttk.Button(main_actions,text='刷新 CC Switch',command=lambda:request_usage(manual=True),style='Setup.TButton')
    refresh_cc_button.pack(side='left')
    def switch_updates():
        try:
            while True:
                event,text=switch_events.get_nowait(); notice.set(text)
                if event in ('done','error'):
                    switch_button.configure(state='normal')
                    if event=='error': messagebox.showerror('账号切换未完成',text,parent=app)
        except queue.Empty: pass
        app.after(200,switch_updates)
    switch_updates()
    def close_window():
        if switch_button.instate(['disabled']):
            messagebox.showinfo('正在切换账号','请等待本次切换结束后再关闭设置。',parent=app)
        else: app.destroy()
    app.protocol('WM_DELETE_WINDOW',close_window)
    tree=ttk.Treeview(tasks,columns=('title','state','queue','time'),show='headings',selectmode='browse',height=6)
    for name,label,width in [('title','聊天',290),('state','状态',150),('queue','待执行队列',155),('time','记录时间',130)]:
        tree.heading(name,text=label); tree.column(name,width=width,minwidth=90)
    scroll=ttk.Scrollbar(tasks,orient='vertical',command=tree.yview); tree.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y'); tree.pack(fill='both',expand=True)
    detail=TextVar(value='右键聊天可添加或管理待执行队列。上一项确认结束、额度达标后才发送下一项。')
    ttk.Label(tasks,textvariable=detail,wraplength=740,foreground=MUTED).pack(anchor='w',pady=12)
    def show_detail(event=None):
        selected=tree.selection()
        if selected:
            state=read_json(data_dir()/'status.json',{})
            record=next((r for r in state.get('tasks',[]) if r['id']==selected[0]),{})
            detail.set(record.get('note') or '会话 ID：'+selected[0])
    tree.bind('<<TreeviewSelect>>',show_detail)
    def queue_thread(tid):
        state=read_json(data_dir()/'status.json',{})
        return next((t for t in state.get('threads',[]) if t['id']==tid),None) or next((t for t in state.get('tasks',[]) if t['id']==tid),None)
    def selected_queue(add=False):
        selection=tree.selection()
        if not selection: return
        from queue_ui import edit_task,open_queue
        callback=lambda:detail.set('队列已保存；右键可查看顺序、暂停队列或编辑尚未发送的任务。')
        (edit_task if add else open_queue)(app,selection[0],queue_thread,callback)
    context=tk.Menu(app,tearoff=False)
    context.add_command(label='添加待执行任务…',command=lambda:selected_queue(True))
    context.add_command(label='管理待执行队列…',command=selected_queue)
    def right_click(event):
        row=tree.identify_row(event.y)
        if row:
            tree.selection_set(row);tree.focus(row)
            try: context.tk_popup(event.x_root,event.y_root)
            finally: context.grab_release()
    tree.bind('<Button-3>',right_click)
    tree.bind('<Double-1>',lambda event:selected_queue())
    task_actions=ttk.Frame(tasks);task_actions.pack(fill='x')
    ttk.Button(task_actions,text='管理所选聊天的待执行队列',command=selected_queue).pack(side='left',padx=(0,10))
    def cancel():
        selection=tree.selection()
        if selection: enqueue('cancel_resume',thread=selection[0]); detail.set('已取消所选任务的自动恢复。')
    ttk.Button(task_actions,text='取消所选任务的自动恢复',command=cancel,style='Quiet.TButton').pack(side='left')
    ttk.Label(outer,text='关闭 Codex Auto Switch Assistant 插件后，后台会停止自动操作。\n设置和暂停记录保存在本机，不保存登录令牌。',
              foreground=MUTED,font=(FONT,9)).pack(anchor='w',pady=(6,0))
    phases={'paused':'因额度暂停','resumed':'已发送继续','cancelled':'已取消自动恢复','superseded':'已由用户接管',
            'needs_review':'需检查操作结果','pause_pending':'正在暂停','resume_pending':'正在恢复'}
    states={'disabled':'自动管理已关闭','connecting':'正在连接 Codex 桌面版','monitoring':'正在监控','incompatible':'桌面协议不兼容，已停止自动操作'}
    def refresh(schedule=True):
        nonlocal usage_inflight
        try:
            for key,entry in cached_usage().items():
                if entry.get('checked_at',0)>usage.get(key,{}).get('checked_at',0):usage[key]=entry
            while not usage_events.empty():
                rows,manual,total=usage_events.get_nowait()
                for entry in rows:usage[entry['id']]=entry
                usage_inflight=False
                refresh_cc_button.state(['!disabled'])
                if manual:
                    good=sum('quota' in entry and not entry.get('error') for entry in rows)
                    if len(rows)<total:refresh_notice.set('刷新未完成：CC Switch 正忙或正在换号，请稍后重试。')
                    elif good==total:refresh_notice.set(f'CC Switch 刷新完成：{good} 个账号额度已更新。')
                    else:refresh_notice.set(f'已刷新 {good}/{total} 个账号；其余额度仍查询失败，请确认 VPN 已连接，再次点击刷新。')
            if accounts_loaded and time.time()>=usage_due:read_accounts()
            render_accounts()
            saved=load_settings();wait_reset.set(saved['wait_for_reset']);wait_auto.set(saved['wait_auto_resume'])
            auto_switch.set(load_settings()['auto_switch'])
            s=read_json(data_dir()/'status.json',{}); alive=time.time()-s.get('heartbeat',0)<15
            compatibility=read_json(data_dir()/'compatibility.json',{})
            installed=compatibility.get('installed',{})
            versions=installed.get('codex_installed',[])
            lines=['Codex 已安装：'+('、'.join(v.get('version') or '未知' for v in versions) or '待检测'),
                   'Codex 正在运行：'+(installed.get('codex_running') or {}).get('version','未检测到'),
                   '额度查询 CLI：'+installed.get('cli',{}).get('version','待检测'),
                   'CC Switch：'+('、'.join(v.get('version') or '未知' for v in installed.get('cc_switch',[])) or '未运行或待检测')]
            protocol='不兼容，停止任务控制' if s.get('state')=='incompatible' else {'compatible':'快照协议匹配；操作时继续核验','incompatible':'不兼容，停止任务控制','waiting':'等待桌面连接或聊天快照'}.get(compatibility.get('desktop_protocol'),'待检查')
            lines.extend(['','桌面协议：'+protocol,'CC Switch 界面：'+('结构检查通过；换号前仍会复核' if compatibility.get('cc_ui',{}).get('state')=='compatible' else '尚未确认，请检查 Codex 供应商页面')])
            for key,label in [('codex','Codex 最新公告'),('cc_switch','CC Switch 最新正式版')]:
                release=compatibility.get('releases',{}).get(key,{})
                lines.append(label+'：'+release.get('title','待查询')+('（查询失败，可能为旧缓存）' if release.get('error') else ''))
            if compatibility.get('checked_at'): lines.append('最近本机检查：'+time.strftime('%m-%d %H:%M:%S',time.localtime(compatibility['checked_at'])))
            if compatibility.get('error'): lines.append(compatibility['error'])
            if not load_settings()['update_tracking']: lines.append('版本跟踪已关闭')
            update_status.set('\n'.join(lines))
            plan=read_json(data_dir()/'reset-wait.json',{})
            wait_lines=[plan.get('message','尚无等待计划')]
            if plan.get('target'): wait_lines.append('等待账号：'+plan['target']['name'])
            if plan.get('wake_at'): wait_lines.append('自然重置恢复时间：'+time.strftime('%m-%d %H:%M:%S',time.localtime(plan['wake_at'])))
            if plan.get('last_check'): wait_lines.append('最近监测：'+time.strftime('%m-%d %H:%M:%S',time.localtime(plan['last_check'])))
            wait_status.set('\n'.join(wait_lines))
            switch_result=read_json(data_dir()/'manual-switch.json',{})
            switch_message=(plan.get('message','') if switch_result.get('plan_id')==plan.get('id') and plan.get('id') else switch_result.get('message',''))
            switch_status.set(switch_message)
            if switch_result.get('plan_id')==plan.get('id') and plan.get('id'):
                notice.set(switch_message)
            label=states.get(s.get('state'),'后台尚未启动') if alive else '后台未运行：点击“立即检查额度”启动'
            if not plugin_enabled(): label='插件已关闭或尚未安装；可打开设置，但不会自动操作任务'
            status_dot.configure(foreground='#16834a' if alive and s.get('state')=='monitoring' and plugin_enabled() and not s.get('error') else MUTED)
            status_text.set(label+(' · '+s['error'] if s.get('error') else '')+(f" · 当前检查间隔 {s['poll_interval']} 秒" if s.get('poll_interval') else ''))
            w=(s.get('quota') or {}).get('windows',{})
            def remain(k): return f"{w[k]['remaining']:.1f}%" if k in w else '未知'
            quota_text.set(f'5 小时剩余 {remain("5h")}       周剩余 {remain("week")}')
            for key in quota_values:
                quota_values[key].set(remain(key))
                quota_bars[key].set(w.get(key,{}).get('remaining',0))
            def stamp(v): return time.strftime('%m-%d %H:%M',time.localtime(v)) if v else '—'
            reset_text.set(f'重置时间：5 小时 {stamp(w.get("5h",{}).get("resets_at"))}  ·  周 {stamp(w.get("week",{}).get("resets_at"))}  |  最近查询 {stamp((s.get("quota") or {}).get("checked_at"))}')
            from task_queue import QueueStore
            queues=QueueStore().read()['threads']
            def queue_label(tid):
                q=queues.get(tid,{})
                count=sum(i['phase']=='queued' for i in q.get('items',[]))
                running=any(i['phase'] in ('running','send_pending') for i in q.get('items',[]))
                return ('暂停 · ' if q.get('enabled') is False else '')+f'待 {count} 项'+(' · 执行中' if running else '') if q else '—'
            rows={r['id']:(r.get('title',r['id']),phases.get(r['phase'],r['phase']),queue_label(r['id']),stamp(r.get('operation_at'))) for r in s.get('tasks',[])}
            for t in s.get('threads',[]):
                if t.get('is_child') or t.get('ephemeral'): continue
                state='等待用户' if t.get('waiting') else {'inProgress':'运行中','completed':'本轮已结束','interrupted':'已中断','failed':'执行失败'}.get(t.get('status'),'等待任务')
                rows[t['id']]=(t.get('title',t['id']),state,queue_label(t['id']),rows.get(t['id'],('','','','—'))[3])
            for tid,q in queues.items():
                if tid not in rows: rows[tid]=(q.get('title',tid),'等待打开原聊天',queue_label(tid),stamp(q.get('updated_at')))
            for item in tree.get_children():
                if item not in rows: tree.delete(item)
            for tid,row in rows.items():
                row=(row[0],tr(row[1]),tr(row[2]),row[3])
                if tree.exists(tid): tree.item(tid,values=row)
                else: tree.insert('',tk.END,iid=tid,values=row)
        except Exception: status_text.set('状态暂时不可用；稍后自动重试')
        if schedule:app.after(2000,refresh)
    localize(app);language_button.configure(text='English' if language()=='zh' else '中文')
    refresh(); app.mainloop()
