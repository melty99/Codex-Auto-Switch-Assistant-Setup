"""Windowless settings entry point with visible startup errors on Windows."""
import ctypes
from pathlib import Path
import tempfile
import traceback


def report_error(details):
    message = 'Codex Auto Switch Assistant could not start.'
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', prefix='codex-quota-settings-',
            suffix='.log', delete=False,
        ) as log:
            log.write(details)
            message += '\n\nError log: ' + str(Path(log.name))
    except OSError:
        pass
    message += '\n\n' + details[-1800:]
    ctypes.windll.user32.MessageBoxW(None, message, 'Codex Auto Switch Assistant · Startup error', 0x10)


def main():
    try:
        from settings_ui import run_ui
        run_ui()
    except Exception:
        report_error(traceback.format_exc())
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
