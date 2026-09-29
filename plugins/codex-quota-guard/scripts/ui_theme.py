"""Light, quiet desktop styling with native ttk keyboard/accessibility behavior."""
import math
import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

BG='#ffffff'
INK='#202123'
MUTED='#707477'
SOFT='#f5f5f4'
LINE='#e7e7e5'
FONT='Microsoft YaHei'

def rounded_image(app,fill,outline,size=28,radius=9):
    image=tk.PhotoImage(master=app,width=size,height=size)
    for y in range(size):
        dy=max(0,radius-y-.5,y-(size-radius)+.5)
        inset=math.ceil(radius-math.sqrt(max(0,radius*radius-dy*dy))) if dy else 0
        image.put(outline,to=(inset,y,size-inset,y+1))
        if 0<y<size-1:image.put(fill,to=(inset+1,y,size-inset-1,y+1))
    return image

def install_theme(app):
    app.configure(bg=BG)
    app.option_add('*Menu.font',(FONT,10))
    for name in tkfont.names(app):
        tkfont.nametofont(name,root=app).configure(family=FONT)
    style=ttk.Style(app);style.theme_use('clam')
    style.configure('.',font=(FONT,10),background=BG,foreground=INK,bordercolor=LINE,
                    lightcolor=BG,darkcolor=BG,troughcolor=SOFT)
    style.configure('TFrame',background=BG)
    style.configure('TLabel',background=BG)
    style.configure('TLabelframe',background=BG,borderwidth=0,relief='flat')
    style.configure('TLabelframe.Label',font=(FONT,12,'bold'),foreground=INK,background=BG)
    style.configure('TCheckbutton',padding=(0,5),background=BG)
    style.configure('TRadiobutton',padding=(0,4),background=BG)
    for cls in ('TCheckbutton','TRadiobutton'):
        style.map(cls,background=[('active',BG)],foreground=[('disabled','#999b9c')])
    style.configure('TEntry',fieldbackground=BG,padding=(9,7),bordercolor=LINE,insertcolor=INK)
    style.configure('TSpinbox',fieldbackground=BG,padding=(9,7),bordercolor=LINE,arrowsize=12)
    style.map('TSpinbox',bordercolor=[('focus','#9d9f9f')],lightcolor=[('focus',BG)],darkcolor=[('focus',BG)])
    style.configure('Treeview',rowheight=36,background=BG,fieldbackground=BG,borderwidth=0,relief='flat')
    style.layout('Treeview',[('Treeview.treearea',{'sticky':'nswe'})])
    style.configure('Treeview.Heading',font=(FONT,9),foreground=MUTED,background=SOFT,
                    relief='flat',borderwidth=0,padding=(12,9))
    style.map('Treeview',background=[('selected','#ededeb')],foreground=[('selected',INK)])
    style.map('Treeview.Heading',background=[('active','#eeeeec')])
    style.configure('TNotebook',background=BG,borderwidth=0,tabmargins=(0,0,0,12))
    app._theme_images=[]
    tab_images=[rounded_image(app,c,LINE) for c in (BG,SOFT,'#fafafa')]
    app._theme_images.extend(tab_images)
    client_image=rounded_image(app,BG,BG);app._theme_images.append(client_image)
    style.element_create('Assistant.client','image',client_image,border=0,sticky='nswe')
    style.layout('TNotebook',[('Assistant.client',{'sticky':'nswe'})])
    style.element_create('Assistant.tab','image',tab_images[0],('selected',tab_images[1]),('active',tab_images[2]),border=9,sticky='nswe')
    style.configure('TNotebook.Tab',padding=(16,3),background=BG,foreground=MUTED,borderwidth=0)
    style.map('TNotebook.Tab',background=[('selected',SOFT),('active','#fafafa')],
              foreground=[('selected',INK),('active',INK)])
    style.layout('TNotebook.Tab',[('Assistant.tab',{'sticky':'nswe','children':[
        ('Notebook.padding',{'side':'top','sticky':'nswe','children':[
            ('Notebook.label',{'side':'top','sticky':''})]})]})])
    style.configure('Vertical.TScrollbar',background='#dededb',troughcolor=BG,borderwidth=0,
                    arrowsize=11,gripcount=0,relief='flat')
    style.layout('Vertical.TScrollbar',[('Vertical.Scrollbar.trough',{'sticky':'ns','children':[
        ('Vertical.Scrollbar.thumb',{'expand':'1','sticky':'nswe'})]})])
    for name,normal,hover,pressed,text,border in [
        ('TButton',BG,SOFT,'#ebebea',INK,LINE),
        ('Primary.TButton',INK,'#393a3c','#101112',BG,INK),
        ('Setup.TButton','#eaf4ff','#dcecff','#cce3ff','#285f91','#dcecff'),
        ('Danger.TButton','#fff2f1','#ffe5e2','#ffd5d0','#b33a32','#f4dedb'),
        ('Quiet.TButton',BG,SOFT,'#ebebea',MUTED,BG)]:
        states=[rounded_image(app,color,border if name!='Quiet.TButton' else color) for color in (normal,hover,pressed)]
        disabled=rounded_image(app,SOFT,LINE)
        focused=rounded_image(app,normal,'#16834a')
        app._theme_images.extend([*states,disabled,focused])
        element=name+'.rounded'
        style.element_create(element,'image',states[0],('disabled',disabled),('pressed',states[2]),('focus',focused),('active',states[1]),border=10,sticky='nswe')
        style.layout(name,[(element,{'sticky':'nswe','children':[
            ('Button.padding',{'sticky':'nswe','children':[
                ('Button.label',{'sticky':'nswe'})]})]})])
        style.configure(name,padding=(8,0),foreground=text,background=BG,anchor='center',borderwidth=0)
        style.map(name,foreground=[('disabled','#999b9c')],background=[('disabled',BG),('active',BG),('pressed',BG)])
    style.configure('Quota.TFrame',background=SOFT)
    style.configure('Quota.TLabel',background=SOFT,foreground=MUTED,font=(FONT,9))
    style.configure('QuotaValue.TLabel',background=SOFT,foreground=INK,font=(FONT,22,'bold'))
    style.configure('Quota.Horizontal.TProgressbar',background=INK,troughcolor='#e4e4e1',
                    borderwidth=0,lightcolor=INK,darkcolor=INK,thickness=3)
    return style

class Section(ttk.Frame):
    """Borderless settings section with a clearly separated heading."""
    def __init__(self,parent,text,padding=18):
        super().__init__(parent,padding=(0,0,0,0))
        tk.Frame(self,height=1,bg=LINE).pack(fill='x',pady=(0,17))
        ttk.Label(self,text=text,font=(FONT,12,'bold')).pack(anchor='w',pady=(0,14))
        self.body=ttk.Frame(self,padding=(0,0,0,12));self.body.pack(fill='both',expand=True)

class QuotaBar(tk.Canvas):
    def __init__(self,parent):
        super().__init__(parent,height=3,bg='#e4e4e1',highlightthickness=0)
        self.value=0
        self.bind('<Configure>',self.draw)
    def set(self,value):
        self.value=max(0,min(100,value));self.draw()
    def draw(self,event=None):
        self.delete('all')
        self.create_rectangle(0,0,self.winfo_width()*self.value/100,3,fill=INK,outline='')
