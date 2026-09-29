"""Read-only release integrity and environment checks; no account operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify():
    manifest = json.loads((ROOT/'SHA256SUMS.json').read_text(encoding='utf-8'))
    if manifest.get('algorithm') != 'sha256' or not manifest.get('files'):
        raise ValueError('Invalid SHA256 manifest')
    for name, expected in manifest['files'].items():
        path = (ROOT/name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError('Missing or invalid file: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('SHA256 mismatch: '+name)
    required = ['README.md', 'README.en.md', 'README.zh-CN.md', 'QUICK_START.md',
                'setting.cmd', 'INSTALL_WITH_CODEX.md', '.agents/plugins/marketplace.json',
                'plugins/codex-quota-guard/.codex-plugin/plugin.json',
                'plugins/codex-quota-guard/skills/codex-quota-guard/SKILL.md']
    for name in required:
        if name not in manifest['files']:
            raise ValueError('Required release file missing: '+name)
    return len(manifest['files'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--installed-root',type=Path)
    args=parser.parse_args()
    try:
        count=verify()
        result={'integrity':True,'files_checked':count}
        if not args.verify_only:
            if os.name!='nt':raise ValueError('Windows is required')
            plugin=args.installed_root or ROOT/'plugins/codex-quota-guard'
            sys.path.insert(0,str(plugin.resolve()/'scripts'))
            import tkinter
            from machine_config import detect,validate
            config=detect()
            result['paths_found']={k:bool(v and Path(v).exists()) for k,v in config.items() if k.endswith(('_exe','_dir','_home','_root'))}
            try:
                validate(config)
                result['configuration']={'valid':True}
            except Exception as exc:
                # Do not dump settings, credentials, usernames or chat content.
                result['configuration']={'valid':False,'error_type':type(exc).__name__}
            if args.installed_root and not result['configuration']['valid']:
                print(json.dumps(result));return 1
        print(json.dumps(result));return 0
    except Exception as exc:
        print(json.dumps({'integrity':False,'error':str(exc)},ensure_ascii=True));return 1


if __name__=='__main__':raise SystemExit(main())
