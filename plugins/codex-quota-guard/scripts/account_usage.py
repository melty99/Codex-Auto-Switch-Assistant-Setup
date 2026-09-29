"""Fresh CC Switch quota readings; never activate an account."""
import math
import re
import time
from core import read_json
from runtime import data_dir


def reset_times(snapshot,checked_at=None):
    """Display-only estimates, bounded to each quota label's own segment."""
    if snapshot.get('queryError') or snapshot.get('refreshing') or snapshot.get('quotaReady') is False:return {}
    checked_at=time.time() if checked_at is None else checked_at
    texts=[str(t).strip() for t in snapshot.get('texts',[])]
    result={}
    labels={'5小时':('5h',5*3600),'7天':('week',7*86400)}
    positions=[(i,t.rstrip(':：').strip()) for i,t in enumerate(texts) if t.rstrip(':：').strip() in labels]
    for offset,(start,label) in enumerate(positions):
        if sum(name==label for _,name in positions)!=1:continue
        end=positions[offset+1][0] if offset+1<len(positions) else len(texts)
        values=[]
        for text in texts[start+1:end]:
            match=re.fullmatch(r'(?:(\d+)d(\d+)h|(?:(\d+)h)?(\d+)m)',text)
            if not match:continue
            days,hours,long_hours,minutes=(int(v or 0) for v in match.groups())
            if (match[1] is not None and hours>=24) or minutes>=60:continue
            seconds=days*86400+(hours+long_hours)*3600+minutes*60
            key,maximum=labels[label]
            if seconds<=maximum:values.append(checked_at+seconds)
        if len(values)==1:result[labels[label][0]]=values[0]
    return result


def format_reset_time(entry,key,now=None):
    from i18n import tr
    value=entry.get('resets_at',{}).get(key)
    if entry.get('error') or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:return '—'
    seconds=value-(time.time() if now is None else now)
    if seconds<=0:return tr('待刷新')
    unit,divisor=('小时',3600) if key=='5h' else ('天',86400)
    return ('≈' if entry.get('reset_estimated',True) else '')+f'{seconds/divisor:.1f} '+tr(unit)


def cached_usage():
    return {r['id']:dict(r,cached=True) for r in
            read_json(data_dir()/'account-observations.json',{}).get('accounts',[]) if r.get('id')}


def read_usage(providers,retry_errors=False):
    from manual_switch import ui_request,remaining_from_card
    from reset_recovery import ACTIVE_PHASES
    import msvcrt
    results=[]
    for provider in providers:
        # The lifecycle controller owns CC Switch while a recovery plan is active.
        busy=ACTIVE_PHASES-{'needs_review'}
        if read_json(data_dir()/'reset-wait.json',{}).get('phase') in busy:break
        directory=data_dir();directory.mkdir(parents=True,exist_ok=True)
        handle=(directory/'manual-switch.lock').open('a+b')
        handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:handle.close();break
        try:
            if read_json(directory/'reset-wait.json',{}).get('phase') in busy:break
            for attempt in range(2 if retry_errors else 1):
                try:
                    snapshot=ui_request('refresh',provider)
                    quota=remaining_from_card(snapshot,conservative=False)
                    break
                except Exception:
                    if not retry_errors or attempt==1:raise
                    time.sleep(1)
                    if read_json(directory/'reset-wait.json',{}).get('phase') in busy:raise RuntimeError('换号流程已开始')
            checked_at=time.time()
            results.append(dict(id=provider['id'],quota=quota,resets_at=reset_times(snapshot,checked_at),reset_estimated=True,checked_at=checked_at,cached=False))
        except Exception as exc:
            results.append(dict(id=provider['id'],error='无法读取额度',error_type=type(exc).__name__,checked_at=time.time()))
        finally:handle.close()
    return results
