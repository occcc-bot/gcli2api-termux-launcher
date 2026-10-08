# gcli2api Termux 启动器（第三方）

一个给 [su-kaka/gcli2api](https://github.com/su-kaka/gcli2api) 用的 Termux 中文菜单启动器，面向在手机上用 Termux + SillyTavern 的用户。

**本项目不是 gcli2api 的修改版，也不是它的官方组件。** 它不修改上游代码，只负责把上游的安装、启动、修复、更新串成一个人能看懂的数字菜单，并处理在中国大陆网络 + Termux 环境下实测遇到的兼容问题。

> 上游项目：<https://github.com/su-kaka/gcli2api>（作者 su-kaka，许可证 CNC-1.0）
> 所有实际的反代功能、控制面板和 API 都由上游项目提供。遇到功能或模型相关问题，请先查阅上游仓库和它的 issue。

## 它解决什么问题

上游的 `termux-install.sh` 和 `termux-start.sh` 本身不复杂，但在真实手机上会遇到一连串它没有覆盖的情况：安装脚本静默卡死、GitHub 和 Termux 源连不上、依赖版本互相冲突、`pm2` 装不上、装完之后不知道该在哪里填酒馆的地址。

这个启动器把上述处理固化下来，并在每一步之后做真实验证，不把「命令退出码为 0」当成成功。

具体已在真机复现并内置的修复见下方「[真机实测发现并已内置的修复](#真机实测发现并已内置的修复)」。

## 开始使用

在手机的 Termux 里：

```bash
pkg install -y git
git clone https://github.com/occcc-bot/gcli2api-termux-launcher.git
bash gcli2api-termux-launcher/launcher.sh
```

如果 GitHub 访问不稳定，可以用镜像克隆：

```bash
git clone https://ghfast.top/https://github.com/occcc-bot/gcli2api-termux-launcher.git
```

输入 `1` 首次安装，之后日常只输入 `2`。若没有 `curl`，先执行 `pkg install curl`。入口 `install.sh` 等同于菜单的首次安装选项：

```bash
bash gcli2api-termux-launcher/install.sh
```

**首次安装耗时较长。** 上游要下载约 139 MB 的 Termux 基础工具，并且 `pydantic-core` 在 Android 上没有预编译包，需要在手机上用 Rust 现场编译，通常需要 15–30 分钟（取决于网络和机器性能）。请保持 Termux 在前台并保持网络连接。

**开始之前请确认手机有浏览器**（Chrome / Chromium / Firefox 均可）。完成 Google OAuth 授权必须在浏览器里进行，没有浏览器就无法授权。部分精简系统只保留了系统 WebView 而没有可用的浏览器应用。

## 菜单与 CLI

| 数字 | CLI | 行为 |
|---|---|---|
| 1 | `install` | 新目录调用上游安装逻辑，记录日志并逐项验证；已安装则只启动 |
| 2 | `start` | 离线检查依赖并启动/复用本项目 PM2 进程，不下载依赖或更新代码 |
| 3 | `repair` | 离线诊断；只有明确缺失/导入失败/版本不符才用 uv 修复 |
| 4 | `guide` | 控制面板授权、酒馆地址/密钥位置、模型列表与手动测试说明 |
| 5 | `update` | 显式 `git pull --ff-only`，本地修改存在则中止；之后条件修复依赖 |
| 6 | `status` | 仅显示与项目路径匹配的后台进程状态 |
| 7 | `stop` | 仅停止与项目路径匹配的后台进程 |
| 8 | `logs` | 本项目日志位置及最近 32KB 已知错误类别，不输出任意原始日志 |
| 0 | — | 退出菜单 |

```bash
bash launcher.sh --help
bash launcher.sh start
bash launcher.sh guide
```

非交互命令失败返回非零退出码，菜单失败则保留在菜单中；输入结束自动退出。操作使用原子目录锁，避免同一状态目录的重复点击。静默命令每 10 秒显示简洁等待心跳，结束显示耗时及结果；**心跳只表示仍在等待，不表示 apt/下载确实有进展，命令退出 0 也不是安装成功证明**。按 Ctrl+C 或收到 SIGTERM 时，清理当前命令进程组（包括普通子进程）、等待回收并释放锁，退出码分别为 130/143；不会遗留心跳后台任务。不会因此执行全局 PM2 kill 或停止其他项目。上游自行脱离进程组的 daemon 不属于此清理范围，已有 PM2 服务仍按项目路径管理。

SIGKILL、Android 强杀或断电无法由脚本捕获；这种情况下确认没有仍在执行的操作后，手动移除状态目录里的空 `action.lock`。

## 安装与启动怎样验证

默认使用以下镜像来源，安装前显示实际选中的三个 URL：

- 上游脚本：`https://ghfast.top/https://raw.githubusercontent.com/su-kaka/gcli2api/master/termux-install.sh`
- 本项目仓库：`https://ghfast.top/https://github.com/su-kaka/gcli2api.git`
- Termux 主源：`https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main`

GitHub 下载镜像只负责安装/更新时的脚本和项目代码下载，**不承诺 Google OAuth 或模型请求可用**。PyPI、npm 来源暂不改动。

首次在专用安装父目录运行，项目目标必须是尚不存在的 `gcli2api` 子目录。绝不在已安装的项目里重跑上游安装/重启脚本，也不覆盖残留项目目录。

下载的原始脚本保存在 `$GCLI_STATE_DIR/upstream-install.sh`，不修改原文件；只执行生成的 `upstream-install-compatible.sh`。兼容副本保留精准 pgrep 补丁（将两处 `pgrep -f "apt|dpkg"` 替换为 `pgrep -x "apt|apt-get|dpkg"`，避免 Android adapt 进程误匹配），并精确替换本项目 `git clone` 地址、主源 target/fallback 变量和 sources.list 的硬编码 URL。主源检查改用固定字符串匹配，fallback 改为直接写入所选主源，避免动态 sed 正则。不会泛改其他仓库或设置全局 Git 配置。

网络下载的脚本必须完整匹配已审查的上述语句，每条恰好一处；上游语句变化或仍有未覆盖的已知硬编码时中止，不执行脚本、不宣称成功。只有 `file:///` 本地脚本且完全没有仓库/源配置标记时，才允许省略这些语句，供离线 fixture 使用。来源 URL 的校验不是脚本安全审计，镜像和自定义脚本仍须可信。

首次 curl 下载使用 `--connect-timeout 10 --max-time 60 --retry 1 --retry-max-time 125`：每次连接最多 10 秒、每次传输最多 60 秒，最多重试一次，通常总等待不超过约两分钟（另有心跳轮询/调度开销）。下载失败返回非零、删除不完整脚本且不执行上游。此阶段不需要 Python/uv/node/git/PM2。下载失败时核对已显示的实际来源在 Termux 中的可达性、DNS、TLS 和网络状态；浏览器能打开网页不等于 Termux 能下载脚本。自定义 URL 也需自行核对可达性；不会自动盲目换源，也不会仅凭失败断言具体网络原因。

上游输出仍写入私有日志，不直接显示可能含凭证的内容。执行前明确提示 apt/pkg 可能等待配置交互；本包不自动选择配置文件、不强制非交互覆盖配置。终端读取导致命令暂停时会中止并清理，而非无限等待；其他交互等待或网络卡顿会持续显示心跳，可 Ctrl+C 后在本机私下检查日志。不要把等待或命令退出码当作成功，也不要公开原始日志。

上游安装脚本会操作 Termux 软件源、安装系统工具，并在**首次新克隆目录**执行它自己的 git reset、uv init/uv add 等逻辑。本包不把这套逻辑用于日常启动、修复或更新。首次日志包括下载和上游安装的独立结果；脚本返回 0 不算安装成功，还需要：

1. Python、uv、node、git、PM2 工具存在；项目 `web.py` 和依赖清单存在。
2. `.venv/bin/python` 能执行且 Python >= 3.12。
3. 所有清单直接依赖的发行版元数据存在、版本符合清单、对应模块能导入，包括 HTTPX SOCKS 附加依赖。导入失败也能发现多数底层依赖/原生扩展故障。
4. 将上游临时产生的 `web` 进程按路径停止，再按统一流程启动 `gcli2api-launcher`。
5. PM2 中有匹配本项目路径的在线进程，且本地 `/openapi.json` 包含本项目 `/auth/login`、`/auth/start`、`/v1/chat/completions` 接口特征。不是任意 7861 返回 200 就算就绪。

依赖检查不联网，不导入/运行 `web.py`，不会请求 Google，也不会默认调用模型。当前上游清单的简单依赖语法全部支持；若未来变成复杂 URL、条件或多重版本表达式，诊断会明确中止，而非盲目修复。范围版本检查需要 venv 已有的 `packaging`；本包不会为诊断偷偷安装它。没有承诺递归校验所有传递依赖的全部版本约束。

**接口就绪不等于 Google 授权或模型可用。** 如果上游改变了接口路径或禁用 OpenAPI，探针会失败而非假报成功，需要调整探针。Google OAuth 必须由用户在浏览器里完成。

## 进程隔离与已有安装

本包自己的 PM2 实例放在 `$GCLI_STATE_DIR/pm2`，首次上游安装同样使用该 PM2_HOME。还会检查已有默认/指定旧 PM2 daemon 中的实例，以接管原先安装脚本产生的 `web` 或 `gcli2api`。

匹配依据是解析后的 `web.py` 完整路径，或本项目 venv Python 路径 + 项目工作目录 + `web.py` 参数，**绝不只看进程名**。不运行 `pm2 stop all/delete all/kill/save/startup`，不触碰用户酒馆和其他项目。已有唯一在线实例就复用；存在重复/停止实例时，只停止并移除确属本项目的实例，然后检查端口空闲，再创建统一实例。陌生端口占用不会被杀掉。

注意：就绪识别用 HTTP 接口特征及本项目 PM2 在线状态，并非操作系统层端口 PID 的强身份认证。启动前的端口检查可防止常见冲突，但不能消除检查与绑定之间的竞态。

已有安装示例（保留原目录）：

```bash
GCLI_APP_DIR="$HOME/gcli2api" bash launcher.sh start
```

日常启动不调用 git、uv、npm、pkg，不执行上游 `termux-start.sh`，也不 source `.env`。通过 PM2 管理的是后台进程，不保证 Android 永久保活。请手动在系统里允许 Termux 后台运行、关闭其电池优化；手机重启后仍需进入菜单启动。本包不自动修改系统后台设置。

## 真机实测发现并已内置的修复

以下问题均在一台 Android 13、aarch64 的手机（中国大陆网络）上实测复现，启动包已自动处理，无需用户手工操作：

| 现象 | 原因 | 启动包做法 |
|---|---|---|
| 安装长时间卡住、无输出 | 上游脚本用 `pgrep -f "apt\|dpkg"`，会误匹配 Android 桌面进程 `com.android.launcher3.adapt`，导致永久等待 | 在副本中改为精确匹配 `apt/apt-get/dpkg`，原始脚本保留 |
| 下载脚本/仓库、读取软件源失败 | 大陆网络访问 GitHub raw 与 Termux 官方源不稳定 | 默认使用 ghfast 下载源与清华 TUNA 主源 |
| `apt update` 被废弃源拖住 | 旧版环境残留 `game-packages-21-bin`、`science-packages-21-bin` | 仅停用这两条精确匹配的旧源并保留备份 |
| `node` 无法启动（缺 `libcrypto.so.3` 等） | APK bootstrap 自带的基础库比软件源中的新版工具旧 | 安装前先 `apt-get upgrade` 升级基础包 |
| `uv add` 超时 | 默认访问 pypi.org | 使用清华 PyPI 镜像（仅本次运行导出，不写用户配置文件） |
| `pm2` 未安装 | 上游用 npm 安装，访问 registry.npmjs.org 失败 | 失败后用 npmmirror 重试安装 |
| **`import fastapi` 失败：`cannot import name 'TypeAdapter'`** | **上游 `requirements-termux.txt` 固定 `pydantic==1.10.22`，而 Termux 的 Python 已是 3.14，pydantic v1 在 3.13+ 上直接抛 `ConfigError`** | 安装后检测到 pydantic v1 即替换为 v2 |
| 编译 `pydantic-core` 失败：`Failed to determine Android API level` | 安卓没有官方预编译 wheel，必须本地用 Rust 编译 | 自动安装 `rust`，写入 rsproxy crates 镜像，并设置 `ANDROID_API_LEVEL=24` |

对应地，健康检查会把「已安装的 pydantic v2」视为正常（上游清单写的是 v1），并额外真实导入 `fastapi`/`pydantic`，避免“元数据正确但导入失败”被误判为健康。首次安装后若依赖不健康，会自动进入修复流程，不需要用户再手动点一次。

**实测结果**：在真机上完成首次安装与启动，`/openapi.json` 返回 200，`/v1/models` 返回模型列表，控制面板可访问，PM2 中进程 `online`。**Google OAuth 授权、酒馆实际对话、模型调用仍未验证。**

编译 `pydantic-core` 在本机约需 15–30 分钟（取决于网络），这是首次安装最慢的一步。

## 旧 Termux 软件源兼容

首次安装会检查 `sources.list.d/game.list` 和 `science.list`。仅当它们各自只有一条软件源，且精确对应旧版 `termux.org/game-packages-21-bin` 或 `science-packages-21-bin` 时，改名停用并保留 `.gcli-disabled-时间戳` 备份，避免废弃源拖住 `apt update`。其他自定义软件源不改动。需要恢复时，把备份文件移回原文件名即可。

## 修复与更新

启动就绪后自动显示酒馆接入指引，不必再返回菜单选择 `4`。

依赖健康时 `repair` 不联网。依赖明确损坏时，重新安装依赖（避免已安装但文件损坏时被安装器直接跳过），仅执行：

```bash
uv pip install --reinstall --python /实际项目路径/.venv/bin/python -r requirements-termux.txt
```

venv 不存在时才使用 `uv venv --python python .venv` 创建。已有损坏 venv 不自动删除：可自行将它移到备份目录后重试。项目代码/清单缺失、未知清单语法、PM2/系统工具缺失不会冒充依赖问题自动处理。修复完成再离线检查，失败则保留原状与日志。

`update` 不运行 `git reset --hard`，不自动 stash，不删本地文件。上游首次脚本自身可能改写 `pyproject.toml` 或锁文件，导致更新前检查发现修改：启动器会解释并中止。请自行查看 `git diff`，提交或备份处理后再更新。本包不猜测哪些修改可以丢弃。

更新使用当前 Git 分支已配置的上游，要求 fast-forward。确认 Git 根目录就是本项目、且无已跟踪文件修改后，仅在本项目目录的本次 `git pull --ff-only` 命令上用 `git -c url.<所选仓库>.insteadOf=<本项目官方URL>` 映射下载地址，同时映射本包默认 ghfast 仓库 URL，便于已安装的默认镜像仓库改用 `GCLI_REPO_URL`。其他自定义 remote 不重写；不改变 remote、不写 local/global Git 配置。更新会显示所选镜像及映射范围。更新后仅依赖不健康才修复。不会自动重启旧进程，执行 `stop` 再 `start` 应用更新。

## 酒馆接入

**前置条件：手机需要一个浏览器**（Chrome / Chromium / Firefox 均可）。控制面板的 Google OAuth 授权必须在浏览器里完成，没有浏览器就无法授权。部分精简系统可能只保留了系统 WebView 而没有可用的浏览器应用，请先自行安装。

同一手机浏览器先打开 `http://127.0.0.1:7861`，登录并自行完成对应 GCLI/Antigravity OAuth。

酒馆进入 **插头/API 连接 → Chat Completion（聊天补全）→ Custom（自定义 OpenAI 兼容）**（版本不同名称略有差异）：

- GCLI 地址：`http://127.0.0.1:7861/v1`
- Antigravity 地址：`http://127.0.0.1:7861/antigravity/v1`，必须先授权对应模式
- 密钥栏：本项目 API 密码，不是 Google 登录密码
- 原始默认 API/面板密码为 `pwd`；如自定义 `.env`、环境变量、PM2 环境或面板配置，使用实际配置。`guide` 发现 `.env` 或当前密码环境变量时，只提示自行查看配置，不读取、不执行、不输出里面的密钥。
- 连接/刷新模型列表，选择账户实际可用模型，再**由用户在酒馆手动发一句“你好”**，看到正常回复才验证实际调用；本包不自动消耗模型额度。

401 核对 API 密码；403 核对授权和账户资格；429 查看额度；连接失败先看 status 和端口。`127.0.0.1` 仅适用于酒馆和反代同一设备。

## 可覆盖环境变量

目录请使用绝对路径：

| 变量 | 默认值/用途 |
|---|---|
| `GCLI_STATE_DIR` | `$HOME/.local/share/gcli2api-launcher`，锁、日志、独立 PM2_HOME |
| `GCLI_APP_DIR` | `$GCLI_STATE_DIR/install/gcli2api`，项目位置 |
| `GCLI_PORT` | `7861`，实际启动的 PORT 和本地探针端口；覆盖服务继承的 PORT |
| `GCLI_BASE_URL` | `http://127.0.0.1:$GCLI_PORT`，只修改接入指引地址，不修改探针/绑定 |
| `GCLI_INSTALL_URL` | 默认上述 ghfast 脚本 URL，可替换为已审查的脚本地址/绝对 `file:///` URL |
| `GCLI_REPO_URL` | 默认上述 ghfast 仓库 URL，用于首次克隆及本项目更新映射 |
| `GCLI_TERMUX_MIRROR` | 默认上述清华 Termux 主源，用于首次安装脚本的 target、fallback 和源配置 |
| `GCLI_PYTHON` | `python`，系统诊断/新建 venv 使用的 Python 可执行程序 |
| `GCLI_LEGACY_PM2_HOME` | 优先此变量，其次原 PM2_HOME，否则 `$HOME/.pm2` |
| `GCLI_READY_ATTEMPTS` | `15`，每轮探针最多 2 秒并间隔 1 秒 |

三个来源变量只接受 `https://` 或绝对 `file:///` URL（后者可用于本地离线测试）；拒绝空值、空格/换行、引号、反引号、美元符号、反斜杠等字符。替换使用字面字符串而非把 URL 当正则；克隆参数安全引用，不 eval。来源会显示，请勿在 URL 中放密钥。

本包不输出环境变量列表。原始安装/依赖/更新日志仅保存在状态目录，可能包含上游输出；菜单只显示日志位置/有限错误类别，分享诊断时无需复制全部日志。

## 离线测试

只用 Bash 和 Python 标准库，不实际安装软件，不联系互联网。PM2、git、uv、curl、系统安装工具全部模拟；HTTP 验证使用本机 loopback 测试服务。

```bash
cd gcli2api-termux-launcher
bash -n launcher.sh install.sh
python -m unittest discover -s tests -v
```

测试覆盖无下载启动、旧命名进程复用、项目路径隔离、重复进程/端口冲突、普通 HTTP 200 拒绝、健康/损坏/不确定依赖、ff-only 更新和保留修改、假成功安装拒绝、残留目录保护、无秘密指引及异常 PM2 状态。另覆盖只有 curl 的引导环境、下载超时参数及中文网络诊断、10 秒心跳/耗时结果且不泄漏原始日志、SIGINT/SIGTERM 清理子进程与锁、终端交互暂停拒绝假成功。另覆盖默认三项来源、raw 原文保存、完整镜像替换且不改其他仓库、上游语句变化拒绝执行、来源特殊字符校验及字面替换、命令级更新映射且无全局配置。下载失败由离线桩模拟，不声称验证了真实 DNS/TLS 或 Android 网络。

真实 Android Termux 首次安装、上游实时依赖、实际 Google 授权和酒馆调用仍需手机实测；离线测试不是这些步骤成功的保证。

## 来源与致谢

- 上游项目：[su-kaka/gcli2api](https://github.com/su-kaka/gcli2api)，作者 su-kaka，许可证 CNC-1.0。反代、控制面板、API 兼容层和凭证管理全部来自上游。
- 本启动器由第三方编写，与上游作者无关，未获上游背书。
- 上游安装脚本从上游仓库实时获取并执行，本启动器不内置、不重新分发上游代码。安装和更新时下载的是上游 `master` 分支的脚本与仓库。

## 许可证与免责声明

本启动器以 MIT 许可证发布，见 [LICENSE](LICENSE)。

注意：上游 gcli2api 采用 CNC-1.0（禁止商业使用）。本启动器只是调用上游脚本，但**使用本启动器就意味着你会安装并运行上游项目，因此同样受上游许可证约束**：不得用于商业用途、不得提供付费服务。请阅读上游 [LICENSE](https://github.com/su-kaka/gcli2api/blob/master/LICENSE)。

上游项目 README 声明其「仅供学习和研究用途」。本启动器不改变这一点，也不对使用上游项目产生的任何后果负责。

## 已知限制

- 本启动器不能代替 Google 账号授权，也不能让不可用的账号变得可用。
- 「接口就绪」不等于「模型可调用」；模型可用性取决于账号资格、额度与上游行为。
- PM2 管理的后台进程不保证 Android 永久保活，重启手机后需要重新启动。
- 上游代码变化可能导致兼容补丁失效；此时启动器会拒绝执行并报错，而不是继续跑一个结果不明的安装。
- 只在 Android 13 / aarch64 上做过真机验证，其他版本可能遇到未覆盖的情况。

## 问题反馈

如果卡在安装或启动，请提供：

- 手机 Android 版本、CPU 架构（`uname -m`）、Termux 版本
- 出错的菜单选项
- 状态目录下的日志**位置**（`$HOME/.local/share/gcli2api-launcher/logs/`）

**请不要直接粘贴完整原始日志。** 日志可能包含 Google 凭证、OAuth 回调码等敏感内容。启动器的菜单只显示日志位置和有限错误类别，就是为了避免这种情况。
