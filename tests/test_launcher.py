"""Offline integration fixtures: only temporary files and loopback HTTP; no installations."""
import importlib.util
import http.server
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT = 'https://ghfast.top/https://raw.githubusercontent.com/su-kaka/gcli2api/master/termux-install.sh'
DEFAULT_REPO = 'https://ghfast.top/https://github.com/su-kaka/gcli2api.git'
DEFAULT_MIRROR = 'https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main'
REVIEWED = '''#!/bin/bash
if false; then
pgrep -f "apt|dpkg"
pgrep -f "apt|dpkg"
target_mirror="https://packages-cf.termux.dev/apt/termux-main"
fallback_mirror="https://packages.termux.dev/apt/termux-main"
grep -q "$target_mirror" "$PREFIX/etc/apt/sources.list"
cat <<'EOF'
deb https://packages-cf.termux.dev/apt/termux-main stable main
EOF
sed -i "s#${target_mirror}#${fallback_mirror}#g" "$PREFIX/etc/apt/sources.list" || true
git clone https://github.com/su-kaka/gcli2api.git
git clone https://github.com/other/unrelated.git
fi
'''


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.app = self.base / 'install/gcli2api'
        self.state = self.base / 'state'
        self.bin = self.base / 'bin'
        self.bin.mkdir(); self.state.mkdir()
        self.trace = self.base / 'trace'
        self.registry = self.base / 'registry.json'
        self.registry.write_text('{}')
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        GCLI_APP_DIR=str(self.app), GCLI_STATE_DIR=str(self.state),
                        GCLI_PYTHON=sys.executable, GCLI_READY_ATTEMPTS='1',
                        GCLI_LEGACY_PM2_HOME=str(self.base / 'legacy'),
                        FIX_TRACE=str(self.trace), FIX_REGISTRY=str(self.registry),
                        FIX_HEALTH='0')
        for key in ('GCLI_INSTALL_URL', 'GCLI_REPO_URL', 'GCLI_TERMUX_MIRROR'):
            self.env.pop(key, None)
        self.env['GCLI_INSTALL_URL'] = 'file:///offline-fixture.sh'
        self.tool('pm2', '''import os,sys,json
from pathlib import Path
args=sys.argv[1:]; home=os.environ.get('PM2_HOME',str(Path.home()/'.pm2'))
p=Path(os.environ['FIX_REGISTRY']); data=json.loads(p.read_text()); rows=data.setdefault(home,[])
with open(os.environ['FIX_TRACE'],'a') as f:f.write('pm2 '+ ' '.join(args)+'\\n')
if args[0]=='jlist': print(json.dumps(rows))
elif args[0]=='start':
 rows.append({'pm_id':max([r['pm_id'] for r in rows]+[-1])+1,'pm2_env':{'pm_exec_path':args[1], 'pm_cwd':os.getcwd(), 'args':['web.py'], 'status':'online'}})
 if os.environ.get('FIX_START_SERVER'):
  import subprocess
  child=subprocess.Popen([sys.executable,os.environ['FIX_START_SERVER']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  Path(os.environ['FIX_SERVER_PID']).write_text(str(child.pid))
elif args[0] in ('stop','delete'):
 ident=int(args[1])
 if args[0]=='delete':rows[:]=[r for r in rows if r['pm_id']!=ident]
 else:
  for r in rows:
   if r['pm_id']==ident:r['pm2_env']['status']='stopped'
p.write_text(json.dumps(data))
''')
        for cmd in ('git', 'uv', 'npm', 'pkg', 'node', 'apt-get'):
            self.tool(cmd, '''import os,sys
with open(os.environ['FIX_TRACE'],'a') as f:f.write(os.path.basename(sys.argv[0])+' '+' '.join(sys.argv[1:])+'\\n')
if os.path.basename(sys.argv[0])=='git' and sys.argv[1:2]==['status']:
 print(os.environ.get('FIX_DIRTY',''),end='')
if os.path.basename(sys.argv[0])=='git' and sys.argv[1:]==['rev-parse','--show-toplevel']:
 print(os.environ.get('FIX_GIT_ROOT',os.environ['GCLI_APP_DIR']))
if os.path.basename(sys.argv[0])=='uv' and sys.argv[1:3]==['pip','install']:
 from pathlib import Path
 Path(os.environ['GCLI_APP_DIR'],'healthy').touch()
''')
        self.tool('curl', '''import os,sys,shutil
with open(os.environ['FIX_TRACE'],'a') as f:f.write('curl '+ ' '.join(sys.argv[1:])+'\\n')
shutil.copyfile(os.environ['FIX_UPSTREAM'],sys.argv[sys.argv.index('-o')+1])
''')

    def test_default_sources_and_complete_literal_patch(self):
        upstream = self.base / 'upstream.sh'; upstream.write_text(REVIEWED)
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.env.pop('GCLI_INSTALL_URL')
        output = self.run_cli('install', False)
        for url in (DEFAULT_SCRIPT, DEFAULT_REPO, DEFAULT_MIRROR):
            self.assertIn(url, output)
        self.assertIn(DEFAULT_SCRIPT, self.trace.read_text())
        self.assertEqual((self.state / 'upstream-install.sh').read_text(), REVIEWED)
        patched = (self.state / 'upstream-install-compatible.sh').read_text()
        self.assertIn("git clone '" + DEFAULT_REPO + "'", patched)
        self.assertIn('git clone https://github.com/other/unrelated.git', patched)
        self.assertEqual(patched.count(DEFAULT_MIRROR), 3)
        self.assertIn('grep -Fq', patched)
        self.assertNotIn('sed -i', patched)
        self.assertNotIn('packages-cf.termux.dev', patched)
        self.assertNotIn('packages.termux.dev', patched)
        self.assertEqual(patched.count('pgrep -x "apt|apt-get|dpkg"'), 2)
        self.assertNotIn('--global', patched)

    def test_changed_network_script_fails_closed(self):
        for content in (REVIEWED.replace('git clone https://github.com/su-kaka/gcli2api.git', 'git clone "https://github.com/su-kaka/gcli2api.git"'),
                        REVIEWED.replace('gcli2api.git\n', 'gcli2api.git-other\n'),
                        REVIEWED.replace('fallback_mirror=', 'changed_mirror='),
                        '#!/bin/bash\necho pretend\n'):
            with self.subTest(content=content):
                upstream = self.base / 'upstream.sh'; upstream.write_text(content)
                self.env['FIX_UPSTREAM'] = str(upstream)
                self.env['GCLI_INSTALL_URL'] = DEFAULT_SCRIPT
                self.assertIn('语句已变化', self.run_cli('install', False))
                self.assertFalse(list((self.state / 'logs').glob('upstream-*')))

    def test_source_url_validation_before_commands(self):
        for key in ('GCLI_INSTALL_URL', 'GCLI_REPO_URL', 'GCLI_TERMUX_MIRROR'):
            original = self.env.get(key)
            for value in ('', 'http://example.com/x', 'https://', 'file://relative',
                          "https://example.com/a'", 'https://example.com/`id`',
                          'https://example.com/$HOME', 'https://example.com/a b',
                          'https://example.com/a\ncmd', 'https://example.com/\\x'):
                with self.subTest(key=key, value=value):
                    self.env[key] = value
                    self.assertIn('URL 无效', self.run_cli('install', False))
                    self.assertFalse(self.trace.exists())
            if original is None: self.env.pop(key)
            else: self.env[key] = original

    def test_custom_sources_are_literal_and_update_scoped(self):
        upstream = self.base / 'upstream.sh'; upstream.write_text(REVIEWED)
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.env['GCLI_REPO_URL'] = 'https://example.com/a.b/repo.git?x=1&y=2#frag'
        self.env['GCLI_TERMUX_MIRROR'] = 'file:///tmp/main.a+b#literal'
        self.run_cli('install', False)
        patched = (self.state / 'upstream-install-compatible.sh').read_text()
        self.assertIn("git clone '" + self.env['GCLI_REPO_URL'] + "'", patched)
        self.assertEqual(patched.count(self.env['GCLI_TERMUX_MIRROR']), 3)
        self.project(); self.run_cli('update')
        trace = self.trace.read_text()
        self.assertIn('url.' + self.env['GCLI_REPO_URL'] + '.insteadOf=', trace)
        self.assertIn('pull --ff-only', trace)
        self.assertNotIn('--global', trace)

    def test_upstream_pgrep_patch_preserves_original(self):
        upstream = self.base / 'upstream.sh'
        content = '#!/bin/bash\nif false; then\npgrep -f "apt|dpkg"\npgrep -f "apt|dpkg"\nfi\n'
        upstream.write_text(content)
        self.env['FIX_UPSTREAM'] = str(upstream)
        output = self.run_cli('install', False)
        self.assertIn('精确检测', output)
        self.assertEqual((self.state / 'upstream-install.sh').read_text(), content)
        patched = (self.state / 'upstream-install-compatible.sh').read_text()
        self.assertEqual(patched.count('pgrep -x "apt|apt-get|dpkg"'), 2)
        self.assertNotIn('pgrep -f "apt|dpkg"', patched)

    def test_legacy_sources_retired_only_exact_obsolete_entries(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\nexit 0\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.env['GCLI_INSTALL_URL'] = upstream.as_uri()
        prefix = self.base / 'prefix'
        sources = prefix / 'etc/apt/sources.list.d'
        sources.mkdir(parents=True)
        game = 'deb https://termux.org/game-packages-21-bin games stable\n'
        (sources / 'game.list').write_text(game)
        (sources / 'science.list').write_text('deb https://example.org/custom science stable\n')
        self.env['PREFIX'] = str(prefix)
        self.run_cli('install', False)
        self.assertFalse((sources / 'game.list').exists())
        self.assertEqual(next(sources.glob('game.list.gcli-disabled-*')).read_text(), game)
        self.assertTrue((sources / 'science.list').exists())

    def test_install_exports_mirrors_and_upgrades_base(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\necho "PYPI=$UV_DEFAULT_INDEX" >> "$FIX_TRACE"\necho "NPM=$npm_config_registry" >> "$FIX_TRACE"\nexit 0\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        prefix = self.base / 'prefix'
        prefix.mkdir()
        self.env['PREFIX'] = str(prefix)
        self.run_cli('install', False)
        trace = self.trace.read_text()
        self.assertIn('apt-get -y', trace)
        self.assertIn('PYPI=https://pypi.tuna.tsinghua.edu.cn/simple', trace)
        self.assertIn('NPM=https://registry.npmmirror.com', trace)

    def test_custom_package_mirrors_are_used(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\necho "PYPI=$UV_DEFAULT_INDEX" >> "$FIX_TRACE"\nexit 0\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.env['GCLI_PYPI_MIRROR'] = 'https://mirror.example.com/pypi/simple'
        self.run_cli('install', False)
        self.assertIn('PYPI=https://mirror.example.com/pypi/simple', self.trace.read_text())

    def test_invalid_package_mirror_is_rejected(self):
        self.env['GCLI_PYPI_MIRROR'] = 'https://evil.example.com/$(id)'
        self.run_cli('install', False)
        self.assertFalse(self.trace.exists())

    def test_install_auto_repairs_unhealthy_deps(self):
        # Upstream install can succeed while deps are broken (pinned pydantic v1).
        template = self.base / 'template'
        self.project(); shutil.copytree(self.app, template); shutil.rmtree(self.app)
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\ncp -R "' + str(template) + '" ./gcli2api\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.env['FIX_HEALTH'] = '1'
        self.run_cli('install', False)
        self.assertIn('uv pip install --reinstall', self.trace.read_text())

    def tearDown(self):
        pidfile = self.base / 'server.pid'
        if pidfile.exists():
            try:
                os.kill(int(pidfile.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.tmp.cleanup()

    def delayed_server(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.env['GCLI_PORT'] = str(port)
        self.env['GCLI_READY_ATTEMPTS'] = '3'
        script = self.base / 'http_fixture.py'
        script.write_text('''import http.server, json, os
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers()
  self.wfile.write(json.dumps({'paths':dict.fromkeys(['/auth/login','/auth/start','/v1/chat/completions'])}).encode())
http.server.HTTPServer(('127.0.0.1',int(os.environ['GCLI_PORT'])),Handler).serve_forever()
''')
        self.env['FIX_START_SERVER'] = str(script)
        self.env['FIX_SERVER_PID'] = str(self.base / 'server.pid')

    def tool(self, name, code):
        p = self.bin / name
        p.write_text('#!' + sys.executable + '\n' + code)
        p.chmod(0o755)

    def project(self):
        (self.app / '.venv/bin').mkdir(parents=True)
        (self.app / 'web.py').write_text('# fixture')
        (self.app / 'requirements-termux.txt').write_text('fastapi\n')
        p = self.app / '.venv/bin/python'
        p.write_text('#!' + sys.executable + '''
import os,sys
from pathlib import Path
if sys.argv[-1]=='health':
 rc=int(os.environ['FIX_HEALTH'])
 if Path(os.environ['GCLI_APP_DIR'],'healthy').exists():rc=0
 print('模拟依赖诊断' if rc else '模拟依赖健康')
 sys.exit(rc)
os.execv(sys.executable,[sys.executable]+sys.argv[1:])
''')
        p.chmod(0o755)

    def run_cli(self, action, success=True):
        p = subprocess.run(['bash', str(ROOT / 'launcher.sh'), action], env=self.env, text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        else:
            self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout + p.stderr

    def server(self, service=True):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(s):
                s.send_response(200); s.end_headers()
                s.wfile.write(json.dumps({'paths':dict.fromkeys(['/auth/login','/auth/start','/v1/chat/completions'])} if service else {'hello':'world'}).encode())
            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.env['GCLI_PORT'] = str(server.server_port)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)

    def rows(self, *rows, legacy=False):
        home = str(self.base / 'legacy') if legacy else str(self.state / 'pm2')
        if legacy:
            Path(home).mkdir(exist_ok=True); Path(home, 'pm2.pid').touch()
        data = json.loads(self.registry.read_text()); data[home] = list(rows)
        self.registry.write_text(json.dumps(data))

    def row(self, ident, name='web', cwd=None, status='online'):
        return {'pm_id':ident,'pm2_env':{'name':name,'pm_exec_path':str(Path(cwd or self.app) / '.venv/bin/python'), 'pm_cwd':str(cwd or self.app),'args':['web.py'],'status':status}}

    def test_start_reuses_legacy_no_download(self):
        self.project(); self.server(); self.rows(self.row(4), legacy=True)
        output = self.run_cli('start')
        self.assertIn('GCLI API 地址：', output)
        trace = self.trace.read_text()
        for word in ('git ', 'uv ', 'npm ', 'pkg ', 'start ', 'stop ', 'delete '):
            self.assertNotIn(word, trace)

    def test_stop_only_exact_project_not_names(self):
        self.project(); self.rows(self.row(1), self.row(2, cwd=self.base/'SillyTavern'))
        self.run_cli('stop')
        rows = json.loads(self.registry.read_text())[str(self.state/'pm2')]
        self.assertEqual(rows[0]['pm2_env']['status'], 'stopped')
        self.assertEqual(rows[1]['pm2_env']['status'], 'online')

    def test_duplicates_scoped_stop_and_port_conflict(self):
        self.project(); self.server(False)
        self.rows(self.row(1), self.row(2), self.row(3, cwd=self.base/'other'))
        self.run_cli('start', False)
        trace = self.trace.read_text()
        self.assertIn('pm2 stop 1', trace); self.assertIn('pm2 stop 2', trace)
        self.assertNotIn('pm2 stop 3', trace); self.assertNotIn('pm2 start', trace)

    def test_arbitrary_200_is_not_ready(self):
        self.project(); self.server(False); self.rows(self.row(1))
        self.assertIn('尚未就绪', self.run_cli('start', False))

    def test_repair_healthy_offline(self):
        self.project(); self.run_cli('repair')
        self.assertFalse(self.trace.exists())

    def test_repair_damaged_only_requirements(self):
        self.project(); self.env['FIX_HEALTH']='1'
        self.run_cli('repair')
        text=self.trace.read_text()
        self.assertIn('uv pip install --reinstall --python', text)
        self.assertIn('-r requirements-termux.txt', text)
        self.assertNotIn('uv add', text)

    def test_uncertain_diagnosis_does_not_repair(self):
        self.project(); self.env['FIX_HEALTH']='2'
        self.run_cli('repair', False); self.assertFalse(self.trace.exists())

    def test_update_preserves_dirty_files(self):
        self.project(); self.env['FIX_DIRTY']=' M pyproject.toml\n'
        self.assertIn('uv init/uv add', self.run_cli('update', False))
        self.assertNotIn('pull', self.trace.read_text())

    def test_update_refuses_parent_repository(self):
        self.project(); self.env['FIX_GIT_ROOT'] = str(self.base)
        self.run_cli('update', False)
        self.assertNotIn('pull', self.trace.read_text())

    def test_update_ff_only_conditional_repair(self):
        self.project(); self.run_cli('update')
        trace = self.trace.read_text()
        self.assertIn('pull --ff-only', trace)
        self.assertIn('url.' + DEFAULT_REPO + '.insteadOf=https://github.com/su-kaka/gcli2api.git', trace)
        self.assertNotIn('--global', trace)
        self.assertNotIn('git config', trace)
        self.assertNotIn('uv ', self.trace.read_text())

    def test_guide_does_not_execute_or_disclose_env(self):
        self.project(); (self.app/'.env').write_text('PASSWORD=SECRET-XYZ\ntouch '+str(self.base/'BAD'))
        text=self.run_cli('guide')
        self.assertNotIn('SECRET-XYZ', text); self.assertFalse((self.base/'BAD').exists())
        self.assertIn('/antigravity/v1', text)

    def test_install_false_zero_is_rejected(self):
        upstream=self.base/'upstream.sh'; upstream.write_text('#!/bin/bash\necho pretend success\nexit 0\n')
        self.env['FIX_UPSTREAM']=str(upstream)
        self.run_cli('install', False)
        self.assertTrue(list((self.state/'logs').glob('upstream-*')))

    def test_download_limits_and_private_network_diagnosis_curl_only(self):
        # Bootstrap has Bash/coreutils/curl, but no Python/uv/node/git/pm2.
        minimal = self.base / 'minimal-bin'; minimal.mkdir()
        for name in ('bash', 'dirname', 'basename', 'mkdir', 'date', 'sleep', 'rm', 'rmdir'):
            (minimal / name).symlink_to(shutil.which(name))
        curl = minimal / 'curl'
        curl.write_text('''#!/bin/bash
printf '%s\\n' "$@" > "$FIX_TRACE"
echo SECRET-DOWNLOAD >&2
exit 28
''')
        curl.chmod(0o755)
        self.env['PATH'] = str(minimal)
        menu = subprocess.run([str(minimal / 'bash'), str(ROOT / 'launcher.sh')],
                              env=self.env, input='0\n', text=True, capture_output=True, timeout=5)
        self.assertEqual(menu.returncode, 0, menu.stdout + menu.stderr)
        self.assertIn('首次安装', menu.stdout)
        output = self.run_cli('install', False)
        args = self.trace.read_text().splitlines()
        for flag, value in (('--connect-timeout', '10'), ('--max-time', '60'),
                            ('--retry', '1'), ('--retry-max-time', '125')):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn('浏览器能打开网页不等于 Termux', output)
        self.assertIn('不会盲目换源', output)
        self.assertNotIn('代理', output)
        self.assertNotIn('VPN', output)
        self.assertIn('耗时', output)
        self.assertNotIn('SECRET-DOWNLOAD', output)
        self.assertFalse(list((self.state / 'logs').glob('upstream-*')))
        self.assertFalse((self.state / 'upstream-install.sh').exists())
        self.assertFalse((self.state / 'action.lock').exists())

    def test_logged_heartbeat_and_completion_do_not_expose_logs(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\necho SECRET-WAIT\nsleep 11\nexit 7\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        output = self.run_cli('install', False)
        self.assertIn('仍在等待 upstream（已耗时', output)
        self.assertIn('退出码 7，耗时', output)
        self.assertIn('apt/pkg 以非交互方式执行', output)
        self.assertIn('不表示安装有进展', output)
        self.assertNotIn('SECRET-WAIT', output)
        self.assertIn('SECRET-WAIT', next((self.state / 'logs').glob('upstream-*')).read_text())

    def assert_cancel_cleans_children(self, sig, phase):
        pidfile = self.base / 'worker-pids'
        code = '''#!/bin/bash
sleep 300 &
printf '%s %s' "$$" "$!" > "$FIX_WORKER_PIDS"
echo SECRET-CANCEL
wait
'''
        self.env['FIX_WORKER_PIDS'] = str(pidfile)
        if phase == 'download':
            (self.bin / 'curl').write_text(code)
        else:
            upstream = self.base / 'upstream.sh'; upstream.write_text(code)
            self.env['FIX_UPSTREAM'] = str(upstream)
        proc = subprocess.Popen(['bash', str(ROOT / 'launcher.sh'), 'install'],
                                env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(pidfile.exists(), 'worker did not start')
            pids = [int(p) for p in pidfile.read_text().split()]
            proc.send_signal(sig)
            out, err = proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 130 if sig == signal.SIGINT else 143, out + err)
            self.assertIn('操作已中断', out + err)
            self.assertNotIn('SECRET-CANCEL', out + err)
            self.assertFalse((self.state / 'action.lock').exists())
            for pid in pids:
                status = Path(f'/proc/{pid}/stat')
                # Orphan zombies are not running tasks; container init may reap late.
                if status.exists():
                    self.assertEqual(status.read_text().split()[2], 'Z', f'child {pid} still running')
        finally:
            if proc.poll() is None:
                proc.kill(); proc.communicate()
            if pidfile.exists():
                for pid in map(int, pidfile.read_text().split()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_sigint_download_cleans_process_tree_and_lock(self):
        self.assert_cancel_cleans_children(signal.SIGINT, 'download')

    def test_sigterm_upstream_cleans_process_tree_and_lock(self):
        self.assert_cancel_cleans_children(signal.SIGTERM, 'upstream')

    def test_self_stopped_command_is_not_left_waiting(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\nkill -STOP $$\necho MUST-NOT-SUCCEED\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        output = self.run_cli('install', False)
        self.assertIn('被信号暂停', output)
        self.assertNotIn('MUST-NOT-SUCCEED', output)
        self.assertFalse((self.state / 'action.lock').exists())

    def test_upstream_stdin_is_dev_null_for_noninteractive(self):
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\nif [ -t 0 ]; then echo TTY; else echo NO-TTY; fi\n')
        self.env['FIX_UPSTREAM'] = str(upstream)
        self.assertIn('命令完成', self.run_cli('install', False))

    def test_existing_install_never_reruns_upstream(self):
        self.project(); self.server(); self.rows(self.row(1))
        self.run_cli('install'); self.assertNotIn('curl',self.trace.read_text())

    def test_interrupted_install_repairs_instead_of_starting_broken(self):
        self.project()
        self.env['FIX_HEALTH'] = '1'
        self.run_cli('install', False)
        trace = self.trace.read_text()
        self.assertIn('uv pip install --reinstall', trace)
        self.assertNotIn('curl', trace)

    def test_incomplete_install_not_overwritten(self):
        self.app.mkdir(parents=True); marker=self.app/'mine'; marker.touch()
        self.run_cli('install',False); self.assertTrue(marker.exists()); self.assertFalse(self.trace.exists())

    def test_pm2_initial_daemon_banner(self):
        self.project()
        self.tool('pm2', 'print("[PM2] daemon started\\n[]")')
        self.assertIn('没有匹配', self.run_cli('status'))

    def test_action_lock_and_invalid_port(self):
        (self.state/'action.lock').mkdir()
        self.run_cli('status', False)
        (self.state/'action.lock').rmdir()
        self.env['GCLI_PORT'] = 'not-a-port'
        self.run_cli('status', False)
        self.assertFalse(self.trace.exists())

    def test_invalid_pm2_json_fails_closed(self):
        self.project(); self.tool('pm2','print("not json")')
        self.run_cli('stop',False)

    def test_new_start_is_offline_and_ready(self):
        self.project(); self.delayed_server()
        self.assertIn('接口就绪', self.run_cli('start'))
        trace = self.trace.read_text()
        self.assertIn('--interpreter none', trace)
        for word in ('git ', 'uv ', 'npm ', 'pkg ', 'curl'):
            self.assertNotIn(word, trace)

    def test_successful_first_install_checks_results(self):
        self.project()
        template = self.base / 'template'
        shutil.copytree(self.app, template); shutil.rmtree(self.app)
        upstream = self.base / 'upstream.sh'
        upstream.write_text('#!/bin/bash\ncp -R "'+str(template)+'" ./gcli2api\necho SECRET-RAW-INSTALL\n')
        self.env['FIX_UPSTREAM'] = str(upstream); self.delayed_server()
        output = self.run_cli('install')
        self.assertIn('接口就绪', output)
        self.assertIn('download 命令完成（耗时', output)
        self.assertIn('upstream 命令完成（耗时', output)
        self.assertIn('退出码 0 不代表安装/接口验证成功', output)
        self.assertNotIn('SECRET-RAW-INSTALL', output)
        self.assertIn('SECRET-RAW-INSTALL', next((self.state/'logs').glob('upstream-*')).read_text())

    def test_real_health_logic_metadata_imports_extras(self):
        self.project()
        (self.app/'requirements-termux.txt').write_text('pydantic==1.10.22\nhttpx[socks]\npython-dotenv\nPyJWT\n')
        with patch.dict(os.environ, self.env):
            spec = importlib.util.spec_from_file_location('health_fixture', ROOT/'check.py')
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        imports = []
        def imported(name):
            imports.append(name)
        with patch.object(mod.sys, 'version_info', (3,12)), patch.object(mod.md, 'version', return_value='1.10.22'), patch.object(mod.importlib, 'import_module', side_effect=imported):
            self.assertEqual(mod.health(), 0)
            self.assertIn('socksio', imports); self.assertIn('dotenv', imports); self.assertIn('jwt', imports)
        # pydantic v2 is the intended Termux replacement for the pinned v1.
        with patch.object(mod.sys, 'version_info', (3,12)), patch.object(mod.md, 'version', return_value='2.13.5'), patch.object(mod.importlib, 'import_module', return_value=None):
            self.assertEqual(mod.health(), 0)
        # A real version mismatch on another package is still a failure.
        (self.app/'requirements-termux.txt').write_text('httpx==9.9.9\n')
        with patch.object(mod.sys, 'version_info', (3,12)), patch.object(mod.md, 'version', return_value='0.28.1'), patch.object(mod.importlib, 'import_module', return_value=None):
            self.assertEqual(mod.health(), 1)
        # Installed but incompatible combination must fail even with good metadata.
        (self.app/'requirements-termux.txt').write_text('pydantic==1.10.22\n')
        def explode(name):
            if name == 'fastapi':
                raise ImportError('cannot import name TypeAdapter')
        with patch.object(mod.sys, 'version_info', (3,12)), patch.object(mod.md, 'version', return_value='1.10.22'), patch.object(mod.importlib, 'import_module', side_effect=explode):
            self.assertEqual(mod.health(), 1)
        (self.app/'requirements-termux.txt').write_text('package @ https://invalid.example/x\n')
        with patch.object(mod.sys, 'version_info', (3,12)):
            self.assertEqual(mod.health(), 2)

    def test_help_and_unknown(self):
        self.run_cli('--help'); self.run_cli('invalid',False)

    def test_single_file_build_matches_directory_mode(self):
        # Build the single-file launcher, then run it from a directory that has
        # no check.py next to it, and confirm the embedded helper is verified.
        out = self.base / 'dist/gcli2api.sh'
        build = subprocess.run(['bash', str(ROOT / 'build.sh'), str(out)],
                               text=True, capture_output=True, timeout=120)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        self.assertTrue(out.exists())
        # The embedded payload must not leave the placeholder behind, and the
        # digest must be filled in (that is what enables embedded mode).
        text = out.read_text()
        self.assertNotIn('__GCLI_HELPER_PAYLOAD__', text)
        self.assertNotIn("EMBEDDED_HELPER_SHA256=''", text)

        lone = self.base / 'lone'
        lone.mkdir()
        shutil.copy(out, lone / 'gcli2api.sh')
        env = dict(self.env)
        env['GCLI_STATE_DIR'] = str(lone / 'state')
        env['GCLI_APP_DIR'] = str(lone / 'state/install/gcli2api')

        # guide needs no helper; it must work with no check.py present.
        p = subprocess.run(['bash', str(lone / 'gcli2api.sh'), 'guide'],
                           env=env, text=True, capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('酒馆', p.stdout)
        self.assertFalse((lone / 'check.py').exists(), 'guide must not need the helper')

        # A helper-backed action materializes and hash-checks the embedded copy.
        subprocess.run(['bash', str(lone / 'gcli2api.sh'), 'status'],
                       env=env, text=True, capture_output=True, timeout=60)
        materialized = lone / 'state/check.py'
        if materialized.exists():
            import hashlib
            expected = hashlib.sha256((ROOT / 'check.py').read_bytes()).hexdigest()
            self.assertEqual(hashlib.sha256(materialized.read_bytes()).hexdigest(), expected)

    def test_single_file_rejects_tampered_helper(self):
        out = self.base / 'dist/gcli2api.sh'
        subprocess.run(['bash', str(ROOT / 'build.sh'), str(out)],
                       text=True, capture_output=True, timeout=120, check=True)
        lone = self.base / 'lone2'
        lone.mkdir()
        shutil.copy(out, lone / 'gcli2api.sh')
        state = lone / 'state'
        state.mkdir()
        (state / 'check.py').write_text('print("TAMPERED")\n')
        env = dict(self.env, GCLI_STATE_DIR=str(state),
                   GCLI_APP_DIR=str(state / 'install/gcli2api'))
        p = subprocess.run(['bash', str(lone / 'gcli2api.sh'), 'status'],
                           env=env, text=True, capture_output=True, timeout=60)
        self.assertNotIn('TAMPERED', p.stdout + p.stderr)
        import hashlib
        expected = hashlib.sha256((ROOT / 'check.py').read_bytes()).hexdigest()
        self.assertEqual(hashlib.sha256((state / 'check.py').read_bytes()).hexdigest(), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
