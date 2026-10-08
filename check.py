#!/usr/bin/env python3
"""Offline diagnostics and scoped PM2 operations; never print environment values."""
import importlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

APP = Path(os.environ['GCLI_APP_DIR']).resolve()
STATE = Path(os.environ['GCLI_STATE_DIR'])
PORT = int(os.environ.get('GCLI_PORT', '7861'))
MODULES = {'python-dotenv': 'dotenv', 'python-multipart': 'multipart',
           'pyjwt': 'jwt', 'httpx': 'httpx', 'pydantic': 'pydantic'}


def health():
    if sys.version_info < (3, 12):
        print('Python 需要 3.12 或更新版本。'); return 1
    reqfile = APP / 'requirements-termux.txt'
    if not reqfile.is_file():
        print('缺少 requirements-termux.txt；请检查项目文件，不自动重建代码。'); return 2
    broken, unknown = [], []
    for raw in reqfile.read_text().splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([\w.-]+)(?:\[([\w,.-]+)\])?\s*(?:(==|>=|<=|>|<|~=|!=)\s*([\w.+-]+))?', line)
        if not match:
            unknown.append('无法离线解释的依赖语法'); continue
        name, extras, op, wanted = match.groups()
        normalized = name.lower().replace('_', '-')
        try:
            version = md.version(name)
            if op:
                if op in ('==', '!='):
                    valid = (version == wanted) if op == '==' else (version != wanted)
                else:
                    try:
                        from packaging.specifiers import SpecifierSet
                        valid = version in SpecifierSet(op + wanted)
                    except ImportError:
                        unknown.append(f'{name}: 需要 packaging 才能判断版本范围'); continue
                # The upstream Termux list pins pydantic v1, which cannot run on
                # the Python 3.12+ Termux ships; the launcher replaces it with v2
                # on purpose, so treat an installed v2 as healthy.
                if not valid and normalized == 'pydantic' and version.startswith('2.'):
                    valid = True
                if not valid:
                    broken.append(f'{name}: 版本不符合要求'); continue
            module = MODULES.get(normalized, normalized.replace('-', '_'))
            importlib.import_module(module)
            if normalized == 'httpx' and extras and 'socks' in extras.split(','):
                md.version('socksio'); importlib.import_module('socksio')
        except Exception:
            broken.append(f'{name}: 未安装或无法导入（含底层依赖）')
    # Version metadata alone is not enough: a bad pydantic/fastapi pair installs
    # cleanly but fails at import, which is the real Termux failure mode.
    for module in ('fastapi', 'pydantic'):
        try:
            importlib.import_module(module)
        except Exception:
            broken.append(f'{module}: 导入失败（依赖组合不兼容）')
    for item in broken + unknown:
        print(item)
    if unknown:
        print('诊断不完整；不据此自动修复。'); return 2
    if broken:
        return 1
    print('依赖离线检查通过（版本元数据、所有直接依赖导入、SOCKS 附加依赖）。')
    return 0


def namespaces():
    own = str(STATE / 'pm2')
    legacy = os.environ.get('GCLI_LEGACY_PM2_HOME') or os.environ.get('PM2_HOME') or str(Path.home() / '.pm2')
    return list(dict.fromkeys([own, legacy]))


def inventory():
    found = []
    for home in namespaces():
        # Do not launch a previously nonexistent legacy daemon.
        if home != namespaces()[0] and not Path(home, 'pm2.pid').exists():
            continue
        env = dict(os.environ, PM2_HOME=home)
        p = subprocess.run(['pm2', 'jlist'], env=env, capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError('PM2 状态读取失败')
        rows = None
        # First daemon startup can prefix jlist with PM2 banners. Accept only
        # an entire JSON document/line, never extract arbitrary JSON fragments.
        for candidate in [p.stdout] + list(reversed(p.stdout.splitlines())):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, list) and all(isinstance(row, dict) for row in parsed):
                rows = parsed
                break
        if rows is None:
            raise RuntimeError('PM2 返回格式无效')
        for row in rows:
            e = row.get('pm2_env', {})
            cwd = Path(e.get('pm_cwd', '/')).resolve()
            exe = Path(e.get('pm_exec_path', '/')).resolve()
            args = e.get('args') or []
            if isinstance(args, str):
                args = [args]
            # Name alone (web/gcli2api) never grants permission to operate.
            matches = exe == (APP / 'web.py').resolve() or (exe == (APP / '.venv/bin/python').resolve() and cwd == APP and 'web.py' in args)
            if matches:
                found.append((home, row['pm_id'], e))
    return found


def mutate(action):
    for home, ident, _ in inventory():
        p = subprocess.run(['pm2', action, str(ident)], env=dict(os.environ, PM2_HOME=home), capture_output=True)
        if p.returncode:
            raise RuntimeError('PM2 操作失败')


def probe():
    # Hypercorn's FastAPI OpenAPI document identifies this API, not a generic 200.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{PORT}/openapi.json', timeout=2) as response:
            data = json.loads(response.read(2_000_000))
        paths = data.get('paths', {})
        return all(p in paths for p in ('/auth/login', '/auth/start', '/v1/chat/completions'))
    except Exception:
        return False


def main():
    action = sys.argv[1]
    if action == 'health':
        return health()
    if action == 'free':
        try:
            with socket.socket() as s:
                s.bind(('127.0.0.1', PORT))
            return 0
        except OSError:
            print('目标端口已占用，未启动新进程；检查旧服务或设置 GCLI_PORT。'); return 1
    if action == 'ready':
        for _ in range(int(os.environ.get('GCLI_READY_ATTEMPTS', '15'))):
            if probe() and any(e.get('status') == 'online' for _, _, e in inventory()):
                print('本服务接口就绪；这不代表 Google 已授权或模型可调用。'); return 0
            time.sleep(1)
        print('接口尚未就绪：检查端口配置、项目日志及 PM2 状态；没有自动调用模型。'); return 1
    if action == 'delete' or action == 'stop':
        mutate(action); return 0
    rows = inventory()
    if action == 'online':
        return 0 if len(rows) == 1 and rows[0][2].get('status') == 'online' else 1
    if action == 'status':
        if not rows:
            print('没有匹配本项目路径的 PM2 进程。')
        for _, ident, e in rows:
            print(f'本项目 PM2 ID={ident} 状态={e.get("status", "unknown")}')
    elif action == 'logs':
        # Do not dump arbitrary log lines (OAuth codes and credentials may occur).
        for _, ident, e in rows:
            print(f'本项目 PM2 ID={ident} 日志位置：')
            for field in ('pm_out_log_path', 'pm_err_log_path'):
                path = e.get(field)
                if path:
                    print(path)
                    try:
                        with open(path, 'rb') as f:
                            f.seek(max(0, os.path.getsize(path) - 32768))
                            text = f.read().decode(errors='replace')
                        categories = [c for c in ('ModuleNotFoundError', 'ImportError', 'Address already in use', 'Traceback', '429', '403') if c in text]
                        print('最近 32KB 诊断类别：' + ('、'.join(categories) or '无已知错误类别'))
                    except OSError:
                        print('日志暂不可读。')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('诊断工具失败（未打印内部环境/敏感原文）；检查 Python、PM2 和目录配置。')
        sys.exit(2)
