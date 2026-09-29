"""Quota policy and durable operation journal. No network or model calls here."""
from __future__ import annotations
import json, math, os, time, uuid, threading, copy
from pathlib import Path

DEFAULTS = {
    'enabled': True, 'auto_resume': True, 'pause_5h': 1.0, 'resume_5h': 10.0,
    'checkpoint': 5.0,
    'poll_seconds': 60, 'cooldown_seconds': 60, 'resume_spacing_seconds': 10,
    'limit_id': 'codex', 'excluded_threads': [],
    'selection_strategy': 'quota', 'account_priority': [], 'auto_switch': False,
    'wait_for_reset': True, 'wait_auto_resume': True,
    'update_tracking': True, 'auto_adapt': True,
    'dynamic_poll': True, 'poll_high_seconds': 180, 'poll_mid_below': 20.0,
    'poll_mid_seconds': 60, 'poll_low_below': 10.0, 'poll_low_seconds': 30,
}

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default

def validate_settings(values):
    if not isinstance(values, dict): raise ValueError('设置必须是对象')
    # Accept old settings files, but never apply their weekly percentage gates.
    unknown = set(values) - set(DEFAULTS) - {'pause_week', 'resume_week'}
    if unknown: raise ValueError('未知设置：' + ', '.join(sorted(unknown)))
    out = dict(DEFAULTS); out.update({k: v for k, v in values.items() if k in DEFAULTS})
    for name in ('enabled', 'auto_resume', 'auto_switch', 'wait_for_reset', 'wait_auto_resume', 'update_tracking', 'auto_adapt', 'dynamic_poll'):
        if type(out[name]) is not bool: raise ValueError(name + ' 必须为布尔值')
    for name in ('pause_5h', 'resume_5h', 'checkpoint', 'poll_mid_below', 'poll_low_below'):
        v = out[name]
        if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100:
            raise ValueError('阈值必须为 0–100 之间的数字')
    for suffix in ('5h',):
        if out['resume_' + suffix] <= out['pause_' + suffix]:
            raise ValueError('恢复阈值必须高于暂停阈值')
        if out['resume_' + suffix] >= 100:
            raise ValueError('恢复条件为严格大于，恢复阈值必须小于 100%')
    if out['checkpoint'] < out['pause_5h']:
        raise ValueError('提前保存阈值不能低于暂停阈值')
    if not 0<out['poll_low_below']<out['poll_mid_below']<=100:
        raise ValueError('动态监控分界须满足 0 < 低额度分界 < 中额度分界 ≤ 100')
    for name, lo, hi in (('poll_seconds', 10, 3600), ('cooldown_seconds', 10, 3600), ('resume_spacing_seconds', 2, 300)):
        if type(out[name]) is not int or not lo <= out[name] <= hi:
            raise ValueError(f'{name} 必须是 {lo}–{hi} 之间的整数')
    for name in ('poll_high_seconds','poll_mid_seconds','poll_low_seconds'):
        if type(out[name]) is not int or not 10<=out[name]<=3600: raise ValueError('动态检查间隔必须为 10–3600 秒的整数')
    if not out['poll_low_seconds']<=out['poll_mid_seconds']<=out['poll_high_seconds']:
        raise ValueError('额度越低，检查间隔应越短或保持相同')
    if not isinstance(out['limit_id'], str) or not out['limit_id'].strip(): raise ValueError('额度桶不能为空')
    if not isinstance(out['excluded_threads'], list) or any(not isinstance(x, str) for x in out['excluded_threads']):
        raise ValueError('排除列表格式错误')
    if out['selection_strategy'] not in ('quota','priority'): raise ValueError('未知账号选择方式')
    priorities=out['account_priority']
    if not isinstance(priorities,list) or any(not isinstance(x,str) or not x for x in priorities) or len(set(priorities))!=len(priorities):
        raise ValueError('账号优先级必须是不重复的账号 ID 列表')
    return out

