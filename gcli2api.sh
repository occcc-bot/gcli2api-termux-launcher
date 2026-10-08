#!/usr/bin/env bash
# No .env sourcing, no package/network operations in normal start.
set -u
umask 077
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Repo mode uses the sibling check.py; the single-file build embeds it below.
HELPER="$HERE/check.py"
# Empty means repo mode (sibling check.py); the single-file build substitutes the
# digest here, and that presence is what enables embedded mode.
EMBEDDED_HELPER_SHA256='70d70298a4e313b705b0aa605cce30c525e7fb1b6fbe78e4612c6daea6532464'
embedded_helper() {
    cat <<'GCLI_HELPER_EOF'
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
GCLI_HELPER_EOF
}
# In single-file mode, materialize the embedded helper into the state dir once
# and reuse it; verify its hash so a tampered copy is not silently trusted.
ensure_helper() {
    [[ -f "$HELPER" ]] && return 0
    [[ -n "$EMBEDDED_HELPER_SHA256" ]] || { fail '辅助模块缺失（非单文件构建，且目录内没有 check.py）。'; return 1; }
    local target="$GCLI_STATE_DIR/check.py" digest
    if [[ -f "$target" ]]; then
        digest=$(sha256sum "$target" 2>/dev/null | cut -d' ' -f1)
        [[ "$digest" == "$EMBEDDED_HELPER_SHA256" ]] && { HELPER="$target"; return 0; }
    fi
    mkdir -p "$GCLI_STATE_DIR" || { fail '无法创建状态目录，不能展开辅助模块。'; return 1; }
    embedded_helper >"$target.tmp.$$" || { fail '辅助模块展开失败。'; return 1; }
    digest=$(sha256sum "$target.tmp.$$" 2>/dev/null | cut -d' ' -f1)
    [[ "$digest" == "$EMBEDDED_HELPER_SHA256" ]] || { rm -f "$target.tmp.$$"; fail '内嵌辅助模块校验失败，未执行。'; return 1; }
    chmod 600 "$target.tmp.$$" && mv "$target.tmp.$$" "$target" || { fail '辅助模块写入失败。'; return 1; }
    HELPER="$target"
}
export GCLI_STATE_DIR="${GCLI_STATE_DIR:-$HOME/.local/share/gcli2api-launcher}"
export GCLI_APP_DIR="${GCLI_APP_DIR:-$GCLI_STATE_DIR/install/gcli2api}"
export GCLI_PORT="${GCLI_PORT:-7861}"
export GCLI_BASE_URL="${GCLI_BASE_URL:-http://127.0.0.1:$GCLI_PORT}"
OFFICIAL_REPO='https://github.com/su-kaka/gcli2api.git'
UPSTREAM="${GCLI_INSTALL_URL-https://ghfast.top/https://raw.githubusercontent.com/su-kaka/gcli2api/master/termux-install.sh}"
REPO_URL="${GCLI_REPO_URL-https://ghfast.top/https://github.com/su-kaka/gcli2api.git}"
TERMUX_MIRROR="${GCLI_TERMUX_MIRROR-https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main}"
PYPI_MIRROR="${GCLI_PYPI_MIRROR-https://pypi.tuna.tsinghua.edu.cn/simple}"
NPM_MIRROR="${GCLI_NPM_MIRROR-https://registry.npmmirror.com}"
SYS_PY="${GCLI_PYTHON:-python}"
# Upstream requirements-termux.txt pins pydantic==1.10.22, which cannot work on
# the Python 3.12+ that Termux now ships (v1 raises ConfigError on 3.13/3.14).
# Verified on device: pydantic v2 needs pydantic-core, which has no Android
# wheel, so it must be built locally with Rust + ANDROID_API_LEVEL.
PYDANTIC_SPEC="${GCLI_PYDANTIC_SPEC-pydantic>=2.11.7}"
CRATES_MIRROR="${GCLI_CRATES_MIRROR-https://rsproxy.cn/index/}"
ANDROID_API_LEVEL="${GCLI_ANDROID_API_LEVEL-24}"
fail() { printf '%s\n' "$*" >&2; return 1; }
helper() { ensure_helper || return 1; "$SYS_PY" "$HELPER" "$@"; }
need_app() {
    [[ -f "$GCLI_APP_DIR/web.py" && -f "$GCLI_APP_DIR/requirements-termux.txt" ]] || { fail '项目文件不完整；首次使用请选择安装。已有残留目录不会被覆盖，请先自行检查。'; return 1; }
}
health() {
    need_app || return 2
    [[ -x "$GCLI_APP_DIR/.venv/bin/python" ]] || { fail '虚拟环境 Python 缺失；请选择修复。'; return 1; }
    ensure_helper || return 1
    "$GCLI_APP_DIR/.venv/bin/python" "$HELPER" health
}
# Bash job control gives each logged command its own process group, without
# requiring Python, setsid, or other bootstrap dependencies.
ACTIVE_LOG_PID=''
cleanup_logged() {
    [[ -n "$ACTIVE_LOG_PID" ]] || return 0
    kill -TERM -- "-$ACTIVE_LOG_PID" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$ACTIVE_LOG_PID" 2>/dev/null || true
    wait "$ACTIVE_LOG_PID" 2>/dev/null || true
    ACTIVE_LOG_PID=''
}
interrupt_action() {
    printf '\n操作已中断；正在清理本次命令及其子进程，不宣称成功。\n' >&2
    cleanup_logged
    exit "$1"
}
in_dir() {
    local dir=$1; shift
    # Called only as run_logged's background worker; no extra shell between
    # the job-group leader and apt, so terminal stops are visible to jobs.
    cd "$dir" && exec "$@"
}
run_logged() {
    local label=$1; shift
    mkdir -p "$GCLI_STATE_DIR/logs" || return
    local log="$GCLI_STATE_DIR/logs/$label-$(date +%Y%m%d-%H%M%S)-$$.log"
    printf '正在执行 %s；原始日志仅保存在：%s\n' "$label" "$log"
    local started=$SECONDS last=$SECONDS rc monitor=0
    [[ $- == *m* ]] && monitor=1
    set -m
    # stdin is /dev/null so a package manager that tries to prompt gets EOF and
    # fails instead of stopping on SIGTTIN; stdout/stderr stay private.
    DEBIAN_FRONTEND=noninteractive "$@" </dev/null >"$log" 2>&1 &
    ACTIVE_LOG_PID=$!
    while kill -0 "$ACTIVE_LOG_PID" 2>/dev/null; do
        sleep 1
        # stdin is /dev/null, so a prompt gets EOF instead of SIGTTIN. If the
        # command still stops itself (SIGSTOP/SIGTSTP), do not wait forever.
        if [[ -n $(jobs -s) ]]; then
            fail "$label 被信号暂停（耗时 $((SECONDS - started)) 秒）；正在清理，不宣称成功。请在本机私下检查日志。"
            cleanup_logged
            (( monitor )) || set +m
            return 125
        fi
        if (( SECONDS - last >= 10 )); then
            printf '仍在等待 %s（已耗时 %s 秒）；可按 Ctrl+C 中断。\n' "$label" "$((SECONDS - started))"
            last=$SECONDS
        fi
    done
    wait "$ACTIVE_LOG_PID"
    rc=$?
    ACTIVE_LOG_PID=''
    (( monitor )) || set +m
    if [[ $rc == 0 ]]; then
        printf '%s 命令完成（耗时 %s 秒）；退出码 0 不代表安装/接口验证成功。\n' "$label" "$((SECONDS - started))"
    else
        fail "$label 执行失败（退出码 $rc，耗时 $((SECONDS - started)) 秒）；未输出可能包含凭证的原始日志。"
    fi
    return "$rc"
}
start() {
    health || { fail '启动已中止，不下载依赖；依赖问题用 repair，代码问题请检查项目。'; return 1; }
    command -v pm2 >/dev/null || { fail '缺少 PM2；请检查首次安装结果（不会自动安装系统软件）。'; return 1; }
    if helper online; then
        printf '已有唯一在线的本项目进程，复用，不重复启动。\n'
        helper ready || return
        guide
        return
    fi
    helper stop || return
    helper free || return
    helper delete || return
    (cd "$GCLI_APP_DIR" && PORT="$GCLI_PORT" PM2_HOME="$GCLI_STATE_DIR/pm2" pm2 start "$GCLI_APP_DIR/.venv/bin/python" --name gcli2api-launcher --interpreter none -- web.py) >"$GCLI_STATE_DIR/start.log" 2>&1 || { fail 'PM2 启动失败；选择日志查看位置。'; return 1; }
    helper ready || return
    printf '后台已启动。请手动设置 Android 允许 Termux 后台运行/关闭电池优化；不保证系统不会终止它。\n'
    guide
}
repair() {
    health
    local rc=$?
    [[ $rc != 0 ]] || { printf '依赖健康，无需修复；没有联网。\n'; return 0; }
    [[ $rc == 1 ]] || { fail '诊断不能确认是依赖损坏，不自动修复。'; return 1; }
    command -v uv >/dev/null || { fail '缺少 uv；请检查 Termux 中 uv 的安装。'; return 1; }
    need_app || return
    validate_sources || return
    export_package_mirrors
    if [[ ! -x "$GCLI_APP_DIR/.venv/bin/python" ]]; then
        if [[ -e "$GCLI_APP_DIR/.venv" ]]; then
            fail '已有损坏的 .venv，未删除；请自行移到备份目录后再修复。'; return 1
        fi
        run_logged venv in_dir "$GCLI_APP_DIR" uv venv --python "$SYS_PY" .venv || return
    fi
    run_logged dependencies in_dir "$GCLI_APP_DIR" uv pip install --reinstall --python "$GCLI_APP_DIR/.venv/bin/python" -r requirements-termux.txt || return
    # Upstream pins pydantic v1; modern Termux Python rejects it. Replace it
    # with v2 after the list install, compiling pydantic-core when needed.
    if pydantic_broken; then
        printf '检测到上游固定 pydantic v1，在当前 Python 上不可用；改用 pydantic v2。\n'
        ensure_rust || return
        run_logged pydantic in_dir "$GCLI_APP_DIR" uv pip install --python "$GCLI_APP_DIR/.venv/bin/python" "$PYDANTIC_SPEC" || return
    fi
    health || { fail '修复后检查仍未通过；保留现有文件，查看日志位置。'; return 1; }
    printf '依赖修复完成；没有修改 pyproject.toml，也没有自动重启服务。\n'
}
validate_sources() {
    local value
    # Restricted URL alphabet: no shell quoting/expansion, whitespace or controls.
    # file:/// is supported for offline fixtures/local audited inputs.
    for value in "$UPSTREAM" "$REPO_URL" "$TERMUX_MIRROR" "$PYPI_MIRROR" "$NPM_MIRROR" "$CRATES_MIRROR"; do
        [[ "$value" =~ ^(https://[A-Za-z0-9][A-Za-z0-9._:-]*(/[A-Za-z0-9._~:/?\&=%+#@-]*)?|file:///[A-Za-z0-9._~:/?\&=%+#@-]+)$ ]] || {
            fail '来源 URL 无效：仅支持合理的 https:// 或绝对 file:/// URL；不允许空白、引号、反引号、美元符号等字符。'; return 2;
        }
    done
}
show_sources() {
    printf '安装脚本来源：%s\n项目仓库来源：%s\nTermux 主源：%s\nPython 包源：%s\nNode 包源：%s\n' "$UPSTREAM" "$REPO_URL" "$TERMUX_MIRROR" "$PYPI_MIRROR" "$NPM_MIRROR"
}
# Mainland networks commonly cannot reach pypi.org/registry.npmjs.org; export
# index overrides for this run only, without writing uv/npm user config files.
export_package_mirrors() {
    export UV_DEFAULT_INDEX="$PYPI_MIRROR"
    export UV_INDEX_URL="$PYPI_MIRROR"
    export PIP_INDEX_URL="$PYPI_MIRROR"
    export npm_config_registry="$NPM_MIRROR"
    # pydantic-core has no Android wheel and is compiled here; point cargo at a
    # reachable crates mirror and tell its build script the Android API level.
    export CARGO_NET_GIT_FETCH_WITH_CLI=true
    export ANDROID_API_LEVEL="$ANDROID_API_LEVEL"
    if [[ -n ${HOME:-} ]]; then
        mkdir -p "$HOME/.cargo" 2>/dev/null || true
        if [[ ! -f "$HOME/.cargo/config.toml" ]]; then
            cat >"$HOME/.cargo/config.toml" <<EOF
[source.crates-io]
replace-with = "gcli-mirror"
[source.gcli-mirror]
registry = "sparse+$CRATES_MIRROR"
[net]
git-fetch-with-cli = true
EOF
            printf '已写入 cargo 镜像配置（仅新建时）：%s\n' "$HOME/.cargo/config.toml"
        fi
    fi
}
# The pinned pydantic v1 in the upstream list is unusable on modern Termux
# Python. Detect it by importing pydantic and reading its major version.
pydantic_broken() {
    local py="$GCLI_APP_DIR/.venv/bin/python"
    [[ -x "$py" ]] || return 1
    "$py" - <<'PY' >/dev/null 2>&1
import sys
import pydantic
sys.exit(0 if pydantic.VERSION.startswith('1.') else 1)
PY
}
# Termux ships Rust only via the package manager; install it on demand because
# building pydantic-core is impossible without it.
ensure_rust() {
    [[ -n ${PREFIX:-} ]] || return 0
    command -v rustc >/dev/null && command -v cargo >/dev/null && return 0
    command -v pkg >/dev/null || { fail '缺少 Rust 且没有 pkg，无法编译 pydantic-core。'; return 1; }
    printf '正在安装 Rust（编译 pydantic-core 需要，仅首次）...\n'
    run_logged rust in_dir "$PREFIX" pkg install -y rust || return
    command -v rustc >/dev/null || { fail 'Rust 安装后仍不可用。'; return 1; }
}
# The APK bootstrap can ship base libraries older than the current mirror
# packages (e.g. node fails to link against an old libcrypto). Upgrading the
# base packages first is what makes node/openssl consistent. Termux only.
upgrade_base() {
    [[ -n ${PREFIX:-} ]] || return 0
    command -v apt-get >/dev/null || { fail '缺少 apt-get，无法升级基础包。'; return 1; }
    printf '正在升级 Termux 基础包（修复 node/openssl 等基础库版本不一致）...\n'
    run_logged upgrade-base in_dir "$PREFIX" apt-get -y \
        -o Dpkg::Options::=--force-confold -o Dpkg::Options::=--force-overwrite upgrade || return
}
prepare_upstream() {
    local raw="$GCLI_STATE_DIR/upstream-install.sh"
    local compatible="$GCLI_STATE_DIR/upstream-install-compatible.sh"
    local old='pgrep -f "apt|dpkg"' new='pgrep -x "apt|apt-get|dpkg"'
    local content count
    content=$(<"$raw")
    count=$(grep -cF "$old" "$raw") || true
    case "$count" in
        0) cp "$raw" "$compatible" || return;;
        2)
            content=${content//"$old"/"$new"}
            printf '%s\n' "$content" >"$compatible" || return
            printf '已应用兼容补丁：精确检测 apt/apt-get/dpkg，避免误匹配 Android adapt 桌面进程；原始上游脚本保留。\n';;
        *) fail '上游包管理器检测代码已变化，兼容补丁无法确定应用；未执行脚本。'; return 1;;
    esac
    # Match the reviewed upstream statements literally, never interpret URL as regex.
    local clone='git clone https://github.com/su-kaka/gcli2api.git'
    local target='target_mirror="https://packages-cf.termux.dev/apt/termux-main"'
    local fallback='fallback_mirror="https://packages.termux.dev/apt/termux-main"'
    local source='deb https://packages-cf.termux.dev/apt/termux-main stable main'
    local probe='grep -q "$target_mirror"'
    local switch='sed -i "s#${target_mirror}#${fallback_mirror}#g" "$PREFIX/etc/apt/sources.list" || true'
    local statement rest matches
    content=$(<"$compatible")
    # Only local file fixtures with no upstream markers may omit these statements.
    # Network scripts (including custom URLs) must match the entire reviewed shape.
    if [[ "$UPSTREAM" != file:///* || "$content" == *gcli2api.git* || "$content" == *termux-main* || "$content" == *target_mirror* || "$content" == *fallback_mirror* || "$content" == *sources.list* || "$content" == *'git clone'* ]]; then
        for statement in "$clone" "$target" "$fallback" "$source" "$probe" "$switch"; do
            rest=$content; matches=0
            while [[ "$rest" == *"$statement"* ]]; do
                rest=${rest#*"$statement"}; ((matches += 1))
            done
            [[ $matches == 1 ]] || { fail '上游仓库/主源语句已变化，无法确认完整镜像替换；未执行脚本。'; return 1; }
        done
        # Do not accept a changed command merely because it contains a known
        # prefix (e.g. another repository with a suffix or extra clone options).
        local line exact
        for statement in "$clone" "$target" "$fallback" "$source" "$switch"; do
            exact=0
            while IFS=$' \t' read -r line; do
                [[ "$line" != "$statement" ]] || ((exact += 1))
            done <<< "$content"
            [[ $exact == 1 ]] || { fail '上游仓库/主源语句已变化，无法确认完整镜像替换；未执行脚本。'; return 1; }
        done
        content=${content//"$clone"/"git clone '$REPO_URL'"}
        content=${content//"$target"/"target_mirror='$TERMUX_MIRROR'"}
        content=${content//"$fallback"/"fallback_mirror='$TERMUX_MIRROR'"}
        content=${content//"$source"/"deb $TERMUX_MIRROR stable main"}
        content=${content//"$probe"/'grep -Fq "$target_mirror"'}
        # Rewriting sources directly also avoids the upstream sed regex injection.
        content=${content//"$switch"/'printf "deb %s stable main\n" "$fallback_mirror" > "$PREFIX/etc/apt/sources.list" || true'}
        rest=${content//"$REPO_URL"/}
        rest=${rest//"$TERMUX_MIRROR"/}
        [[ "$rest" != *'https://packages-cf.termux.dev/apt/termux-main'* && "$rest" != *'https://packages.termux.dev/apt/termux-main'* && "$rest" != *"$OFFICIAL_REPO"* ]] || {
            fail '上游仍有未覆盖的仓库/主源硬编码；未执行脚本。'; return 1;
        }
        printf '%s\n' "$content" >"$compatible" || return
        printf '兼容副本已替换本项目克隆地址、主源 target/fallback/源配置；原始脚本保留。\n'
    fi
    bash -n "$compatible"
}
retire_legacy_sources() {
    [[ -n ${PREFIX:-} ]] || return 0
    local name expected file count backup
    for name in game science; do
        file="$PREFIX/etc/apt/sources.list.d/$name.list"
        [[ -f "$file" ]] || continue
        if [[ "$name" == game ]]; then
            expected='deb https://termux.org/game-packages-21-bin games stable'
        else
            expected='deb https://termux.org/science-packages-21-bin science stable'
        fi
        count=$(grep -cE '^[[:space:]]*deb(-src)?[[:space:]]' "$file") || true
        if [[ "$count" == 1 ]] && grep -Fqx "$expected" "$file"; then
            backup="$file.gcli-disabled-$(date +%Y%m%d-%H%M%S)-$$"
            mv "$file" "$backup" || return
            printf '已停用旧版 Termux 的废弃 %s 源，配置保留在：%s\n' "$name" "$backup"
        fi
    done
}
install() {
    if [[ -f "$GCLI_APP_DIR/web.py" ]]; then
        # A previously interrupted install can leave a cloned project without
        # working dependencies. Verify first; do not silently start a broken app.
        if health >/dev/null 2>&1; then
            printf '已有项目且依赖健康，安装不会重跑上游脚本；正在按已安装启动。\n'
            start; return
        fi
        printf '检测到已有项目但依赖不健康，可能是上次安装中断；正在修复依赖，不重跑上游安装。\n'
        repair || { fail '已有项目依赖修复失败；请查看上述诊断与日志位置。'; return 1; }
        start; return
    fi
    [[ ! -e "$GCLI_APP_DIR" ]] || { fail '目标目录已存在但项目不完整，未覆盖；请检查残留安装。'; return 1; }
    [[ $(basename -- "$GCLI_APP_DIR") == gcli2api ]] || { fail '首次安装的 GCLI_APP_DIR 必须以 /gcli2api 结尾。'; return 1; }
    local parent
    parent=$(dirname -- "$GCLI_APP_DIR")
    mkdir -p "$parent" || return
    # Upstream decides clone location based on cwd. Never run in a user repository.
    [[ ! -f "$parent/web.py" && ! -e "$parent/.git" ]] || { fail '安装父目录不是隔离目录，请使用专用空目录。'; return 1; }
    command -v curl >/dev/null || { fail '缺少 curl；请先在 Termux 执行 pkg install curl。'; return 1; }
    validate_sources || return
    show_sources
    export_package_mirrors
    local download_rc=0
    run_logged download curl -fL --connect-timeout 10 --max-time 60 --retry 1 --retry-max-time 125 -o "$GCLI_STATE_DIR/upstream-install.sh" "$UPSTREAM" || download_rc=$?
    if [[ $download_rc != 0 ]]; then
        rm -f "$GCLI_STATE_DIR/upstream-install.sh"
        fail '首次脚本下载失败，未执行上游安装。请核对上方实际来源在 Termux 中的可达性、DNS、TLS 和网络状态；不会盲目换源，不能仅凭失败确定原因。'
        fail '浏览器能打开网页不等于 Termux 能下载该脚本；自定义地址请先自行审查可信性及可达性。'
        return "$download_rc"
    fi
    bash -n "$GCLI_STATE_DIR/upstream-install.sh" || { fail '下载的上游脚本语法无效。'; return 1; }
    prepare_upstream || return
    retire_legacy_sources || return
    upgrade_base || return
    local rc=0
    printf '即将执行上游安装：输出仅写入私有日志，心跳只表示命令仍未结束，不表示安装有进展。\n'
    printf '上游 apt/pkg 以非交互方式执行：需要人工回答配置问题时会直接失败并保留日志，不会挂起等待；勿公开含凭证的原始日志。\n'
    PM2_HOME="$GCLI_STATE_DIR/pm2" run_logged upstream in_dir "$parent" bash "$GCLI_STATE_DIR/upstream-install-compatible.sh" || rc=$?
    # Upstream installs pm2 via npm; retry through the configured npm mirror so
    # a mainland network failure does not silently drop process management.
    if ! command -v pm2 >/dev/null && command -v npm >/dev/null; then
        printf 'PM2 未安装，改用镜像重试 npm 全局安装。\n'
        run_logged pm2 in_dir "$parent" npm install -g pm2 --registry "$NPM_MIRROR" || true
    fi
    local missing=0 tool
    for tool in "$SYS_PY" uv node git pm2; do
        command -v "$tool" >/dev/null || { fail "首次安装缺少工具：$tool"; missing=1; }
    done
    [[ $missing == 0 ]] || return 1
    need_app || return
    local repo_top app_real
    repo_top=$(cd "$GCLI_APP_DIR" && git rev-parse --show-toplevel 2>/dev/null) || { fail '安装后的 Git 仓库检查失败。'; return 1; }
    app_real=$(cd "$GCLI_APP_DIR" && pwd -P) || return
    [[ "$repo_top" == "$app_real" ]] || { fail '安装结果不是独立项目仓库，不宣称成功。'; return 1; }
    # Upstream's "web" instance is not trusted as readiness evidence. Quiesce
    # only exact-path instances, then canonical start checks the port first.
    helper stop || return
    if ! health; then
        printf '上游安装完成，但依赖检查未通过（常见原因：上游固定的 pydantic v1 在 Termux 新 Python 上不可用）；正在自动修复。\n'
        repair || { fail '自动修复失败，未宣称安装成功；请查看上述诊断与日志位置。'; return 1; }
    fi
    [[ $rc == 0 ]] || { fail '上游安装出现错误，即使依赖检查通过也不宣称完整成功。'; return 1; }
    printf '项目文件、运行工具和依赖已验证；接着验证后台接口。\n'
    start
}
update() {
    need_app || return
    command -v git >/dev/null || { fail '缺少 git。'; return 1; }
    local dirty top actual
    top=$(cd "$GCLI_APP_DIR" && git rev-parse --show-toplevel 2>/dev/null) || { fail '项目不是有效 Git 仓库，未更新。'; return 1; }
    actual=$(cd "$GCLI_APP_DIR" && pwd -P) || return
    [[ "$top" == "$actual" ]] || { fail 'Git 仓库根目录与项目目录不一致，未更新其他仓库。'; return 1; }
    dirty=$(cd "$GCLI_APP_DIR" && git status --porcelain --untracked-files=no) || return
    [[ -z "$dirty" ]] || { fail '存在已跟踪文件的本地修改，更新已中止且保留修改。上游安装可能通过 uv init/uv add 修改 pyproject.toml 或锁文件；请自行审查 git diff 并提交/备份处理，然后重新更新。不会 reset 或自动 stash。'; return 1; }
    validate_sources || return
    printf '更新仓库镜像：%s（仅映射本项目官方仓库 URL；其他 remote 保持原状）\n' "$REPO_URL"
    # Per-command mapping, scoped to the verified project cwd and exact official
    # URL; no persistent local/global config or unrelated repository rewriting.
    run_logged update in_dir "$GCLI_APP_DIR" git -c "url.$REPO_URL.insteadOf=$OFFICIAL_REPO" -c "url.$REPO_URL.insteadOf=https://ghfast.top/$OFFICIAL_REPO" pull --ff-only || return
    repair || return
    printf '代码更新和依赖检查完成；运行中服务仍是旧进程。执行 stop 后 start 应用更新。\n'
}
guide() {
    printf '\n0. 前置：手机需要有一个浏览器（Chrome/Chromium/Firefox 均可）用于完成 Google 授权；\n   若手机没有浏览器，请先安装一个，否则无法完成授权。\n'
    printf '1. 本手机浏览器打开控制面板：%s\n' "$GCLI_BASE_URL"
    printf '   自行登录并完成 Google OAuth 授权，启动包不能代办授权。\n'
    if [[ -f "$GCLI_APP_DIR/.env" ]] || [[ -n ${PASSWORD+x} || -n ${API_PASSWORD+x} || -n ${PANEL_PASSWORD+x} ]]; then
        printf '   检测到 .env 或密码环境配置：请自行查看配置中的 PASSWORD/API_PASSWORD/PANEL_PASSWORD；本工具不读取或显示密钥。\n'
    else
        printf '   未检测到密码配置文件/当前密码环境变量，原始默认面板密码和 API 密钥均为 pwd；若曾在面板或 PM2 中修改，以实际配置为准。\n'
    fi
    printf '2. 酒馆：插头/API 连接 → Chat Completion（聊天补全）→ Custom（自定义 OpenAI 兼容）；不同版本名称略有差异。\n'
    printf '   GCLI API 地址：%s/v1\n' "${GCLI_BASE_URL%/}"
    printf '   Antigravity API 地址：%s/antigravity/v1（须先完成对应模式授权）\n' "${GCLI_BASE_URL%/}"
    printf '   密钥栏填本项目 API 密码，不是 Google 密码；不要填完整 /chat/completions 路径。\n'
    printf '3. 连接/刷新模型列表，选择账户实际可用的模型；模型列表不保证调用成功。\n'
    printf '4. 由你在酒馆手动发一句“你好”，看到正常回复才算实际模型测试通过；启动包不默认消耗模型额度。\n'
    printf '   401：核对 API 密码；403：核对授权/账户资格；429：查看额度；无法连接：先看 status/端口。\n'
    printf '5. 本地接口就绪不等于 Google 授权成功/模型可用。127.0.0.1 仅适用于酒馆与反代同一设备。\n'
    printf '   修改端口请用 GCLI_PORT；自定义指引地址用 GCLI_BASE_URL，两者应与实际服务一致。\n'
}
usage() {
    printf '用法：bash launcher.sh [install|start|repair|update|status|stop|logs|guide|--help]\n不带参数进入中文数字菜单。正常 start 不执行 git/uv/npm/pkg。\n'
}
main() {
    case "${1:-}" in
        --help|-h) usage; return;;
        guide) guide; return;;
        install|start|repair|update|status|stop|logs)
            [[ "$GCLI_STATE_DIR" == /* && "$GCLI_APP_DIR" == /* ]] || { fail 'GCLI_STATE_DIR 和 GCLI_APP_DIR 请使用绝对路径。'; return 2; }
            [[ "$GCLI_PORT" =~ ^[0-9]+$ ]] && (( 10#$GCLI_PORT >= 1 && 10#$GCLI_PORT <= 65535 )) || { fail 'GCLI_PORT 必须是 1—65535 的端口数字。'; return 2; }
            mkdir -p "$GCLI_STATE_DIR" || return
            # mkdir is an atomic, offline lock. SIGINT/TERM clean up the active command.
            mkdir "$GCLI_STATE_DIR/action.lock" 2>/dev/null || { fail '另一个操作正在执行，或上次中断留下 action.lock；确认无操作后手动移除此目录。'; return 1; }
            trap 'cleanup_logged; rmdir "$GCLI_STATE_DIR/action.lock" 2>/dev/null || true' EXIT
            trap 'interrupt_action 130' INT
            trap 'interrupt_action 143' TERM
            case "$1" in
                status|stop|logs) command -v pm2 >/dev/null || { fail '未安装 PM2。'; return 1; }; helper "$1";;
                *) "$1";;
            esac
            local rc=$?
            rmdir "$GCLI_STATE_DIR/action.lock" 2>/dev/null || true
            trap - EXIT INT TERM
            return "$rc";;
        '')
            local choice action
            while true; do
                printf '\n=== gcli2api 手机启动包 ===\n1 首次安装\n2 启动（不下载）\n3 诊断/修复依赖\n4 酒馆接入指引\n5 显式更新\n6 后台状态\n7 停止本项目\n8 日志位置/摘要\n0 退出\n'
                read -r -p '输入数字：' choice || return 0
                case "$choice" in
                    1) action=install;; 2) action=start;; 3) action=repair;; 4) action=guide;;
                    5) action=update;; 6) action=status;; 7) action=stop;; 8) action=logs;;
                    0) return;; *) printf '请输入 0—8。\n'; continue;;
                esac
                main "$action" || printf '操作未完成，按上述诊断处理。\n'
            done;;
        *) usage; return 2;;
    esac
}
main "$@"
