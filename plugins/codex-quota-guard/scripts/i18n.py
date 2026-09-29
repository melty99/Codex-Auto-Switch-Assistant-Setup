"""Local UI translations. User account names and task text are never translated."""
import json,re,string,weakref
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox as native_messagebox
from core import atomic_json,read_json

_language='zh';_variables=weakref.WeakSet()
_catalog=json.loads(Path(__file__).with_name('locales').joinpath('en.json').read_text(encoding='utf-8'))
_patterns=[]
for source,target in _catalog.items():
    if '{' not in source:continue
    try:
        parts=list(string.Formatter().parse(source))
        pattern='';fields=[]
        for literal,field,spec,conversion in parts:
            pattern+=re.escape(literal)
            if field is not None:pattern+='(.*?)';fields.append(field)
        _patterns.append((re.compile('^'+pattern+'$',re.S),fields,target))
    except ValueError:pass

def language():return _language
def load_language():
    from runtime import data_dir
    global _language
    _language=read_json(data_dir()/'ui.json',{}).get('language','zh')
    if _language not in ('zh','en'):_language='zh'
    return _language
def tr(text):
    if _language!='en' or not isinstance(text,str):return text
    if text in _catalog:return _catalog[text]
    for pattern,fields,target in _patterns:
        match=pattern.fullmatch(text)
        if match:
            try:return target.format(**dict(zip(fields,match.groups())))
            except (KeyError,ValueError):pass
    # Compose status messages from translated pieces; leave unknown diagnostics
    # and user-supplied text intact instead of inventing a translation.
    sources=sorted((k for k in _catalog if '{' not in k and k),key=len,reverse=True)
    return re.sub('|'.join(re.escape(k) for k in sources),lambda match:_catalog[match.group()],text)
class TextVar(tk.StringVar):
    def __init__(self,*args,**kwargs):
        self.source=kwargs.get('value','');super().__init__(*args,**kwargs);_variables.add(self)
        self.set(self.source)
    __hash__=object.__hash__
    def set(self,value):self.source=value;self.display=tr(value);super().set(self.display)
    def render(self):
        current=super().get()
        if current!=str(self.display):self.source=current
        self.display=tr(self.source);super().set(self.display)

def localize(root):
    """Refresh static widget text in place, preserving all form values."""
    def apply(widget):
        originals=getattr(widget,'_translation_text',{})
        if isinstance(widget,(tk.Tk,tk.Toplevel)):
            originals.setdefault('title',widget.title());widget.title(tr(originals['title']))
        keys=widget.keys()
        if 'text' in keys and ('textvariable' not in keys or not widget.cget('textvariable')):
            originals.setdefault('text',widget.cget('text'));widget.configure(text=tr(originals['text']))
        if isinstance(widget,ttk.Notebook):
            for tab in widget.tabs():
                key='tab:'+tab;originals.setdefault(key,widget.tab(tab,'text'));widget.tab(tab,text=tr(originals[key]))
        if isinstance(widget,ttk.Treeview):
            for col in widget['columns']:
                key='heading:'+col;originals.setdefault(key,widget.heading(col,'text'));widget.heading(col,text=tr(originals[key]))
        if isinstance(widget,tk.Menu):
            for index in range((widget.index('end') or 0)+1):
                try:
                    key='menu:'+str(index);originals.setdefault(key,widget.entrycget(index,'label'))
                    widget.entryconfigure(index,label=tr(originals[key]))
                except tk.TclError:pass
        widget._translation_text=originals
        for child in widget.winfo_children():apply(child)
    apply(root)
    for var in list(_variables):
        try:var.render()
        except tk.TclError:pass
def set_language(value,root):
    from runtime import data_dir
    global _language
    prefs=read_json(data_dir()/'ui.json',{});prefs['language']=value
    atomic_json(data_dir()/'ui.json',prefs);_language=value;localize(root)
class MessageBox:
    def __getattr__(self,name):
        def call(title,message,**kwargs):return getattr(native_messagebox,name)(tr(title),tr(message),**kwargs)
        return call
messagebox=MessageBox()