def normalize_quota(result, limit_id='codex', now=None):
    now = time.time() if now is None else now
    buckets = result.get('rateLimitsByLimitId') or {}
    bucket = buckets.get(limit_id)
    if bucket is None:
        single = result.get('rateLimits') or {}
        if single.get('limitId') in (None, limit_id): bucket = single
    if not isinstance(bucket, dict): raise ValueError('未返回指定额度桶')
    windows = {}
    for value in (bucket.get('primary'), bucket.get('secondary')):
        if not isinstance(value, dict): continue
        duration = value.get('windowDurationMins')
        key = {300: '5h', 10080: 'week'}.get(duration)
        if key is None: continue
        used, reset = value.get('usedPercent'), value.get('resetsAt')
        if type(used) not in (float, int) or not math.isfinite(used) or not 0 <= used <= 100:
            raise ValueError('额度百分比无效')
        if type(reset) not in (float, int) or not math.isfinite(reset) or reset <= now:
            raise ValueError('额度窗口已过期，等待重新查询')
        windows[key] = {'remaining': 100-used, 'resets_at': reset}
    return {'windows': windows, 'checked_at': now, 'limit_id': limit_id}

def poll_interval(quota,settings,now=None):
    now=time.time() if now is None else now
    if not settings.get('dynamic_poll',True): return settings['poll_seconds']
    windows=(quota or {}).get('windows',{})
    five=windows.get('5h',{});week=windows.get('week',{})
    low=settings.get('poll_low_seconds',30)
    if not five or five.get('resets_at',0)<=now or not week or week.get('resets_at',0)<=now or week.get('remaining',0)<=0:
        return low
    remaining=five.get('remaining',0)
    interval=(low if remaining<settings.get('poll_low_below',10) or remaining<=settings['pause_5h'] else
              settings.get('poll_mid_seconds',60) if remaining<settings.get('poll_mid_below',20) else settings.get('poll_high_seconds',180))
    if now-(quota or {}).get('checked_at',0)>max(30,interval*2): return low
    return interval


def quota_decision(quota, settings, now=None):
    now = time.time() if now is None else now
    if not quota or now - quota.get('checked_at', 0) > max(30, poll_interval(quota,settings,now) * 2): return 'unknown'
    windows = quota.get('windows', {})
    valid = {k: v for k, v in windows.items() if v.get('resets_at', 0) > now}
    if '5h' in valid and valid['5h']['remaining'] <= settings['pause_5h']: return 'pause'
    if 'week' in valid and valid['week']['remaining'] <= 0: return 'pause'
    if not all(k in valid for k in ('5h', 'week')): return 'unknown'
    if valid['5h']['remaining'] > settings['resume_5h']: return 'resume'
    if valid['5h']['remaining'] <= settings['checkpoint']: return 'checkpoint'
    return 'hold'

def is_active(thread):
    return (thread.get('status') == 'inProgress' and bool(thread.get('turn_id'))
            and thread.get('runtime_status') == 'active' and not thread.get('waiting')
            and not thread.get('is_child') and not thread.get('ephemeral'))

def resume_eligible(record, thread):
    return (record.get('phase') == 'paused' and record.get('reason') == 'quota'
            and thread.get('status') == 'interrupted' and thread.get('turn_id') == record.get('turn_id')
            and thread.get('runtime_status') in ('idle', 'notLoaded') and not thread.get('waiting')
            and thread.get('owner') and not thread.get('is_child'))

class Journal:
    def __init__(self, path):
        self.lock=threading.RLock()
        self.path = Path(path)
        self.data = read_json(path, {'version': 1, 'tasks': {}})
        if self.data.get('version') != 1 or not isinstance(self.data.get('tasks'), dict): raise ValueError('任务记录格式不兼容')

    def save(self):
        with self.lock: atomic_json(self.path, self.data)

    def records(self):
        with self.lock: return copy.deepcopy(self.data['tasks'])

    def intent(self, thread, action, account):
        tid = thread['id']; old = self.data['tasks'].get(tid, {})
        record = dict(old) if action == 'resume' else {
            'id': tid, 'turn_id': thread['turn_id'], 'title': thread.get('title', tid),
            'cwd': thread.get('cwd'), 'reason': 'quota', 'paused_account': account,
            'paused_at': time.time(),
        }
        record.update(phase=action + '_pending', operation_id=uuid.uuid4().hex, operation_at=time.time())
        self.data['tasks'][tid] = record; self.save()
        return record

    def mark(self, tid, phase, **kwargs):
        with self.lock:
            self.data['tasks'][tid].update(phase=phase, **kwargs); self.save()

    def recover_uncertain(self):
        # Never resend a start-turn whose result was lost; it may already have run.
        for record in self.data['tasks'].values():
            if record.get('phase') in ('resume_pending', 'pause_pending'):
                record['phase'] = 'needs_review'
                record['note'] = '上次操作结果未确认；为避免重复执行，需检查后手动处理'
        self.save()
