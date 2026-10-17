"""Stop only this checkout's local Django server and queue worker (Linux /proc)."""
import os
from pathlib import Path
import signal
import time


ROOT = Path(__file__).resolve().parent.parent


def identity(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if stat[0] == 'Z' else stat[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def main():
    targets = {}
    supervisors = set()
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / 'cmdline').read_bytes().split(b'\0')
            if len(args) < 3 or args[1] not in (b'manage.py', os.fsencode(ROOT / 'manage.py')):
                continue
            if args[2] not in (b'runserver', b'worker') or (entry / 'cwd').resolve() != ROOT:
                continue
            if (entry / 'exe').resolve() != (ROOT / '.venv/bin/python').resolve():
                continue
            pid = int(entry.name)
            targets[pid] = identity(pid)
            parent = int((entry / 'status').read_text().split('PPid:')[1].split()[0])
            parent_dir = Path(f'/proc/{parent}')
            parent_args = (parent_dir / 'cmdline').read_bytes().split(b'\0')
            if (parent_dir / 'cwd').resolve() == ROOT and any(
                arg in (b'./start.sh', b'start.sh', os.fsencode(ROOT / 'start.sh'))
                for arg in parent_args[1:]
            ):
                supervisors.add(parent)
                targets[parent] = identity(parent)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise SystemExit('无法检查本项目服务的进程信息，请使用启动服务的同一用户运行。')

    if not targets:
        print('没有正在运行的本项目 Web 或 worker。', flush=True)
        return
    print('正在停止旧 Web 和 worker……', flush=True)
    for pid in [*supervisors, *(targets.keys() - supervisors)]:
        try:
            if targets[pid] is not None and identity(pid) == targets[pid]:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if all(token is None or identity(pid) != token for pid, token in targets.items()):
            print('旧服务已停止。', flush=True)
            return
        time.sleep(0.1)
    raise SystemExit('旧服务仍在处理任务，未启动新服务；请稍后重试。')


if __name__ == '__main__':
    main()
