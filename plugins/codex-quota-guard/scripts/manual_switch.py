"""Manual, explicit account switch through CC Switch's own accessible buttons."""
from __future__ import annotations
import base64
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid
from core import atomic_json, read_json
from runtime import codex_home, cc_switch_dir, data_dir, hidden_flags, load_settings, plugin_enabled
from reset_recovery import countdown_reset,earliest_reset,request_wait,request_restart,ACTIVE_PHASES


def provider_inventory():
    directory = cc_switch_dir()
    settings = read_json(directory/'settings.json', {})
    target_home = Path(settings.get('codexConfigDir') or Path.home()/'.codex').resolve()
    if target_home != codex_home().resolve():
        raise RuntimeError('CC Switch 管理的 Codex 目录与当前监控目录不一致；未切换。')
    current = settings.get('currentProviderCodex')
    with sqlite3.connect((directory/'cc-switch.db').as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute("SELECT id,name,meta FROM providers WHERE app_type='codex'").fetchall()
    providers = []
    for pid, name, metadata in rows:
        binding = json.loads(metadata or '{}').get('authBinding') or {}
        if binding.get('source') == 'managed_account' and binding.get('authProvider') == 'codex_oauth' and binding.get('accountId'):
            providers.append(dict(id=pid, name=name, account=binding['accountId']))
    active = next((p for p in providers if p['id'] == current), None)
    if not active:
        raise RuntimeError('当前供应商不是 CC Switch 托管的 ChatGPT 账号；未切换。')
    names = [p['name'] for p in providers]
    if len(names) != len(set(names)):
        raise RuntimeError('账号卡片存在重名，请先在 CC Switch 设置不同名称。')
    return active, providers


def ui_request(action, provider, **kwargs):
    request = dict(action=action, name=provider['name'], autoAdapt=load_settings()['auto_adapt'],
                   executable=read_json(data_dir()/'installation.json',{}).get('cc_switch_exe'),**kwargs)
    script = (Path(__file__).with_name('window_access.ps1').read_text(encoding='utf-8')+'\n'+
              Path(__file__).with_name('cc_switch_ui.ps1').read_text(encoding='utf-8'))
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    shell = Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            env=dict(os.environ, QUOTA_GUARD_CC_REQUEST=json.dumps(request, ensure_ascii=True),
                                     QUOTA_GUARD_REFRESH_SOURCE=str(Path(__file__).with_name('quiet_refresh.cs'))),
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90 if action=='refresh' else 50,
                            creationflags=hidden_flags())
    try:
        response = json.loads(result.stdout.decode('utf-8-sig').strip())
    except (ValueError, UnicodeError):
        raise RuntimeError('无法读取 CC Switch 界面；请确认其已启动并打开 Codex 供应商页面。') from None
    if not response.get('ok'):
        raise RuntimeError(response.get('error', 'CC Switch 操作失败'))
    return response['result']


def remaining_from_card(snapshot,require_fresh=True,conservative=True):
    """UI is rounded *used* percentage; use its conservative remaining bound."""
    if snapshot.get('refreshing'):
        raise ValueError('额度仍在刷新')
    if snapshot.get('queryError') or snapshot.get('quotaReady') is False:
        raise ValueError('额度查询失败，请刷新 CC Switch。')
    texts = snapshot.get('texts', [])
    if require_fresh and '刚刚' not in texts:
        raise ValueError('额度不是最近一分钟的查询结果')
    quota = {}
    for label, key in [('5小时', '5h'), ('7天', 'week')]:
        indices = [i for i, text in enumerate(texts) if text.rstrip(':：').strip() == label]
        if len(indices) != 1:
            raise ValueError('未识别五小时和周额度')
        i = indices[0]
        tail = texts[i+1:i+4]
        percentages = [v for v in tail if re.fullmatch(r'\d+(?:\.\d+)?%', v)]
        if len(percentages) != 1:
            raise ValueError('额度百分比格式不明确')
        used = float(percentages[0][:-1])
        if not math.isfinite(used) or not 0 <= used <= 100:
            raise ValueError('额度百分比无效')
        quota[key] = max(0.0, 100-used-(0.5 if conservative else 0))
    return quota


def ordered_accounts(providers, priorities):
    unique={}
    for p in providers: unique.setdefault(p['account'],p)
    rank={key:i for i,key in enumerate(priorities)}
    return sorted(unique.values(),key=lambda p:(rank.get(p['account'],len(rank)),p['name'].casefold(),p['account']))


def select_candidate(candidates, resume_threshold, strategy='quota', priorities=()):
    eligible = [c for c in candidates if c['quota']['5h'] > resume_threshold and c['quota']['week'] > 0]
    if strategy=='priority':
        return next(iter(ordered_accounts(eligible,priorities)),None)
    if strategy!='quota': raise ValueError('未知账号选择方式')
    # Geometric mean gives equal weight to both remaining percentages.
    return max(eligible, key=lambda c: (c['quota']['5h']*c['quota']['week'], min(c['quota'].values()), c['quota']['5h'], c['id'])) if eligible else None


def switch_to_available(progress=lambda text: None, automatic=False, allowed=lambda: True):
    prior=read_json(data_dir()/'reset-wait.json',{})
    retry_review=prior.get('phase')=='needs_review' and not automatic
    if prior.get('phase') in ACTIVE_PHASES and not retry_review:
        raise RuntimeError('已有等待重置计划，请先在“等待与监测”页取消计划再手动切换。')
    def check_permission():
        settings=load_settings()
        if not plugin_enabled() or not settings['enabled'] or not settings['auto_resume']:
            raise RuntimeError('退出换号流程需要启用插件、自动管理和自动继续；未直接切换账号。')
        if automatic and (not allowed() or not plugin_enabled() or not load_settings()['enabled'] or not load_settings()['auto_switch']):
            raise RuntimeError('自动切换已关闭或任务状态变化，已取消。')
    check_permission()
    import msvcrt
    directory = data_dir(); directory.mkdir(parents=True, exist_ok=True)
    handle = (directory/'manual-switch.lock').open('a+b')
    handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
    deadline=time.monotonic()+55
    while True:
        try:msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1);break
        except OSError:
            recent=read_json(directory/'manual-switch.json',{})
            if recent.get('phase') in ('scanning','switching') and time.time()-recent.get('updated_at',0)<300:
                handle.close();raise RuntimeError('已有一次账号切换正在进行，请等待结果。') from None
            if time.monotonic()>=deadline:
                handle.close();raise RuntimeError('账号额度刷新仍在进行，请稍后切换。') from None
            time.sleep(.1)
    state_path = directory/'manual-switch.json'
    def report(phase, text):
        plan_id=read_json(directory/'reset-wait.json',{}).get('id') if phase in ('restart_requested','waiting_reset') else None
        atomic_json(state_path, dict(phase=phase, updated_at=time.time(), message=text, automatic=automatic,plan_id=plan_id))
        progress(text)
    try:
        check_permission()
        current_plan=read_json(directory/'reset-wait.json',{})
        if current_plan.get('phase') in ACTIVE_PHASES and (not retry_review or current_plan.get('id')!=prior.get('id') or current_plan.get('phase')!='needs_review'):
            raise RuntimeError('已有换号计划正在处理，请等待完成。')
        if retry_review:
            # A new explicit click may replace a pre-switch exit failure only
            # after verifying the app is open. Replan from the current account;
            # an external account change must never replay the old target.
            from desktop_lifecycle import desktop_instance,remove_wake
            active_now,_=provider_inventory()
            if (not desktop_instance() or prior.get('desktop_open_sent') or
                    prior.get('review_phase') not in (None,'preparing','closing') or
                    (prior.get('review_phase') is None and '上次退出' not in prior.get('message',''))):
                raise RuntimeError('旧计划可能已换号或未确认桌面状态；请先检查账号与 Codex，不能自动重试。')
            latest=read_json(directory/'reset-wait.json',{})
            if latest.get('id')!=prior.get('id') or latest.get('phase')!='needs_review':raise RuntimeError('计划状态已改变，请重新检查。')
            backup_id=str(uuid.UUID(prior['id']))
            atomic_json(directory/('reset-review-'+backup_id+'.json'),prior)
            atomic_json(directory/'reset-wait.json',dict(prior,phase='cancelled',updated_at=time.time(),message='用户重新手动切换：已核验当前账号与打开的桌面，旧退出请求归档。'))
            if prior.get('wake_task'):remove_wake(prior['wake_task'])
        report('scanning', '正在刷新其他账号额度…')
        active, providers = provider_inventory()
        if not ui_request('inspect', active)['active']:
            raise RuntimeError('CC Switch 当前界面与已保存账号不一致；未切换。')
        candidates, failures = [], 0
        seen = {active['account']}
        for provider in providers:
            check_permission()
            if provider['account'] in seen:
                continue
            seen.add(provider['account'])
            report('scanning', '正在查询：'+provider['name'])
            try:
                snapshot = ui_request('refresh', provider)
                quota = remaining_from_card(snapshot)
                if not snapshot['active'] and snapshot['canSwitch']:
                    candidates.append(dict(provider, quota=quota, snapshot=snapshot,reset_estimate=countdown_reset(snapshot)))
            except (RuntimeError, ValueError, subprocess.TimeoutExpired):
                failures += 1
        policy=load_settings()
        target = select_candidate(candidates, policy['resume_5h'], policy['selection_strategy'], policy['account_priority'])
        if target is None:
            if policy['wait_for_reset'] and policy['enabled'] and policy['auto_resume'] and plugin_enabled() and not failures:
                check_permission()
                current_snapshot=ui_request('refresh',active)
                current_quota=remaining_from_card(current_snapshot)
                all_accounts=candidates+[dict(active,quota=current_quota,snapshot=current_snapshot,
                                              reset_estimate=countdown_reset(current_snapshot))]
                viable=[c for c in all_accounts if c['quota']['week']>0]
                if (not select_candidate(all_accounts,policy['resume_5h']) and viable
                        and all(c['reset_estimate'] is not None for c in viable)):
                    fallback=earliest_reset(viable)
                    if fallback:
                        request_wait(active,fallback,automatic,all_accounts)
                        from runtime import ensure_service
                        ensure_service()
                        message='全部账号额度不足，已选择最早重置的账号：'+fallback['name']+'。后台将保存暂停记录、核验精确重置时间并退出 Codex。'
                        report('waiting_reset',message);return message
            message = '没有额度充足的其他账号，已保持当前账号。'
            if failures:
                message += f' 有 {failures} 个账号额度查询失败或无法确认，请在 CC Switch 检查。'
            report('unchanged', message)
            return message
        current, providers_now = provider_inventory()
        if current != active or not any(p['id'] == target['id'] and p['account'] == target['account'] and p['name'] == target['name'] for p in providers_now):
            raise RuntimeError('账号配置已发生变化；请重新点击手动切换。')
        # Refresh the winner again so an earlier account's scan cannot go stale.
        snapshot = ui_request('refresh', target)
        target['quota'] = remaining_from_card(snapshot)
        latest=load_settings()
        if any(latest[k]!=policy[k] for k in ('resume_5h','selection_strategy','account_priority')):
            raise RuntimeError('选号规则已改变，请重新切换。')
        if not select_candidate([target], latest['resume_5h']):
            raise RuntimeError('目标账号最新额度已不满足恢复条件；未切换。')
        check_permission()
        request_restart(active,target,automatic=automatic)
        from runtime import ensure_service
        ensure_service()
        message='已安排退出后换号：'+target['name']+'。后台将保存任务、确认 Codex 退出，再切换账号并重开；尚未执行切换。'
        report('restart_requested',message)
        return message
    except Exception as exc:
        report('error', str(exc) if isinstance(exc, (RuntimeError, ValueError)) else '切换未完成；请检查 CC Switch 中的账号状态。'+' ['+type(exc).__name__+']')
        raise
    finally:
        # Drop cached quota even after uncertain outcomes before any auto-resume.
        atomic_json(directory/'commands'/(uuid.uuid4().hex+'.json'), {'action':'check'})
        handle.close()
