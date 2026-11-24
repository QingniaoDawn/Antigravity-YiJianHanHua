# -*- coding: utf-8 -*-
"""
Antigravity 一键汉化 __VERSION__（Windows / macOS / Linux 三平台）
================================================
用法：
  python localize.py            # 交互菜单
  python localize.py apply      # 直接汉化
  python localize.py restore    # 恢复官方英文原版
  python localize.py status     # 查看状态
  python localize.py chinese-output   # AI 回复中文化（写入全局规则）
  python localize.py update           # 检查更新（查 GitHub Release，只下载不替换）
  可选：--path <安装目录>  --yes（免确认）  --no-kill（仅 Linux：不关进程直接热替换）

流程：杀进程 → 备份 → 解包 → 注入 → 语法门禁 → 打包 → 部署后复检 → 原子替换
      →（macOS 追加）代码签名重签 → 启动
安全：汉化 / 还原 / 状态 / AI 中文化全程仅本地操作，无任何网络行为；唯一例外是
      「检查更新」，只有你主动点它才会连一次 GitHub（只查版本号，不自动替换）。
      所有修改幂等可重入；备份永不降级。

跨平台说明：
  本脚本为 Windows / macOS / Linux 三平台共用，按当前系统自动走对应分支。

  三平台共通的差异处理：
    1. 安装路径：按平台各自的常见位置自动探测，找不到时用 --path 指定
    2. 进程管理：三平台都按**可执行文件真实路径**精确匹配，绝不误杀同名进程
       （Windows 比对 exe 路径；macOS 比对 .app 内二进制路径；
        Linux 读 /proc/<pid>/exe 内核符号链接）

  macOS 特有的两处：
    1. 安装路径：/Applications/Antigravity.app/Contents/Resources
    2. 代码签名：改 asar 会使 Apple 签名失效，必须重签，否则 App 打不开
       （Apple Silicon 上直接启动失败）。重签采用 Apple 推荐的分层方式：
       嵌套组件裸签 → 主包带 entitlements 签。
       主包额外用 -r 写入稳定的代码身份（只锚定签名标识），否则 ad-hoc
       默认身份锚定在二进制哈希上，Squirrel 会拒绝官方应用内更新。
       写入失败自动回退普通重签；全程需管理员密码。

  Linux 特有的三处：
    1. 原子热替换：POSIX os.replace() 在同文件系统内是原子的，所以进程没退出
       也能换掉 app.asar，重启一次应用即生效（--no-kill，或在交互提示里选 h）。
       在 Antigravity 内置终端里运行时，脚本会回溯 /proc/<pid>/status 的 PPid
       祖先链，自动识别「杀了就等于杀自己」，直接走热替换。
    2. 系统目录提权：装在 /opt、/usr/lib 这类普通用户无写权限的位置时，
       解包/注入/语法门禁/打包复检全在临时目录完成，只有最后写备份与原子替换
       一步才调 sudo / pkexec。写入权限位沿用目标文件的原有 mode。
    3. 启动方式：剥离 ELECTRON_RUN_AS_NODE 后以独立会话（setsid）后台启动，
       避免 Electron 应用被当成 Node 解释器。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

# 控制台编码自适应：Windows 中文版默认代码页 936(GBK)，无法表示部分符号
# （如 ✗ ⚠），若不处理会显示成问号或触发 UnicodeEncodeError。
# 策略：优先用 UTF-8 输出（Windows 10 起支持良好）；若终端明确是 GBK，
# 则把不可表示的符号降级为等价的 ASCII/中文写法，确保任何环境下都不乱码。
def _console_supports(ch):
    enc = (getattr(sys.stdout, "encoding", None) or "utf-8").lower()
    try:
        ch.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


_GLYPH = {
    # 降级值为纯 ASCII 且不含方括号，避免与外层 [tag] 叠加成 [[OK]]
    "✓": "v",   # ✓ 对勾 → v（checkmark 近似）
    "✗": "x",        # ✗ 叉号 → x
    "△": "!",        # △ 三角 → !
    "·": "-",          # · 间隔号 → -
    "→": "->",         # → 箭头 → ->
    "⚠": "!",        # ⚠ 警告 → !
    "★": "*",          # ★ 星 → *
}


def _fit_glyphs(text):
    """把文本里当前终端无法表示的符号替换为等价写法。
    替换值已是纯 ASCII，因此不会出现重复括号。"""
    out = text
    for ch, alt in _GLYPH.items():
        if ch in out and not _console_supports(ch):
            out = out.replace(ch, alt)
    return out


# ---------------------------------------------------------------------------
# 注意：不要在这里给 stdout 改 encoding。改动前先读完这段。
#
# 曾经尝试过"把 stdout 编码对齐到控制台代码页(cp936)"，结果反而制造了乱码，已回滚。
# 原因：Windows 上 Python 3.6+ 的控制台输出走 _WindowsConsoleIO，内部链路是
#     文本 → encode(utf-8) → 字节 → decode(utf-8) → WriteConsoleW(宽字符)
# 也就是说中文能不能正常显示，跟控制台代码页 936 没有关系，靠的是这套宽字符链路。
#
# 一旦把 encoding 改成 cp936，TextIOWrapper 吐出的就是 GBK 字节，
# _WindowsConsoleIO 仍然按 UTF-8 去解，解出来全是方块——比不改更糟。
# 实测症状：bat 自己 echo 的中文正常，Python 打出来的菜单全是"□□□"。
#
# 结论：不要动 encoding。只允许加 errors="replace"，避免极端字符把程序打崩。
# ---------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

# ----------------------------------------------------------------------------
# 常量与内置资源读取（支持源码目录直跑 & 单文件编译版 zipapp 内置读取）
# ----------------------------------------------------------------------------
def _resolve_script_dir():
    f = os.path.abspath(__file__)
    d = os.path.dirname(f)
    if os.path.isdir(d):
        return d
    # 单文件 zipapp / 可执行包：__file__ 形如 /path/to/binary/localize.py
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    return d or os.path.dirname(os.path.abspath(sys.argv[0]))


def _find_bundled_archive():
    f = os.path.abspath(__file__)
    d = os.path.dirname(f)
    while d and not os.path.exists(d):
        d = os.path.dirname(d)
    if d and os.path.isfile(d):
        return d
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv else ""
    if argv0 and os.path.isfile(argv0):
        return argv0
    return None


SCRIPT_DIR = _resolve_script_dir()
ENGINE_PATH = os.path.join(SCRIPT_DIR, "engine.js")
DATA_PATH = os.path.join(SCRIPT_DIR, "zh_data.json")


def load_asset_text(filename, disk_path):
    """优先读取同目录下的外部文件（方便用户自定义 zh_data.json）；
    若外部文件不存在且当前为单文件编译版，则从内置归档中读取。"""
    if os.path.isfile(disk_path):
        with open(disk_path, encoding="utf-8") as f:
            return f.read()
    arch = _find_bundled_archive()
    if arch:
        import zipfile
        try:
            if zipfile.is_zipfile(arch):
                with zipfile.ZipFile(arch, "r") as zf:
                    if filename in zf.namelist():
                        return zf.read(filename).decode("utf-8")
        except Exception:
            pass
    return None

ENGINE_START = "// ===== [AGY_ZH_ENGINE_START] Antigravity 一键汉化引擎（整合增强版）====="
ENGINE_END = "// ===== [AGY_ZH_ENGINE_END] ====="
MENU_START = "// ===== [AGY_ZH_MENU_START] 原生菜单翻译 ====="
MENU_END = "// ===== [AGY_ZH_MENU_END] ====="
TRAY_START = "// ===== [AGY_ZH_TRAY_START] 托盘菜单翻译 ====="
TRAY_END = "// ===== [AGY_ZH_TRAY_END] ====="
CTX_START = "// ===== [AGY_ZH_CTXMENU_START] 原生右键菜单翻译 ====="
CTX_END = "// ===== [AGY_ZH_CTXMENU_END] ====="

TOOL_VERSION = "v1.19.1"               # 工具版本号（唯一数据源，改这里即可全局生效）
TESTED_VERSION = "2.34.0"          # 深度适配验证过的版本（Windows 真机实测：2.21.1 / 2.23.1 / 2.24.0 / 2.25.0 / 2.27.0 / 2.28.1 / 2.28.2 / 2.31.0 / 2.31.2 / 2.33.0；2.34.0 为离线全量验证 + 运行态抽查）
EXIT_CANCELLED = 130                # 用户主动中断（Ctrl+C / 输入流断开）。批处理据此区分"取消"和"出错"
MARKER_CHECK = b"AGY_ZH_ENGINE_START"   # 用于字节级判断 asar 是否已被汉化
NODE_OK = False                     # check_env() 探测结果：node 不仅存在，而且实测能跑起来
NODE_BIN = None                     # 可用的 node 绝对路径。node_check 一律用它，不用裸 "node"
NODE_PROBED = False                 # check_env() 是否已执行过（用来区分"主动降级"和"漏调用"）
NODE_INFO = "尚未检测"               # 一句话描述探测结果，写进失败日志供排障

# --- 检查更新（全程序唯一的联网入口，仅当用户主动点「检查更新」才会触发）---
UPDATE_REPO = "QingniaoDawn/Antigravity-YiJianHanHua"
UPDATE_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
UPDATE_PAGE = f"https://github.com/{UPDATE_REPO}/releases/latest"
UPDATE_TIMEOUT = 6          # 查版本号的超时（秒）：查不到就当没更新，绝不能卡住用户
DOWNLOAD_TIMEOUT = 30       # 下载时单块读取超时（秒）
DL_CHUNK = 65536            # 下载分块大小

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

WIN_PATHS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "antigravity"),
    r"C:\Program Files\Antigravity",
    r"C:\Program Files (x86)\Antigravity",
]
# macOS：.app 包结构。Contents/Resources 下才有 app.asar（Electron 官方标准布局）。
# 除标准位置外，还要覆盖 macOS 上真实存在的几种安装方式：
#   - ~/Applications：用户自装
#   - ~/Downloads / ~/Desktop：直接从 dmg 或浏览器下载后未拖入"应用程序"
#   - /Volumes/*：dmg 挂载后直接运行（未安装）
#   - /opt/homebrew/Caskroom、/usr/local/Caskroom：Homebrew cask 安装
MAC_PATHS = [
    "/Applications/Antigravity.app/Contents/Resources",
    os.path.expanduser("~/Applications/Antigravity.app/Contents/Resources"),
    os.path.expanduser("~/Downloads/Antigravity.app/Contents/Resources"),
    os.path.expanduser("~/Desktop/Antigravity.app/Contents/Resources"),
]
# 需要在运行时展开通配的路径（dmg 挂载点、Homebrew 等）
MAC_PATTERNS = [
    "/Volumes/*/Antigravity.app/Contents/Resources",
    "/opt/homebrew/Caskroom/antigravity/*/Antigravity.app/Contents/Resources",
    "/usr/local/Caskroom/antigravity/*/Antigravity.app/Contents/Resources",
    "/Applications/*.app/Contents/Resources",       # 应用名大小写/变体
]
LINUX_PATHS = [
    os.path.expanduser("~/.local/share/antigravity/resources"),
    os.path.expanduser("~/.local/share/Antigravity/resources"),
    "/opt/Antigravity/resources",
    "/opt/antigravity/resources",
    "/usr/lib/antigravity/resources",
    "/usr/share/antigravity/resources",
    "/usr/local/share/antigravity/resources",
    os.path.expanduser("~/Antigravity/resources"),
    os.path.expanduser("~/antigravity/resources"),
]

PROC_NAMES = ["Antigravity.exe", "language_server.exe"]


def log(msg, ok=False, warn=False):
    """统一日志出口。图标按终端编码能力自适应：
    UTF-8 终端显示 ✓ ·，GBK 等旧终端自动降级为纯 ASCII，任何环境都不乱码。"""
    if ok:
        tag = _fit_glyphs("[✓]")
    elif warn:
        tag = "[!]"
    else:
        tag = _fit_glyphs("[·]")
    print(_fit_glyphs(f"{tag} {msg}"), flush=True)


# 文件名必须是纯 ASCII：中文文件名在英文代码页(437)的控制台里显示成乱码，
# 用户根本找不到这个文件，也就没法发给我们。中文说明写在文件内容里。
FAIL_LOG_NAME = "hanhua_error_log.txt"


def _collect_env_snapshot():
    """收集一份最小但够用的环境快照。用户描述不清时，全靠这份东西定位。"""
    import platform
    lines = []
    try:
        lines.append(f"时间      : {time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"工具版本  : {TOOL_VERSION}")
        lines.append(f"系统      : {platform.platform()}")
        lines.append(f"架构      : {platform.machine()} / Python {platform.python_version()}")
        lines.append(f"解释器    : {sys.executable}")
        lines.append(f"输出编码  : {getattr(sys.stdout, 'encoding', '?')}")
        lines.append(f"工作目录  : {os.getcwd()}")
        lines.append(f"脚本目录  : {SCRIPT_DIR}")
        lines.append(f"Node探测  : {NODE_INFO}")
        lines.append(f"Python路径: {os.environ.get('PATH', '')[:600]}")
    except Exception as e:
        lines.append(f"(环境快照收集失败: {e})")
    return lines


def _write_fail_log(msg):
    """失败时落一份日志到脚本目录。写不下去就算了，绝不能因为写日志再抛异常。"""
    try:
        body = ["=== Antigravity 一键汉化 · 失败记录 ===", ""]
        body += _collect_env_snapshot()
        body += ["", "--- 错误信息 ---", str(msg), ""]
        text = "\n".join(body)
        for d in (SCRIPT_DIR, tempfile.gettempdir()):
            try:
                p = os.path.join(d, FAIL_LOG_NAME)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(text)
                print(_fit_glyphs(f"[·] 已写入失败日志：{p}"), flush=True)
                return p
            except OSError:
                continue
    except Exception:
        pass
    return None


def die(msg, code=1, log=True):
    """中止程序。

    log=False 用于"预期内的拒绝"：锁冲突（用户重复双击）、用户主动取消、
    检测到进程让用户手动处理。这些都不是故障，不该在目录里留下"失败记录"，
    否则用户看到 hanhua_error_log.txt 会误以为程序坏了。
    """
    print(_fit_glyphs(f"[✗] {msg}"), flush=True)
    if log:
        _write_fail_log(msg)
    sys.exit(code)


# ----------------------------------------------------------------------------
# 并发防护（重复双击/多窗口同时运行时，只有一个实例能执行，其余安全退出）
# ----------------------------------------------------------------------------
LOCK_TIMEOUT = 600  # 锁最长持有 10 分钟（防僵死锁）

def acquire_lock():
    lock_path = os.path.join(tempfile.gettempdir(), "antigravity_hanhua.lock")
    if os.path.exists(lock_path):
        age = time.time() - os.path.getmtime(lock_path)
        try:
            with open(lock_path, encoding='utf-8') as f:
                old_pid = f.read().strip()
        except Exception:
            old_pid = "?"
        if age > LOCK_TIMEOUT:
            print(f"[!] 发现超过 {LOCK_TIMEOUT//60} 分钟的残留锁（上次进程可能被强杀），已自动清除")
            try:
                os.remove(lock_path)
            except OSError:
                pass
        else:
            die(f"另一个汉化程序正在运行（PID {old_pid}，{int(age)} 秒前启动）。\n"
                "  为避免文件冲突，请等它完成后重试；若确认没有其他窗口在跑，\n"
                f"  可删除锁文件：{lock_path}", log=False)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
        return lock_path
    except FileExistsError:
        die("另一个汉化程序刚刚启动，本次退出（重复双击无副作用，稍后重试即可）。",
            log=False)
    except OSError as e:
        print(f"[!] 无法创建锁文件（{e}），继续执行但不防并发")
        return None


def release_lock(lock_path):
    if lock_path and os.path.exists(lock_path):
        try:
            with open(lock_path, encoding='utf-8') as f:
                owner = f.read().strip()
            # 注意：必须先关闭文件再删除（Windows 不允许删除打开中的文件）
            if owner == str(os.getpid()):
                os.remove(lock_path)
        except OSError:
            pass


# ----------------------------------------------------------------------------
# 环境与路径
# ----------------------------------------------------------------------------
def _resources_from_given(cand):
    """把用户给的任意路径解析成 resources 目录。支持四种输入：
       1. 直接就是 resources 目录（含 app.asar）
       2. .app 包（/Applications/Antigravity.app）
       3. .app 里的 Contents 目录（此时不会再往下拼 resources，否则得到
          Contents/resources 这种不存在的路径）
       4. 安装根目录（其下有 resources/ 或 Contents/Resources/）
    返回 None 表示这条路径不成立。返回值统一做 normpath 归一化（去掉尾斜杠）。"""
    if not cand:
        return None
    cand = os.path.normpath(cand)
    # 用户很可能直接把 Antigravity.exe 或 app.asar 拖进来（Windows 上尤其常见）。
    # 此时取它所在目录再继续判断，比直接报"找不到"友好得多。
    if os.path.isfile(cand):
        cand = os.path.dirname(cand)
    # 先判断是否已经是 resources（含 app.asar），命中直接返回
    if os.path.isfile(os.path.join(cand, "app.asar")):
        return cand
    # 若 basename 是 Contents，说明用户指向了 .app 内部结构，
    # 其 Resources 就是同级目录，绝不能再拼一层
    if os.path.basename(os.path.normpath(cand)) == "Contents":
        cont = os.path.join(cand, "Resources")
        if os.path.isfile(os.path.join(cont, "app.asar")):
            return cont
        return None
    for rel in (("Contents", "Resources"), ("resources",)):
        c = os.path.join(cand, *rel)
        if os.path.isfile(os.path.join(c, "app.asar")):
            return c
    return None


def find_resources(override=None):
    """定位 Antigravity 的 resources 目录"""
    if override:
        r = _resources_from_given(override)
        if r:
            return r
        die(f"指定路径下未找到 app.asar：{override}\n"
            "  可以指向：Antigravity 安装目录、resources 目录，\n"
            "            或 Antigravity.exe / app.asar 文件本身。")
    if IS_WIN:
        cands = [os.path.join(p, "resources") for p in WIN_PATHS if p]
    elif IS_MAC:
        cands = list(MAC_PATHS)
        # 固定位置没有时，展开通配（dmg 挂载点 / Homebrew / 应用名变体）
        import glob as _glob
        for pat in MAC_PATTERNS:
            try:
                cands.extend(sorted(_glob.glob(pat)))
            except Exception:
                pass
    else:
        cands = list(LINUX_PATHS)
        # Linux 额外从 PATH / ~/.local/bin/antigravity 及正在运行的进程反推安装目录
        for bin_cand in (
            shutil.which("antigravity"),
            shutil.which("Antigravity"),
            shutil.which("agy"),
            os.path.expanduser("~/.local/bin/antigravity"),
            "/usr/local/bin/antigravity",
            "/usr/bin/antigravity",
        ):
            if bin_cand and os.path.exists(bin_cand):
                try:
                    real_bin = os.path.realpath(bin_cand)
                    r = _resources_from_given(os.path.dirname(real_bin))
                    if r and r not in cands:
                        cands.insert(0, r)
                except OSError:
                    pass
    for c in cands:
        if os.path.isfile(os.path.join(c, "app.asar")):
            return c
    if IS_LINUX and os.path.isdir("/proc"):
        # 从正在运行的 antigravity 进程 /proc/<pid>/exe 自动定位
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                exe = os.path.realpath(f"/proc/{entry}/exe")
                if os.path.basename(exe).lower() == "antigravity":
                    r = _resources_from_given(os.path.dirname(exe))
                    if r:
                        log(f"已从运行中的进程自动定位到 Antigravity：{r}", ok=True)
                        return r
            except OSError:
                continue
        die("未找到 Antigravity 安装目录。\n"
            "\n"
            "  已自动查找：\n"
            "    - ~/.local/share/antigravity/resources/app.asar\n"
            "    - /opt/Antigravity/resources/app.asar\n"
            "    - /usr/lib/antigravity/resources/app.asar\n"
            "    - PATH 中的 antigravity 可执行文件及运行中进程\n"
            "\n"
            "  请用 --path 参数指定安装路径，例如：\n"
            "    ./一键汉化.sh --path \"/你的安装路径/antigravity\"")
    if IS_MAC:
        # 最后再试一次广搜：常见位置里逐层找 Antigravity.app
        found = _mac_deep_search()
        if found:
            return found
        die("未找到 Antigravity 安装目录。\n"
            "\n"
            "  预期位置：/Applications/Antigravity.app/Contents/Resources/app.asar\n"
            "  已自动查找：应用程序、用户应用程序、下载、桌面、外接磁盘挂载点、\n"
            "            Homebrew 安装目录（均未找到）\n"
            "\n"
            "  可能的原因：\n"
            "    1. Antigravity 还没安装 —— 请先从 antigravity.google 下载安装\n"
            "    2. 装在了特殊位置 —— 请用 --path 指定，例如：\n"
            "         python3 localize.py apply --path \"/路径/Antigravity.app\"\n"
            "    3. 应用名不是 Antigravity —— 请把实际的应用名告诉我")
    die("未找到 Antigravity 安装目录。\n"
        "请用 --path 参数指定安装路径（例如：--path \"%LOCALAPPDATA%\\Programs\\antigravity\"）")


def _mac_deep_search():
    """在常见根目录下逐层查找 Antigravity.app（限深度，避免全盘扫描）。
    返回其 resources 目录，未找到返回 None。"""
    import glob as _glob
    roots = [
        os.path.expanduser("~"),
        "/Applications",
        os.path.expanduser("~/Applications"),
        "/Volumes",
        "/opt/homebrew/Caskroom",
        "/usr/local/Caskroom",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            hits = _glob.glob(os.path.join(root, "*", "Antigravity.app"))
            hits += _glob.glob(os.path.join(root, "Antigravity.app"))
            hits += _glob.glob(os.path.join(root, "*", "*", "Antigravity.app"))
        except Exception:
            continue
        for app in hits:
            r = _resources_from_given(app)
            if r:
                log(f"已自动定位到 Antigravity：{app}", ok=True)
                return r
    return None


def find_app_bundle(resources_dir):
    """macOS：从 resources 目录反推 .app 包路径（代码签名需要）"""
    if not IS_MAC:
        return None
    # resources = /Applications/Antigravity.app/Contents/Resources
    d = os.path.abspath(resources_dir)
    for _ in range(4):
        d = os.path.dirname(d)
        if d.endswith(".app"):
            return d
    return None


# npm 缓存不可写时记录原因（None = 一切正常）。cmd_apply 据此决定是否绕开 npx。
NPM_CACHE_PROBLEM = None
_NPM_CACHE_CHECKED = False       # 菜单里反复选 1 时不必每次都起一个 npm 进程


def probe_npm_cache():
    """探测 npm 缓存目录能不能写。

    为什么必须探测：npx 每次运行都要往 npm 缓存里落临时文件，缓存目录不可写时
    （Node 装在 C 盘根目录但只给了管理员权限、整个目录从别的账户拷过来、
    杀软的勒索防护锁住缓存、缓存被指到只读盘……），npx 会在**真正干活之前**
    就以 `EPERM: operation not permitted` 退出，还给用户甩一屏 npm 内部栈。
    更坑的是：解包这步失败会被内置解包器默默兜住，用户以为一切正常，
    直到最后"重新打包"才炸——那时已经跑了半天。

    提前探测有两个作用：
      1. 把英文栈换成人话 + 可直接复制的修复命令；
      2. 直接绕开 npx 走内置工具链，不让整个流程死在这里。

    返回可写的缓存路径（探测不出结论时返回 None，交给 npx 自己报错）。
    """
    global NPM_CACHE_PROBLEM, _NPM_CACHE_CHECKED
    if _NPM_CACHE_CHECKED:
        return None
    _NPM_CACHE_CHECKED = True
    npm = shutil.which("npm")
    if not npm:
        return None                      # 没 npm 就没得查，不是本函数的职责
    try:
        r = subprocess.run([npm, "config", "get", "cache"], capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=20)
        cache = (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return None                      # npm 本身跑不起来，交给后续步骤报错

    if not cache:
        return None

    # 常见坑：在 PowerShell 里执行 cmd 写法时 %LOCALAPPDATA% 不会展开，
    # npm 会把字面量 "%LOCALAPPDATA%\npm-cache" 当路径存下来 —— 同样是坏的。
    if not os.path.isabs(cache) or "%" in cache:
        NPM_CACHE_PROBLEM = f"{cache}（不是有效绝对路径，可能是变量没展开）"
    else:
        # 探针名带 PID：两个实例同时启动时不会互相删掉对方的文件
        probe = os.path.join(cache, f"._agyzh_write_probe_{os.getpid()}")
        try:
            os.makedirs(cache, exist_ok=True)
            with open(probe, "wb") as f:
                f.write(b"ok")
            os.remove(probe)
            return cache
        except OSError as e:
            NPM_CACHE_PROBLEM = f"{cache}（{e.strerror or e}）"

    log(f"npm 缓存目录不可写：{NPM_CACHE_PROBLEM}", warn=True)
    log("这会让 npx 在下载工具之前就报 EPERM 失败。修复办法（在 cmd 里执行一次，不需要管理员）：", warn=True)
    log('    npm config set cache "%LOCALAPPDATA%\\npm-cache"', warn=True)
    log("本次已自动改用内置解包/打包器，不依赖 npx，可以照常继续。", ok=True)
    return None


NODE_PROBE_TIMEOUT = 15     # 实测 node 能不能跑起来时的超时（秒）


def _probe_node(path):
    """实测这个 node 到底能不能跑起来：能就跑一次 --version 返回版本号，不能返回 None。

    为什么不能只靠 shutil.which()：它只证明"PATH 里有个叫 node 的东西"，
    不证明"这个东西能被启动"。Windows 上有两种典型翻车方式——
      1. which 会按 PATHEXT 匹配到 node.CMD / node.BAT 这类包装脚本，
         而 subprocess 走 CreateProcess 只认 .exe，于是每个文件都抛
         [WinError 2] 系统找不到指定的文件；
      2. 商店版 Node 卸载后残留的 %LOCALAPPDATA%\\Microsoft\\WindowsApps\\node.exe
         执行别名——文件还在，但它指向的目标已经没了，报错同样是 WinError 2。
    2026-11-23 有用户正是撞上第 1 种：工具连报 9 次"语法校验失败"后中止，
    观感像汉化包坏了，实际原 app.asar 一个字节都没改。实测一次即可彻底避免。
    """
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=NODE_PROBE_TIMEOUT,
                           encoding="utf-8", errors="replace")
    except Exception:
        return None
    if r.returncode != 0:
        return None
    # 正常输出只有一行 "v26.13.1"。但有的包装脚本会先打一行警告再输出版本，
    # 所以扫描所有行、取第一个形如 vX.Y 的，比只认第一行宽容；一个都匹配不上
    # 才判为不可用（保守失败，最坏情况只是降级到结构自检，不影响汉化结果）。
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if re.match(r"^v\d+\.\d+", line):
            return line
    return None


def check_env():
    """检查运行环境：node（打包后语法门禁，可选）+ npx（官方 asar 工具，可选）+ macOS 签名工具。

    关于 node：2026-11-19 起不再强制。原先是硬依赖——"改完的 JS 能不能解析"
    只有 node --check 能可靠回答。但这样把绝大多数不装开发工具的普通用户挡在门外，
    而注入内容本身是固定的、发布前已做过完整语法校验。现在改成：
      有可用的 node  → 照旧跑 node --check（最严格）
      没有 / 跑不起来 → 走 structure_check() 结构自检（注入块配对 / 文件完整性），并明确告知

    "能跑起来"是必须实测的：shutil.which() 只能证明 PATH 里有这个文件。
    v1.19.1 起把判定改成"实测 node --version 成功"——否则 PATH 里一个
    失效的 node.cmd 或商店版残留别名，会让语法门禁把每个文件都判成失败。

    npx 只影响"用哪种方式打包"：npm 缓存不可写时会被自动跳过，改走内置打包器。

    macOS 的 codesign 仍是硬依赖：不签名 App 直接打不开，没有替代方案。"""
    global NODE_OK, NODE_BIN, NODE_PROBED, NODE_INFO
    node = shutil.which("node")
    npx = shutil.which("npx")
    NODE_PROBED = True
    # 每次探测都先清空上一次的结果。菜单模式下 [1] 可以被反复点，
    # 不清空的话：第一次成功、第二次失败（用户中途卸载或改了 PATH）时，
    # NODE_OK / NODE_BIN 会留着旧值，语法门禁就会拿一个已失效的路径去跑。
    NODE_OK = False
    NODE_BIN = None
    if node:
        # 关键：which 只说明"PATH 里有个叫 node 的东西"，不等于"它能被启动"。
        # 必须实测一次，否则坏掉的 node 会让后面每个文件都报 WinError 2。
        ver = _probe_node(node)
        if ver:
            NODE_OK = True
            NODE_BIN = node
            NODE_INFO = f"{node} → {ver}（语法级校验已启用）"
        else:
            NODE_INFO = f"{node} → 无法启动，已降级为结构自检"
            log(f"检测到 node（{node}），但它启动不了，本次按「没有 Node.js」处理", warn=True)
            log("  常见原因：PATH 里是失效的 node.cmd / node.bat 包装脚本、商店版 Node", warn=True)
            log("  卸载后残留的执行别名，或版本管理器留下的失效链接。", warn=True)
            log("  这不影响汉化：已改用内置结构自检，翻译内容和结果完全一样。", warn=True)
    else:
        NODE_INFO = "PATH 里没有 node（已降级为结构自检）"
        log("未检测到 Node.js：跳过 node --check，改用内置结构自检", warn=True)
        log("  （汉化本身不需要 Node.js；装上它可获得更严格的语法校验，可选）", warn=True)
    if not npx:
        log("未检测到 npx，本次直接使用内置打包器（不影响汉化结果）", warn=True)
    else:
        probe_npm_cache()
    if IS_MAC:
        # macOS 特有：改包后必须重签名，缺 codesign 会导致 App 打不开
        missing = [t for t in ("codesign", "xattr") if shutil.which(t) is None]
        if missing:
            die("未检测到 macOS 签名工具：%s\n"
                "\n"
                "  这是汉化的**必要条件**：macOS 会对应用包做代码签名，\n"
                "  我们修改了应用包内容后必须重新签名，否则 Antigravity\n"
                "  会提示「应用已损坏」而无法打开。\n"
                "\n"
                "  解决方法（打开「终端」粘贴执行，需联网，约几分钟）：\n"
                "      xcode-select --install\n"
                "\n"
                "  弹出安装界面后点「安装」，等待完成再重新运行本工具。\n"
                "  若已安装却仍提示缺失，请重启终端后重试。"
                % "、".join(missing))
    return npx


class AsarToolError(Exception):
    """asar 工具执行失败。soft 模式下抛出，交给调用方决定是降级还是中止。"""


def asar_run(npx, *args, timeout=300, soft=False):
    """调用官方 @electron/asar 工具（参数列表形式，无 shell 拼接，防注入）

    soft=True：失败抛 AsarToolError 而不是直接退出，供调用方降级。
    """
    if not npx:
        # 正常流程进不来（builtin_required 会先拦截），纯防御：让降级路径拿到
        # 一个明确的异常，而不是 [None, "--yes", ...] 触发的 TypeError。
        if soft:
            raise AsarToolError("没有可用的 npx")
        die("没有可用的 npx，无法调用官方 asar 工具。")
    cmd = [npx, "--yes", "@electron/asar"] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
    except OSError as e:
        # npx 命令"存在"但根本执行不了：npm 目录被删了一半、被杀软拦、
        # 32/64 位错配。不接住会变成 Python traceback，用户看到的是天书。
        if soft:
            raise AsarToolError(f"无法启动 asar 工具：{e}")
        die(f"无法启动 asar 工具（{e}）")
    except subprocess.TimeoutExpired:
        if soft:
            raise AsarToolError("asar 工具执行超时")
        die("asar 工具执行超时（网络下载依赖可能过慢），请重试")
    if r.returncode != 0:
        msg = f"asar 工具执行失败：{' '.join(cmd[:5])}...\n{r.stderr[-800:] or r.stdout[-800:]}"
        if soft:
            raise AsarToolError(msg)
        die(msg)
    return r.stdout


def asar_extract_py(asar_path, out_dir):
    """纯 Python 解包 app.asar（官方工具失败时的回退方案）。

    为什么需要它：@electron/asar 的 extract 是"一个坏条目带走全部"——
    header 里声明为 unpacked、但 app.asar.unpacked 下实际缺少该文件时，
    它会直接抛错退出，前面已解出的内容全部作废。
    Antigravity 2.28.1 就有这种条目（chrome-devtools-mcp 漏了 1 个 .md），
    1085 个条目里坏 1 个，官方包自身的问题，不是汉化造成的。

    这里逐条处理：缺失的外部文件跳过并记录，不阻塞汉化。
    跳过意味着重打包后该条目消失——与官方包"实际读不到"的状态一致，不影响运行。

    返回 (解出文件数, 跳过的相对路径列表, 跳过的符号链接列表)
    """
    import struct as _struct
    f = open(asar_path, 'rb')
    try:
        head = f.read(16)
        if len(head) < 16:
            die("app.asar 头部异常，文件可能已损坏")
        # 与 read_asar_header 保持同一套校验：magic 必须是 4。
        # 少了这道校验，把非 asar 文件喂进来会解析出垃圾目录树然后继续跑，
        # 最后在别处以莫名其妙的方式失败。
        #
        # 四个 UInt32 的含义（别搞混，搞混一次就白解包）：
        #   [0:4]  magic（固定 4）  [4:8]  size  [8:12] header_size  [12:16] header_str_size
        # 数据区起点 base = 12 + header_size（用的是**第 3 个**，不是第 2 个）。
        magic, _size, header_size, header_str_size = _struct.unpack('<4I', head)
        if magic != 4:
            die("app.asar 头部 magic 异常（不是标准 asar 文件），已停止解包")
        f.seek(16)
        header = json.loads(f.read(header_str_size).decode('utf-8'))
        base = 12 + header_size
        unpacked_dir = asar_path + ".unpacked"
        counter = [0]
        skipped = []
        links = []

        def walk(node, prefix):
            for name, entry in node.get('files', {}).items():
                rel = os.path.join(prefix, name) if prefix else name
                if 'files' in entry:
                    os.makedirs(os.path.join(out_dir, rel), exist_ok=True)
                    walk(entry, rel)
                    continue
                if 'link' in entry:
                    # 符号链接：Windows 创建需特权，官方包当前不含此类条目。
                    # 不静默丢弃——记下来由调用方提示，否则重打包后链接会无声消失。
                    links.append(rel.replace(os.sep, '/'))
                    continue
                dest = os.path.join(out_dir, rel)
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if entry.get('unpacked'):
                    # 注意顺序：unpacked 条目在 header 里**没有 offset 字段**
                    # （它们在 asar 里只占一个索引位，真正内容在 .unpacked 目录）。
                    # 所以必须先用 unpacked 分流，再谈 offset，否则会把所有外部文件
                    # 当成"无 offset 异常"全跳掉——这个顺序错过一次，
                    # 表现是只解出 738 个文件、连 package.json 都没有。
                    src = os.path.join(unpacked_dir, rel)
                    if not os.path.isfile(src):
                        skipped.append(rel.replace(os.sep, '/'))
                        continue
                    shutil.copy2(src, dest)
                elif 'offset' in entry:
                    f.seek(base + int(entry['offset']))
                    with open(dest, 'wb') as out:
                        out.write(f.read(entry['size']))
                else:
                    # 既不是目录、链接、unpacked，也没有 offset：未知条目类型。
                    # 记录下来跳过，别让 entry['offset'] 抛 KeyError 把整个流程带崩。
                    skipped.append(rel.replace(os.sep, '/') + '（无 offset 字段）')
                    continue
                counter[0] += 1

        walk(header, '')
        return counter[0], skipped, links
    finally:
        f.close()


# ----------------------------------------------------------------------------
# asar 写入侧：内置打包器 + 自校验（官方 @electron/asar 不可用时的完整回退）
# ----------------------------------------------------------------------------
# 为什么必须自己实现"写"：npx 跑不起来时（npm 缓存 EPERM、公司网络装不上包、
# Node 环境残缺、杀软拦截），官方工具连启动都做不到，而汉化最后一步恰恰是
# "把改好的目录重新打回 app.asar"——只有只读的降级路径等于死路一条。
#
# 二进制格式必须与 @electron/asar 4.x 逐字段对齐，否则 Electron 会按错误的
# 偏移读数据，表现是白屏/启动失败，而且几乎无法反查。实测对照的真实头部：
#   [0:4]   UInt32 固定 4
#   [4:8]   UInt32 sizeBuf 里写的 headerBuf.length = 8 + align4(jsonLen)
#   [8:12]  UInt32 headerPickle 的 payloadSize = 4 + align4(jsonLen)
#   [12:16] UInt32 真实 jsonLen（不带 padding）
#   [16:]   JSON + 补零到 4 字节对齐
#   数据区起点 = 12 + [8:12] = 16 + align4(jsonLen)
ASAR_BLOCK_SIZE = 4 * 1024 * 1024      # 与官方 integrity.js 的 BLOCK_SIZE 一致
ASAR_MAX_FILE = 0xFFFFFFFF             # 官方的 UINT32_MAX 上限（单个文件 4GB）


def _walk_raise(e):
    """os.walk 的错误处理器：默认行为是把权限错误吞掉、静默跳过读不了的目录，
    结果就是「包里悄悄缺文件」和「校验看着一致」同时发生。这里一律抛出。"""
    raise e


def _js_sort_key(s):
    """模拟 JS 默认字符串排序（按 UTF-16 码元逐个比较）。

    Python 的 str 排序按 Unicode 码点比，在增补平面字符（emoji、部分生僻汉字）上
    顺序与 JS 相反：U+1F600 的 UTF-16 首码元是 0xD83D(55357)，小于 U+FF5A(65370)；
    而它的码点 0x1F600(128512) 又大于 0xFF5A。实测两个文件打包后 header 键序颠倒。
    排序只影响 header 键序：功能不受影响，但做不到与官方产物逐字节一致。
    """
    raw = s.encode("utf-16-be", "surrogatepass")
    return tuple(int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2))


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def asar_file_integrity(path):
    """按 @electron/asar 的规则算一个文件的完整性记录。

    结构与官方 integrity.js 的 getFileIntegrity() 完全一致：
        {"algorithm":"SHA256", "hash":<整文件 hex>,
         "blockSize":4194304, "blocks":[<每 4MB 一块的 hex>]}

    三个容易踩的点：
      * hash 是**十六进制**字符串，不是 base64（Electron 只认 hex）；
      * 分块按 4MB 累计切，不能按读取批次切（读取 chunk 可以任意大小）；
      * 空文件也必须有一个 block（空内容的 sha256），blocks 留空会让校验失败。

    返回 (integrity 字典, 文件字节数)
    """
    import hashlib
    whole = hashlib.sha256()
    blk = hashlib.sha256()
    blocks = []
    filled = 0
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            whole.update(chunk)
            pos = 0
            while pos < len(chunk):
                take = min(ASAR_BLOCK_SIZE - filled, len(chunk) - pos)
                blk.update(chunk[pos:pos + take])
                filled += take
                pos += take
                if filled == ASAR_BLOCK_SIZE:
                    blocks.append(blk.hexdigest())
                    blk = hashlib.sha256()
                    filled = 0
    if filled > 0 or not blocks:
        blocks.append(blk.hexdigest())
    return ({"algorithm": "SHA256", "hash": whole.hexdigest(),
             "blockSize": ASAR_BLOCK_SIZE, "blocks": blocks}, size)


def asar_pack_py(src_dir, out_asar, unpack_prefixes=None):
    """纯 Python 打包 asar（官方 @electron/asar 不可用时的回退方案）。

    unpack_prefixes：需要外部化到 `<out>.unpacked/` 的路径前缀（'/' 分隔）。
    语义与官方 `--unpack-dir` 一致：文件相对路径等于前缀、或以 "前缀/" 开头即外部化。

    返回 (包内文件数, 外部化文件数, 产物字节数, 实际外部化的路径集合)
    """
    import struct

    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        raise RuntimeError(f"待打包目录不存在：{src_dir}")

    prefixes = []
    for p in (unpack_prefixes or []):
        p = str(p).strip().strip("/\\").replace("\\", "/")
        if p and p not in prefixes:
            prefixes.append(p)

    def should_unpack(rel_slash):
        for p in prefixes:
            if rel_slash == p or rel_slash.startswith(p + "/"):
                return True
        return False

    # 1) 收集全部路径并做全局字符串排序。
    #    官方 crawlfs 是 crawled.sort()（JS 默认按 UTF-16 码元排序），这里必须用
    #    _js_sort_key 而不是 Python 默认排序，否则增补平面字符会排错位置。
    dirs_rel, files_rel = [], []
    for root, dnames, fnames in os.walk(src_dir, onerror=_walk_raise):
        rel_root = os.path.relpath(root, src_dir)
        rel_root = "" if rel_root == "." else rel_root
        for d in dnames:
            dirs_rel.append(os.path.join(rel_root, d) if rel_root else d)
        for fn in fnames:
            files_rel.append(os.path.join(rel_root, fn) if rel_root else fn)
    all_rel = sorted(set(dirs_rel) | set(files_rel),
                     key=lambda p: _js_sort_key(p.replace(os.sep, "/")))

    header = {"files": {}}

    def node_for(rel):
        """取（必要时创建）目录链节点。与官方 searchNodeFromDirectory 行为一致。"""
        cur = header
        if not rel:
            return cur
        for part in rel.replace(os.sep, "/").split("/"):
            cur = cur.setdefault("files", {}).setdefault(part, {"files": {}})
        return cur

    packed = unpacked = 0
    offset = 0
    seen_hash = {}          # 官方 4.x 会按内容 hash 复用数据段，这里保持一致，避免体积翻倍
    pending = []            # [(源路径, 相对路径, 是否外部化)]
    unpacked_paths = set()

    for rel in all_rel:
        abs_path = os.path.join(src_dir, rel)
        rel_slash = rel.replace(os.sep, "/")
        parent_node = node_for(os.path.dirname(rel))
        name = os.path.basename(rel)

        if os.path.islink(abs_path):
            # 符号链接在 asar 里是 {"link": 目标} 条目，不是文件内容。
            # 内置打包器不重建它：os.walk 不跟随链接目录，会把链接当成空目录写进
            # header —— 内容凭空消失且校验也查不出来（那种包能启动但功能缺失）。
            # 宁可明确失败，也不能产出这种包。
            raise RuntimeError(
                f"待打包目录里含符号链接，内置打包器不支持：{rel_slash}\n"
                "  请先修复 npm 环境（让 npx 可用）后重试。")

        if os.path.isdir(abs_path):
            node = parent_node["files"].setdefault(name, {})
            # 键序对齐官方 insertDirectory：先 unpacked 再 files。
            # 顺序反了功能一样（Electron 按 key 查），但整包就做不到与官方
            # 产物逐字节一致，日后比对差异会被这个假信号带偏。
            if should_unpack(rel_slash):
                node["unpacked"] = True
            node.setdefault("files", {})
            continue

        if not os.path.isfile(abs_path):
            raise RuntimeError(f"不是普通文件，无法打包：{rel}")

        node = parent_node["files"].setdefault(name, {})
        integrity, size = asar_file_integrity(abs_path)
        if size > ASAR_MAX_FILE:
            raise RuntimeError(f"单个文件超过 4.2GB，asar 格式不支持：{rel}")
        node["size"] = size
        if should_unpack(rel_slash):
            # 外部文件在 asar 里只留索引，没有 offset（内容在 .unpacked 目录）
            node["unpacked"] = True
            node["integrity"] = integrity
            unpacked += 1
            unpacked_paths.add(rel_slash)
            pending.append((abs_path, rel, True))
        else:
            h = integrity["hash"]
            # 键序对齐官方 storeFileEntry：size → offset → [executable] → integrity
            if h in seen_hash:
                node["offset"] = seen_hash[h]
            else:
                node["offset"] = str(offset)
                seen_hash[h] = str(offset)
                offset += size
                pending.append((abs_path, rel, False))
            if not IS_WIN and (os.stat(abs_path).st_mode & 0o100):
                node["executable"] = True
            node["integrity"] = integrity
            packed += 1

    # 2) 序列化 header：与 JSON.stringify 对齐（无空格、不转义非 ASCII）
    #    ensure_ascii=False 不能省：官方 JSON.stringify 直接输出原始 UTF-8 字符，
    #    默认的 True 会把中文/日文文件名转成 \uXXXX，header 变长且与官方产物对不上。
    json_bytes = json.dumps(header, separators=(",", ":"),
                            ensure_ascii=False).encode("utf-8")
    json_len = len(json_bytes)
    padded = (json_len + 3) & ~3
    header_payload = 4 + padded           # Pickle payloadSize = 长度前缀 + 对齐后的 JSON
    head = struct.pack("<4I",
                       4,
                       8 + padded,        # sizeBuf 里写的 headerBuf.length
                       header_payload,    # headerPickle 的 payloadSize
                       json_len)          # 真实 JSON 长度（不带 padding）

    # 3) 落盘：先写 .part 再改名，避免中途失败留下半个"看起来存在"的 asar
    out_unpacked = out_asar + ".unpacked"
    if os.path.isdir(out_unpacked):
        shutil.rmtree(out_unpacked, ignore_errors=True)
    if unpacked:
        os.makedirs(out_unpacked, exist_ok=True)
    part = out_asar + ".part"
    try:
        with open(part, "wb") as f:
            f.write(head)
            f.write(json_bytes)
            f.write(b"\x00" * (padded - json_len))
            for abs_path, rel, is_unpack in pending:
                if is_unpack:
                    dest = os.path.join(out_unpacked, rel)
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    shutil.copyfile(abs_path, dest)
                else:
                    with open(abs_path, "rb") as srcf:
                        shutil.copyfileobj(srcf, f, 1024 * 1024)
        os.replace(part, out_asar)
    except Exception:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return packed, unpacked, os.path.getsize(out_asar), unpacked_paths


def asar_verify_archive(asar_path, src_dir, expect_unpacked=None):
    """把刚打好的包原样读回来，逐文件与源目录核对（内置打包器的自证手段）。

    "写进去了"不等于"能读对"：Electron 完全依赖 header 里的 offset/size 定位
    内容，少算 1 个字节就会读出错位的垃圾。所以必须闭环验证——
        源文件 sha256 == header 记录的 hash == 从包内按 offset/size 读回的 sha256
    再核对文件集合与外部化清单。任何一项不符都不允许部署。

    返回问题描述列表（空列表 = 全部通过）。
    """
    import struct
    import hashlib

    problems = []
    src_dir = os.path.abspath(src_dir)
    archive_size = os.path.getsize(asar_path)

    with open(asar_path, "rb") as f:
        head = f.read(16)
        if len(head) < 16:
            return ["产物头部不足 16 字节"]
        magic, _size, header_size, header_str_size = struct.unpack("<4I", head)
        if magic != 4:
            return [f"产物 magic 异常（{magic}）"]
        f.seek(16)
        try:
            header = json.loads(f.read(header_str_size).decode("utf-8"))
        except Exception as e:
            return [f"产物 header 解析失败（{e}）"]
        base = 12 + header_size
        unpacked_dir = asar_path + ".unpacked"
        found = set()

        def walk(node, prefix):
            for name, entry in node.get("files", {}).items():
                rel = f"{prefix}/{name}" if prefix else name
                if "files" in entry:
                    walk(entry, rel)
                    continue
                if "link" in entry:
                    # 链接条目也要计入 found：源目录里它是符号链接（os.walk 会列出），
                    # 不算进来会被反向核对误报成"漏打包"。
                    found.add(rel)
                    continue
                found.add(rel)
                integ = entry.get("integrity") or {}
                want = integ.get("hash")
                src_path = os.path.join(src_dir, rel.replace("/", os.sep))
                if not os.path.isfile(src_path):
                    problems.append(f"{rel}：源目录里没有这个文件")
                    continue
                try:
                    got = _sha256_file(src_path)
                except OSError as e:
                    problems.append(f"{rel}：读源文件失败（{e}）")
                    continue
                if not want:
                    problems.append(f"{rel}：header 缺少 integrity")
                elif got != want:
                    problems.append(f"{rel}：header 记录的 hash 与源文件不符")
                if entry.get("unpacked"):
                    ext = os.path.join(unpacked_dir, rel.replace("/", os.sep))
                    if not os.path.isfile(ext):
                        problems.append(f"{rel}：声明外部化但 .unpacked 下缺文件")
                    elif _sha256_file(ext) != got:
                        problems.append(f"{rel}：.unpacked 里的内容与源文件不符")
                    continue
                off = entry.get("offset")
                if off is None:
                    problems.append(f"{rel}：既没有 offset 也没声明 unpacked")
                    continue
                try:
                    off = int(off)
                    size = int(entry.get("size", -1))
                except (TypeError, ValueError):
                    problems.append(f"{rel}：offset/size 不是整数")
                    continue
                if size != os.path.getsize(src_path):
                    problems.append(f"{rel}：size 与源文件大小不符")
                if off < 0 or base + off + size > archive_size:
                    problems.append(f"{rel}：数据区间越界")
                    continue
                f.seek(base + off)
                data = f.read(size)
                if len(data) != size:
                    problems.append(f"{rel}：按 offset/size 只读到 {len(data)} 字节")
                elif hashlib.sha256(data).hexdigest() != got:
                    problems.append(f"{rel}：包内实际字节与源文件不一致")

        walk(header, "")

        # 反向核对：源目录里每个文件都必须出现在包中（漏打包会静默丢功能）
        for root, _dirs, fnames in os.walk(src_dir, onerror=_walk_raise):
            rel_root = os.path.relpath(root, src_dir)
            rel_root = "" if rel_root == "." else rel_root
            for fn in fnames:
                p = (rel_root + os.sep + fn) if rel_root else fn
                ps = p.replace(os.sep, "/")
                if ps not in found:
                    problems.append(f"{ps}：源目录有、产物里没有（漏打包）")

        if expect_unpacked is not None:
            got_up = get_unpacked_set(asar_path)
            for miss in sorted(set(expect_unpacked) - got_up)[:5]:
                problems.append(f"{miss}：应外部化却没标记 unpacked")
    return problems


def builtin_required(npx):
    """是否必须完全绕开 npx（没有 npx，或 npm 缓存不可写）"""
    return (not npx) or (NPM_CACHE_PROBLEM is not None)


def _report_extract_skips(skipped_unpacked, skipped_links):
    """内置解包器跳过项的统一提示（两类跳过原因完全不同，别合并成一条）"""
    if skipped_unpacked:
        log(f"跳过 {len(skipped_unpacked)} 个官方包自身缺失/异常的条目（与汉化无关，不影响使用）：", warn=True)
        for _s in skipped_unpacked[:5]:
            log(f"    {_s}", warn=True)
    if skipped_links:
        log(f"跳过 {len(skipped_links)} 个符号链接（Windows 无法重建，重打包后会消失）：", warn=True)
        for _s in skipped_links[:5]:
            log(f"    {_s}", warn=True)
        log("若汉化后出现异常，请把这段信息反馈给维护者", warn=True)


def extract_any(npx, asar_path, out_dir, quiet=False):
    """解包：能走官方工具就走，走不通自动降级内置解包器。

    返回 (实际使用的工具, 跳过的外部条目, 跳过的符号链接)，工具为 "npx"/"builtin"。
    """
    if builtin_required(npx):
        n_files, skipped, links = asar_extract_py(asar_path, out_dir)
        if not quiet:
            log(f"内置解包器完成：{n_files} 个文件", ok=True)
            _report_extract_skips(skipped, links)
        return "builtin", skipped, links
    try:
        asar_run(npx, "extract", asar_path, out_dir, soft=True)
        return "npx", [], []
    except AsarToolError:
        # 两个来源会导致这里失败：npm 环境坏（缓存不可写等）或官方包自身有坏条目。
        # 两种情况内置解包器都能扛，所以统一降级，不再区分。
        log("官方 asar 工具解包失败，改用内置解包器处理", warn=True)
        n_files, skipped, links = asar_extract_py(asar_path, out_dir)
        if not quiet:
            log(f"内置解包器完成：{n_files} 个文件", ok=True)
            _report_extract_skips(skipped, links)
        return "builtin", skipped, links


def _discard_partial(out_asar):
    """清掉内置打包器的半成品，绝不留下"看起来存在"的残包"""
    for p in (out_asar + ".part", out_asar, out_asar + ".unpacked"):
        try:
            if os.path.isfile(p):
                os.remove(p)
            elif os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def pack_any(npx, src_dir, out_asar, unpack_pattern, unpack_prefixes,
             force_builtin=False):
    """打包：优先官方工具，失败或环境不可用时用内置打包器（带闭环自校验）。

    force_builtin：外部化目录有多处时置 True。官方 `--unpack-dir` 只接受单条
    前缀/模式，多目录只能靠花括号拼 glob，而它对 isUnpackedDir 的 startsWith
    判定无效——结果是外部文件全被塞回包内、结构守卫必然报缺失。这种情况直接
    用支持多前缀的内置打包器，别让它撞上一条注定失败的路径。

    返回 "npx" / "builtin"。内置路径任何失败都转成 AsarToolError 抛出，
    由调用方中止——绝不允许把未经核对的新包替换上去。
    """
    if not (force_builtin or builtin_required(npx)):
        try:
            # soft=True 是降级的前提：不带它会直接 die，下面的 except 永远等不到异常。
            if unpack_pattern:
                asar_run(npx, "pack", src_dir, out_asar,
                         "--unpack-dir", unpack_pattern, soft=True)
            else:
                asar_run(npx, "pack", src_dir, out_asar, soft=True)
            return "npx"
        except AsarToolError:
            log("官方 asar 工具打包失败，改用内置打包器", warn=True)
    elif force_builtin and not builtin_required(npx):
        log(f"外部目录有 {len(unpack_prefixes)} 处，官方 --unpack-dir 只能表达单条前缀，"
            "改用内置打包器", warn=True)

    try:
        packed, unpacked, nbytes, unpacked_paths = asar_pack_py(
            src_dir, out_asar, unpack_prefixes)
        log(f"内置打包器完成：{packed} 个文件写入包内、{unpacked} 个外部化，"
            f"产物 {nbytes // 1024} KB", ok=True)
        problems = asar_verify_archive(out_asar, src_dir,
                                       expect_unpacked=unpacked_paths)
    except AsarToolError:
        raise
    except Exception as e:
        # 内置路径的失败五花八门（磁盘满、文件被占用、权限不足、含符号链接、
        # 目录读不了……），调用方只认 AsarToolError。不转就会直接喷 traceback，
        # 用户看到的是 Python 栈而不是"打包失败，原包未改动"。
        _discard_partial(out_asar)
        raise AsarToolError(f"内置打包失败：{e}")
    if problems:
        _discard_partial(out_asar)
        raise AsarToolError("内置打包自校验未通过：" + "；".join(problems[:3]))
    log("内置打包自校验通过：所有文件按 offset 读回后与源文件逐字节一致", ok=True)
    return "builtin"


def node_check(files):
    """语法门禁：对所有被修改的 JS 文件做 node --check 静态校验。
    任何异常都视为"校验不通过"——宁可中止，也不能让未校验的产物部署。
    没有 node 时返回空列表，由 structure_check() 接管（调用方负责提示差异）。"""
    if not NODE_OK:
        # 安全阀：降级只允许发生在 check_env() 已经探测过、并且确认 node 不可用之后。
        # 没探测过就走到这里，说明调用方漏掉了 check_env()——那种情况必须中止，
        # 否则会把最严格的语法校验悄悄关掉。
        if not NODE_PROBED:
            die("内部错误：node 探测未执行（漏调 check_env）。\n"
                "  为避免在未校验的情况下改动程序，已中止，原文件未被修改。\n"
                "  请把这句话反馈给维护者。")
        return []
    bad = []
    for f in files:
        try:
            # 用绝对路径而不是裸 "node"：PATH 里可能有同名干扰项，
            # 而且 CheckEnv 已经验证过这个路径能启动，没必要再赌一次 PATH 解析。
            r = subprocess.run([NODE_BIN, "--check", f], capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
        except Exception as e:
            bad.append((f, f"无法执行 node --check（{NODE_BIN}）：{e}"))
            continue
        if r.returncode != 0:
            bad.append((f, (r.stderr or r.stdout)[-500:]))
    return bad


def _bracket_balance(src):
    """粗略统计未闭合的括号数量（跳过字符串与注释）。

    只作参考：JS 的正则字面量（如 /[{}]/）会被误当成括号，
    因此这个结果**不能**用来阻断流程，只在大幅偏离时提示用户。
    返回未闭合左括号的个数；出现"多余右括号"时返回 -1。
    """
    pairs = {'(': ')', '[': ']', '{': '}'}
    closers = {')': '(', ']': '[', '}': '{'}
    stack = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            if j < 0:
                break
            i = j
            continue
        if ch == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in ('"', "'", '`'):
            quote = ch
            i += 1
            while i < n:
                if src[i] == '\\':
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in closers:
            if stack and stack[-1] == closers[ch]:
                stack.pop()
            else:
                return -1
        i += 1
    return len(stack)


def structure_check(files):
    """没有 Node.js 时的替代校验：结构级，不是语法级。

    只覆盖"注入把文件弄坏"的几种典型症状，不能替代 node --check：
      1. 文件能被 UTF-8 解码、非空
      2. 注入块标记成对、顺序正确、不重复
      3. 括号计数没有明显异常（参考项）
    返回 (problems, warnings)；problems 非空即中止。
    """
    problems, warnings = [], []
    # 第三个数字是块内容的最小长度（实测：engine 1,386,048 / menu 2,268 / tray 370 / ctx 1,819）。
    # 取正常值的约 1/4 作下限：既能抓"块被清空/截断到一小截"，又不会误伤正常文件。
    marker_pairs = ((ENGINE_START, ENGINE_END, 300000),
                    (MENU_START, MENU_END, 500),
                    (TRAY_START, TRAY_END, 150),
                    (CTX_START, CTX_END, 400))
    for f in files:
        name = os.path.basename(f)
        try:
            c = open(f, encoding='utf-8').read()
        except UnicodeDecodeError as e:
            problems.append(f"{name}: UTF-8 解码失败（{e}）")
            continue
        except OSError as e:
            problems.append(f"{name}: 读取失败（{e}）")
            continue
        if not c.strip():
            problems.append(f"{name}: 文件为空")
            continue
        if "\x00" in c:
            problems.append(f"{name}: 含 NUL 字节（写入异常）")
            continue
        for s, e, min_len in marker_pairs:
            ns, ne = c.count(s), c.count(e)
            if ns != ne:
                problems.append(f"{name}: 注入标记不成对（{ns} 个 START / {ne} 个 END）")
            elif ns > 1:
                problems.append(f"{name}: 注入标记重复（{ns} 次）")
            elif ns == 1:
                si, ei = c.index(s), c.index(e)
                if si > ei:
                    problems.append(f"{name}: 注入标记顺序颠倒")
                elif ei - si < min_len:
                    problems.append(f"{name}: 注入块内容异常短（{ei - si} 字节，正常应 ≥ {min_len}）")
        bal = _bracket_balance(c)
        # 括号统计仅作兜底提示，阈值放到 200：实测正常文件因正则字面量会有 1~5 的偏移
        # （preload -1、sshArgs 5），逐个报出来只会吓人；真正被整段截断的文件偏移是几百。
        if abs(bal) > 200:
            warnings.append(f"{name}: 括号计数严重异常（{bal}），若界面异常请用「恢复官方原版」")
    return problems, warnings


# ----------------------------------------------------------------------------
# 进程管理
# ----------------------------------------------------------------------------
def _safe_run(cmd, timeout=30):
    """执行命令并忽略一切失败。用于杀进程、启动应用这类
    "失败也不影响主流程"的场景，避免因系统异常中断整个汉化。"""
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return None


def _mac_running_pids(app_bundle=None):
    """macOS：列出 Antigravity 相关进程 (pid, 可执行路径)。
    用 ps -axo pid,comm 拿 PID 与可执行文件全路径，再按路径精确过滤——
    绝不使用 `pkill -f Antigravity` 这种模糊匹配：它会误杀其他目录下的同名程序，
    甚至可能杀掉本脚本自身（脚本路径里就含 Antigravity 字样）。"""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,comm="],
                             capture_output=True, text=True,
                             encoding='utf-8', errors='replace').stdout
    except Exception:
        return []
    marker = os.path.abspath(app_bundle).lower() if app_bundle else ""
    hits = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_s, path = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        low = path.lower()
        base = os.path.basename(low)
        if marker and low.startswith(marker + os.sep):
            hits.append((pid, path))
        elif base in ("antigravity", "language_server", "antigravity helper"):
            # 路径可读但不在预期包内（用户装在非常规位置）——文件名精确匹配才认
            hits.append((pid, path))
    me = os.getpid()
    return [(p, path) for p, path in hits if p != me]


def _same_install_exe(exe_path, resources_dir):
    """进程的可执行文件是否就是本次要改的那份安装里的 Antigravity.exe。

    为什么不能只按文件名认（v1.18 审计发现）：`--path` 指向另一份拷贝时，
    旧逻辑会把**正在运行的那份安装**也一起 taskkill 掉。用户感觉像"工具把我的
    应用关了"，其实只是在改另一份目录。
    用 samefile 而不是字符串比较：Windows 的 8.3 短路径、盘符大小写、符号链接
    都能正确识别成同一个文件；目标文件不存在时返回 False（那份安装本来也没在跑）。
    """
    if not exe_path or not resources_dir:
        return False
    try:
        target = os.path.join(os.path.dirname(os.path.abspath(resources_dir)), "Antigravity.exe")
        return os.path.samefile(exe_path, target)
    except OSError:
        return False


def _linux_ancestor_pids():
    """Linux：获取当前进程的所有祖先 PID 集合（通过 /proc/<pid>/status 的 PPid 链回溯）。
    用于判断汉化工具是否正运行在 Antigravity 自身的内置终端或智能体会话中——
    若是，决不能杀死祖先进程（否则会把汉化脚本自己一起杀掉），而应自动走 POSIX 原子热替换。"""
    ancestors = set()
    cur = os.getpid()
    for _ in range(64):
        if cur <= 1 or cur in ancestors:
            break
        ancestors.add(cur)
        ppid = 0
        try:
            with open(f"/proc/{cur}/status", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("PPid:"):
                        ppid = int(line.split(":", 1)[1].strip())
                        break
        except Exception:
            break
        cur = ppid
    return ancestors


def _linux_running_pids(resources_dir=None):
    """Linux：通过 /proc/<pid>/exe 内核符号链接精确匹配目标安装目录下的进程。
    只匹配可执行文件真实路径位于本次 Antigravity 安装目录（resources 的上级目录）内的进程，
    绝不依赖模糊的进程名子串匹配，彻底杜绝误杀其他编辑器（如 VS Code / Neovim）的 language_server。"""
    if not os.path.isdir("/proc"):
        return []
    install_root = ""
    if resources_dir:
        try:
            install_root = os.path.realpath(os.path.dirname(os.path.abspath(resources_dir)))
        except OSError:
            install_root = os.path.dirname(os.path.abspath(resources_dir))
    me = os.getpid()
    hits = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == me:
            continue
        try:
            exe = os.path.realpath(f"/proc/{pid}/exe")
        except OSError:
            continue
        if not exe:
            continue
        # 剥离内核在二进制被更新后追加的 " (deleted)" 后缀
        if exe.endswith(" (deleted)"):
            exe = exe[:-10]
        if install_root and (exe == install_root or exe.startswith(install_root + os.sep)):
            hits.append((pid, exe))
    return hits


def kill_processes(auto=False, resources_dir=None, no_kill=False):
    """关闭 Antigravity 相关进程。按可执行文件路径过滤，只杀安装目录下的进程——
    其他软件恰好同名（如 language_server）的进程绝不误杀。"""
    if no_kill:
        log("已启用免关进程模式（--no-kill）：直接进行原子热替换，完成后重启 Antigravity 即可生效", ok=True)
        return
    if IS_MAC:
        app_bundle = find_app_bundle(resources_dir)
        procs = _mac_running_pids(app_bundle)
        if not procs:
            return
        desc = ", ".join(f"{os.path.basename(p)}({pid})" for pid, p in procs[:6])
        if len(procs) > 6:
            desc += f" 等 {len(procs)} 个进程"
        if not auto:
            print(f"[!] 检测到正在运行：{desc}")
            ans = input("    需要关闭它们才能继续，是否关闭？(Y/n)：").strip().lower()
            if ans not in ("", "y", "yes"):
                die("已取消。请手动退出 Antigravity 后重试（菜单栏图标 → 退出）。",
                    log=False)
        log(f"正在关闭进程：{desc}")
        for pid, _ in procs:
            _safe_run(["kill", "-TERM", str(pid)])
        for _ in range(15):
            time.sleep(0.2)
            if not _mac_running_pids(app_bundle):
                log("进程已全部退出", ok=True)
                return
        for pid, _ in _mac_running_pids(app_bundle):
            _safe_run(["kill", "-KILL", str(pid)])
        time.sleep(0.5)
        if _mac_running_pids(app_bundle):
            log("部分进程未退出（若有 Helper 残留，建议重启后再试）", warn=True)
        else:
            log("进程已全部退出", ok=True)
        return

    if not IS_WIN:
        procs = _linux_running_pids(resources_dir)
        if not procs:
            return
        ancestors = _linux_ancestor_pids()
        if any(pid in ancestors for pid, _ in procs):
            log("检测到当前正在 Antigravity 内部终端/会话中运行，自动启用 POSIX 原子热替换（不强杀当前会话）", ok=True)
            log("汉化部署完成后，手动重启一次 Antigravity 即可看到中文界面。", ok=True)
            return
        desc = ", ".join(f"{os.path.basename(p)}({pid})" for pid, p in procs[:6])
        if len(procs) > 6:
            desc += f" 等 {len(procs)} 个进程"
        if not auto:
            print(f"[!] 检测到 Antigravity 正在运行（已按安装路径精确匹配）：{desc}")
            ans = input("    是否关闭它们后继续？(Y=关闭 / h=不关闭直接热替换 / n=取消，默认 Y)：").strip().lower()
            if ans in ("h", "hot"):
                log("已选择热替换模式：保持当前进程运行，汉化完成后重启 Antigravity 即可生效", ok=True)
                return
            if ans not in ("", "y", "yes"):
                die("已取消。请手动退出 Antigravity 后重试。", log=False)
        log(f"正在关闭进程：{desc}")
        for pid, _ in procs:
            _safe_run(["kill", "-TERM", str(pid)])
        for _ in range(15):
            time.sleep(0.2)
            if not _linux_running_pids(resources_dir):
                log("进程已全部退出", ok=True)
                return
        for pid, _ in _linux_running_pids(resources_dir):
            _safe_run(["kill", "-KILL", str(pid)])
        time.sleep(0.5)
        if _linux_running_pids(resources_dir):
            log("部分进程未完全退出，将继续使用 POSIX 原子替换部署（完成后重启即可）", warn=True)
        else:
            log("进程已全部退出", ok=True)
        return

    # Windows：枚举进程的可执行路径，只处理 Antigravity 目录下的
    marker = (resources_dir or "").replace("/", "\\").lower()
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'Antigravity.exe' "
             "-or $_.Name -eq 'language_server.exe' } | "
             "Select-Object Name,ProcessId,ExecutablePath | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30, encoding='utf-8', errors='replace').stdout
        procs = json.loads(out) if out.strip() else []
        if isinstance(procs, dict):
            procs = [procs]
    except Exception:
        procs = []
    targets, unknown_generic = [], []
    for p in procs:
        path = (p.get("ExecutablePath") or "").replace("/", "\\").lower()
        name = p.get("Name", "")
        if marker and path and marker in path:
            targets.append(p)
        elif name == "Antigravity.exe" and (not path or _same_install_exe(path, resources_dir)):
            # 路径不可读（权限/占用）时才退回按名字认；路径可读时必须确认是同一份安装，
            # 否则 `--path` 改拷贝目录会把用户正在用的那份杀掉（v1.18 修）
            targets.append(p)
        elif name == "language_server.exe" and not path:
            # 路径读不到的同名进程：仅在确认本机 Antigravity 在跑时才视为疑似目标
            unknown_generic.append(p)
    if unknown_generic and (targets or any(p.get("Name") == "Antigravity.exe" for p in procs)):
        targets.extend(unknown_generic)

    if not targets:
        return
    desc = ", ".join(f"{p['Name']}({p['ProcessId']})" for p in targets)
    if not auto:
        print(f"[!] 检测到正在运行：{desc}")
        ans = input("    需要关闭它们才能继续，是否关闭？(Y/n)：").strip().lower()
        if ans not in ("", "y", "yes"):
            die("已取消。请手动退出 Antigravity 后重试。", log=False)
    log(f"正在关闭进程：{desc}")
    for p in targets:
        _safe_run(["taskkill", "/F", "/PID", str(p["ProcessId"])])
    for _ in range(20):
        time.sleep(0.3)
        _t = _safe_run(["tasklist"])
        task = (_t.stdout.decode("utf-8", "replace").lower()
                if _t is not None and _t.stdout else "")
        if not any(f"{p['Name'].lower()} " in task or task.endswith(p["Name"].lower()) for p in targets):
            log("进程已全部退出", ok=True)
            return
    log("部分进程未退出，继续尝试（若部署失败请重启电脑后再试）", warn=True)


def launch_app(resources_dir):
    if IS_WIN:
        exe = os.path.join(os.path.dirname(resources_dir), "Antigravity.exe")
        if not os.path.isfile(exe):
            return
        # Windows 启动必须"断干净"，否则会踩三个坑（用户实测反馈）：
        #   1. Antigravity 的日志（DevTools 端口、wsl.exe 报错、自动更新）直接刷在本窗口上，
        #      满屏英文乱码，把菜单提示顶没了；
        #   2. 本窗口和它共享同一个控制台，用户点右上角 X 关窗口时，系统会发 CTRL_CLOSE
        #      给控制台里的所有进程，Antigravity 跟着一起退出；
        #   3. 反过来，程序执行完退出后窗口也不关（控制台还被 Antigravity 占着），
        #      看起来就像"输 0 没反应、关不掉"。
        # 解法：DETACHED_PROCESS 让它彻底脱离当前控制台，三路标准流全部指向 NUL，
        #      再配 CREATE_NEW_PROCESS_GROUP 免得 Ctrl+C 波及到它。
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        # 关键：从 Electron 系终端（VS Code / WorkBuddy / Cursor 等）里运行时，
        # 环境里会带着 ELECTRON_RUN_AS_NODE=1。Antigravity 本身也是 Electron 应用，
        # 继承到这个变量后会被当成 Node 解释器启动：不弹窗、立刻以 0 退出，
        # 看起来就像"汉化把软件搞坏了"。这里必须显式剔除。
        child_env = {k: v for k, v in os.environ.items() if k.upper() != "ELECTRON_RUN_AS_NODE"}
        try:
            p = subprocess.Popen(
                [exe], cwd=os.path.dirname(exe),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=flags, env=child_env)
        except Exception:
            # 极端情况（沙箱/权限拦截）退回老办法：能启动就行，代价是日志会打到本窗口
            try:
                os.startfile(exe)  # noqa
                log("已启动 Antigravity（兼容模式：它的日志可能会刷在本窗口）", ok=True)
            except Exception as e2:
                log(f"启动失败：{e2}（可手动打开）", warn=True)
            return
        time.sleep(0.8)
        if p.poll() is not None:
            log("启动失败：进程立刻退出了（可手动双击 Antigravity 试一次）", warn=True)
            return
        log("已启动 Antigravity（独立进程，关掉本窗口不会影响它）", ok=True)
        return
    if IS_MAC:
        bundle = find_app_bundle(resources_dir) or "/Applications/Antigravity.app"
        if not os.path.isdir(bundle):
            log(f"未找到应用包：{bundle}（可手动打开）", warn=True)
            return
        try:
            r = subprocess.run(["open", bundle], capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
        except Exception as e:
            log(f"启动失败：{e}（可手动从「应用程序」打开）", warn=True)
            return
        if r.returncode == 0:
            log("已启动 Antigravity", ok=True)
        else:
            log(f"启动失败：{(r.stderr or '').strip()[:200]}\n"
                "  若提示「应用已损坏」，请看 README 的「打不开怎么办」一节。", warn=True)
        return
    # Linux：定位可执行文件并以独立会话后台启动（剥离 ELECTRON_RUN_AS_NODE）
    install_root = os.path.dirname(os.path.abspath(resources_dir))
    exe = None
    for cand in (
        os.path.join(install_root, "antigravity"),
        os.path.join(install_root, "Antigravity"),
        os.path.expanduser("~/.local/bin/antigravity"),
        shutil.which("antigravity"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            exe = cand
            break
    if not exe:
        log("未找到 Antigravity 启动程序（可从应用菜单或终端手动打开）", warn=True)
        return
    child_env = {k: v for k, v in os.environ.items() if k.upper() != "ELECTRON_RUN_AS_NODE"}
    try:
        p = subprocess.Popen(
            [exe], cwd=os.path.dirname(exe),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=True, env=child_env)
        time.sleep(0.8)
        if p.poll() is not None:
            log("启动失败：进程立刻退出了（可手动运行 antigravity 打开）", warn=True)
            return
        log(f"已启动 Antigravity（{exe}）", ok=True)
    except Exception as e:
        log(f"启动失败：{e}（可手动运行 antigravity 打开）", warn=True)


# ----------------------------------------------------------------------------
# macOS 代码签名（Mac 版的关键差异，Windows 无此环节）
# ----------------------------------------------------------------------------
# 为什么必须做：
#   macOS 对 .app 包做整体代码签名。我们替换了 app.asar，就破坏了原有签名，
#   系统会拒绝启动：提示"应用已损坏，无法打开"（Apple Silicon 上更可能直接启动失败）。
#   解法是用 ad-hoc 临时签名（codesign --sign -）重签一遍，无需苹果开发者证书。
#
# 为什么必须保留 entitlements：
#   Electron 运行时需要 JIT 等特殊权限，这些权限记录在 entitlements.plist 里。
#   如果直接裸签（不带 --entitlements），App 签名是有效的，但启动时会因缺少
#   JIT 权限而崩溃。所以流程是：先把原包的 entitlements 导出，再用它重签。

def _codesign_available():
    return shutil.which("codesign") is not None


def _mac_export_entitlements(app_bundle):
    """导出原包的 entitlements 到临时 plist。失败返回 None（后续用通用兜底）。

    注意两个真实存在的坑（都会导致"明明有权限却被丢弃"）：
      1. macOS 12+ 必须加 --xml，否则输出的是人类可读摘要而非 plist；
      2. Xcode 13.3+ 的 --xml 输出末尾会多一个 NUL(\\x00) 字节，
         它会让 plutil -lint 判定失败，必须先剥掉。
    """
    tmp_plist = os.path.join(tempfile.gettempdir(),
                             f"agy_zh_entitlements_{os.getpid()}.plist")
    try:
        r = subprocess.run(["codesign", "-d", "--entitlements", "-", "--xml", app_bundle],
                           capture_output=True, timeout=60)
        raw = r.stderr or b""
        if isinstance(raw, str):
            raw = raw.encode("utf-8", "replace")
        # 坑2：剥掉尾部 NUL 与空白
        raw = raw.rstrip(b"\x00").rstrip()
        text = raw.decode("utf-8", "replace")
        if "<plist" not in text:
            return None
        # codesign 可能在 plist 之前打印 "Executable=..." 等信息，从 <?xml 或 <plist 起截取
        if "<?xml" in text:
            start = text.index("<?xml")
        else:
            start = text.index("<plist")
        body = text[start:]

        with open(tmp_plist, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        # 用 plutil 校验合法性（陷阱：--lint 成功才算过）
        v = subprocess.run(["plutil", "-lint", tmp_plist], capture_output=True, text=True)
        if v.returncode != 0:
            # 兜底再试一次：让 plutil 直接把 stdin 转成标准 plist 后落盘
            try:
                p2 = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", "-"],
                                    input=body.encode("utf-8"),
                                    capture_output=True, timeout=30)
                if p2.returncode == 0 and b"<plist" in (p2.stdout or b""):
                    with open(tmp_plist, "wb") as f:
                        f.write(p2.stdout.rstrip(b"\x00").rstrip())
                    return tmp_plist
            except Exception:
                pass
            os.remove(tmp_plist)
            return None
        return tmp_plist
    except Exception:
        if os.path.exists(tmp_plist):
            try:
                os.remove(tmp_plist)
            except OSError:
                pass
        return None


def _mac_verify_signature(app_bundle):
    """校验当前签名是否有效。返回 (是否有效, 说明)"""
    try:
        r = subprocess.run(["codesign", "--verify", "--deep", "--strict", app_bundle],
                           capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=120)
        if r.returncode == 0:
            return True, "签名有效"
        return False, _zh_error(r.stderr or r.stdout)
    except Exception as e:
        return False, f"校验异常：{e}"


# ----------------------------------------------------------------------------
# 代码签名身份（designated requirement）
# ----------------------------------------------------------------------------
# 这是 Mac 端「汉化后还能不能更新」的唯一开关，改动前务必读完这段。
#
# 机制：
#   Squirrel.Mac（Electron 的 macOS 更新组件）在安装更新前，会先取**当前正在
#   运行的这个 app** 的 designated requirement（DR，代码身份），再用它去校验
#   下载回来的新包；新包必须满足这条 DR，否则直接拒绝安装。
#
#   默认情况下，`codesign --force --sign -`（ad-hoc 临时签名）生成的 DR 是
#   `cdhash H"..."` —— 也就是「只认这一个人二进制文件的哈希」。
#   官方新版的哈希必然不同，所以 ad-hoc 重签过的 app **永远不可能**通过官方的
#   应用内自动更新。这不是脚本写错，是签名机制本身决定的。
#
# 解法：
#   重签时用 `--requirements` 显式写入一条只锚定 bundle id 的 DR：
#       designated => identifier "com.google.antigravity"
#   这样当前 app 满足自己（identifier 一致，codesign --verify 仍通过），
#   官方新版（Google 签名）也满足（identifier 一致），Squirrel 就会放行。
#
# 代价（必须如实告知用户）：
#   DR 不再校验发布者证书，只校验 bundle id。安全性由更新通道的 HTTPS 与
#   sha512 校验承担。这比 cdhash 宽，但仍然只接受「同一个应用」。
#
# 兼容性：
#   旧版 macOS 或异常环境下 `--requirements` 可能不被接受。因此调用方必须
#   准备好「写入失败 → 回退普通重签」的降级路径，并在重签后回读 DR 确认。

def _mac_bundle_identifier(app_bundle):
    """读取 .app 的 CFBundleIdentifier。失败返回 None。
    优先用 plutil（系统自带，最可靠），再用 plistlib 兜底。"""
    plist = os.path.join(app_bundle, "Contents", "Info.plist")
    if not os.path.isfile(plist):
        return None
    try:
        r = subprocess.run(["plutil", "-extract", "CFBundleIdentifier", "raw", "-o", "-", plist],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=30)
        if r.returncode == 0:
            bid = (r.stdout or "").strip()
            if re.fullmatch(r"[A-Za-z0-9._-]{1,200}", bid):
                return bid
    except Exception:
        pass
    try:
        import plistlib
        with open(plist, "rb") as f:
            d = plistlib.load(f)
        bid = str(d.get("CFBundleIdentifier") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._-]{1,200}", bid):
            return bid
    except Exception:
        pass
    return None


def _mac_signing_identifier(app_bundle):
    """读取「当前签名」里的 Identifier（codesign -d --verbose=4 的 Identifier= 行）。
    必须在重签**之前**调用：重签后读到的就是新签名的标识了。

    为什么优先用它而不是 Info.plist 的 CFBundleIdentifier：
    两者通常相同，但官方若显式指定过 --identifier，就可能不同；
    用签名里的真实标识才能保证 DR 同时匹配「当前 app」和「官方新包」。"""
    try:
        r = subprocess.run(["codesign", "-d", "--verbose=4", app_bundle],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
        text = (r.stderr or "") + (r.stdout or "")
        mm = re.search(r"^Identifier=(.+)$", text, re.M)
        if mm:
            val = mm.group(1).strip()
            if re.fullmatch(r"[A-Za-z0-9._-]{1,200}", val):
                return val
    except Exception:
        pass
    return None


def _mac_update_friendly_requirement(app_bundle):
    """构造稳定 DR（只锚定签名 identifier），返回 codesign -r 的字面量参数。
    拿不到标识时返回 None（调用方回退普通重签）。"""
    ident = _mac_signing_identifier(app_bundle) or _mac_bundle_identifier(app_bundle)
    if not ident:
        return None
    # 只允许安全字符，避免 requirement 语法被畸形标识破坏
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", ident):
        return None
    return f'=designated => identifier "{ident}"'


def _mac_read_designated_requirement(app_bundle):
    """回读当前签名里的 designated requirement 文本。
    codesign 把 DR 打到 stderr；失败返回空串。"""
    try:
        r = subprocess.run(["codesign", "-d", "-r-", app_bundle],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
        return ((r.stderr or "") + (r.stdout or "")).strip()
    except Exception:
        return ""


def _mac_dr_line(dr_text):
    """从 `codesign -d -r-` 的输出里取出 designated requirement 那一行。

    为什么单独抽出来：codesign 的输出里还会有 Executable= / Identifier= /
    Format= 等行，直接拿最后一行或整体字符串判断都可能误判。
    只认以 `designated =>` 开头的行；没有这一行就返回空（视为「没有可用身份」）。"""
    for ln in (dr_text or "").splitlines():
        s = ln.strip()
        if s.startswith("designated =>"):
            return s
    return ""


def _mac_dr_is_stable(dr_text):
    """DR 是否已经摆脱 cdhash 锚定（即「跨版本稳定」）。
    只看 designated 那一行；ad-hoc 默认生成的 DR 一定含 cdhash。"""
    line = _mac_dr_line(dr_text).lower()
    if not line:
        return False
    return "cdhash" not in line


# Mach-O 魔数（含 32/64 位、大小端与 fat 包）。只读文件头 4 字节即可判断，
# 不依赖 file 命令，也不会被 Resources 里的同名文本文件骗到。
_MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",   # 大端 32/64
    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",   # 小端 32/64
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",   # fat（大端/小端）
}


def _mac_is_macho(path):
    """按魔数判断是否 Mach-O 文件。符号链接、目录、读不了的文件一律返回 False。"""
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return False
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    return head in _MACHO_MAGIC


def _mac_framework_resource_binaries(version_dir):
    """列出 framework 内 Resources / Libraries 下的 Mach-O。

    背景：codesign 只把 Contents/Frameworks、Contents/PlugIns、Contents/XPCServices、
    Contents/Helpers 这些固定位置当「嵌套代码」递归处理；Resources 里的可执行文件
    只被记一条文件哈希，**不会被重签**。

    Squirrel.framework/Versions/A/Resources/ShipIt 正是这种情况：
    它保持官方签名（hardened runtime、带 Team ID），却要加载被我们改成 ad-hoc 的
    Mantle/ReactiveObjC。库校验发现 Team ID 不同，dyld 直接拒绝加载：
      Library not loaded: @rpath/Mantle.framework/Mantle（different Team IDs）
    后果就是用户点「重启以更新」后主程序退出、ShipIt 起不来，既不安装也不重启。

    这些文件必须**先于 framework 本体**签名：先签本体的话，CodeResources 里记录的
    文件哈希会立刻过期，--verify 会直接报错。
    """
    out = []
    skip_suffix = (".lproj", ".app", ".framework", ".xpc", ".appex")
    for sub in ("Resources", "Libraries"):
        root_dir = os.path.join(version_dir, sub)
        if not os.path.isdir(root_dir):
            continue
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if not d.endswith(skip_suffix)]
            for fn in files:
                p = os.path.join(root, fn)
                if _mac_is_macho(p):
                    out.append(p)
    return out


def _mac_nested_code_items(app_bundle):
    """枚举 .app 内需要签名的嵌套代码（framework / helper app / dylib / 插件）。

    Apple 明确要求：嵌套代码应逐个签名，且**不能带 entitlements**
    （官方文档：不要在签 framework 时包含授权，包含它们会产生无效签名）。
    返回按"由内到外"排序的路径列表。"""
    items = []
    fw = os.path.join(app_bundle, "Contents", "Frameworks")
    if os.path.isdir(fw):
        try:
            for name in sorted(os.listdir(fw)):
                p = os.path.join(fw, name)
                if name.endswith(".framework"):
                    # framework 必须签 Versions/<具体版本>，不能签顶层（符号链接）
                    vers = os.path.join(p, "Versions")
                    if os.path.isdir(vers):
                        subs = [d for d in sorted(os.listdir(vers))
                                if d != "Current"]
                        if subs:
                            for s in subs:
                                vdir = os.path.join(vers, s)
                                # Resources/Libraries 里的 Mach-O 不在 codesign 认可的
                                # 嵌套代码位置上，必须显式先签（Squirrel 的 ShipIt 就是）
                                items.extend(_mac_framework_resource_binaries(vdir))
                                items.append(vdir)
                        else:
                            items.extend(_mac_framework_resource_binaries(p))
                            items.append(p)
                    else:
                        items.extend(_mac_framework_resource_binaries(p))
                        items.append(p)
                elif name.endswith((".app", ".dylib", ".node", ".so")):
                    items.append(p)
                elif os.path.isdir(p) and not name.endswith(".lproj"):
                    items.append(p)
        except OSError:
            pass
    # 顶层 PlugIns / XPCServices / Helpers 里的 appex 与 helper app
    for sub in ("PlugIns", "XPCServices", "Helpers"):
        d = os.path.join(app_bundle, "Contents", sub)
        if not os.path.isdir(d):
            continue
        try:
            for name in sorted(os.listdir(d)):
                p = os.path.join(d, name)
                if os.path.isdir(p) and name.endswith((".app", ".appex")):
                    items.append(p)
        except OSError:
            pass
    # 深度优先：层级更深（内层）的先签，保证"由内到外"
    items = sorted(set(items), key=lambda x: (-x.count(os.sep), x))
    return items


def _mac_sign_once(target, identity="-", entitlements=None, sudo=False,
                   verbose=False, requirements=None):
    """执行一次 codesign。sudo=True 时走 osascript 弹窗授权。
    返回 (是否成功, 输出信息)。全程参数列表形式，无 shell 拼接。

    requirements: 传给 codesign -r 的字面量（以 = 开头）。仅用于主包，
    用来写入稳定的 designated requirement，保证官方应用内更新不被拒。

    sudo 路径的实现要点（这里刻意不用字符串拼接 + eval）：
      把完整命令写进临时 .sh 脚本，osascript 只执行
      `do shell script "/bin/bash <脚本路径>" with administrator privileges`。
      这样应用路径完全不进入 AppleScript 字符串，
      也就绕开了 AppleScript 与 POSIX 两层转义的坑——
      含空格、单引号、$ 等特殊字符的路径都能正确执行。
    """
    cmd = ["codesign", "--force", "--sign", identity]
    if requirements:
        cmd += ["--requirements", requirements]
    if entitlements:
        cmd += ["--entitlements", entitlements]
    if verbose:
        cmd.append("--verbose")
    cmd.append(target)

    # 路径含控制字符时无法安全写入单行脚本，直接拒绝（macOS 上极罕见）
    for c in cmd:
        if _has_control_chars(c):
            return False, f"路径含控制字符，已拒绝执行：{c!r}"

    if sudo:
        return _mac_sign_via_admin(cmd)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=900)
    except subprocess.TimeoutExpired:
        return False, "签名超时"
    except (FileNotFoundError, PermissionError, OSError) as e:
        # 命令不存在 / 无权限 / 其他系统错误：返回可读原因，不让脚本崩掉
        return False, f"无法执行 codesign：{e}"
    if r.returncode != 0:
        return False, _zh_error(r.stderr or r.stdout)
    return True, ""


def _mac_sign_via_admin(cmd):
    """通过系统密码框以管理员权限执行 codesign。
    做法：命令落盘成临时 shell 脚本，osascript 只执行脚本路径。
    路径不进入 AppleScript 字符串，因此不存在转义/注入风险。"""
    ok = _mac_run_admin(cmd)
    if ok:
        return True, ""
    return False, "管理员授权失败或用户取消了密码输入"


def _mac_cleanup(ent):
    if ent:
        try:
            os.remove(ent)
        except OSError:
            pass


def _mac_cleanup_stale_temp():
    """清理历史遗留的临时文件（上次运行被强杀/中断时的残留）。
    这些文件带 PID，放在系统临时目录里长期累积不干净。"""
    import glob
    import time as _t
    tmp = tempfile.gettempdir()
    cutoff = _t.time() - 3600          # 只清理 1 小时前的，避免误删并发运行的
    for pat in ("agy_zh_entitlements_*.plist", "agy_zh_ent_default_*.plist",
                "agy_zh_sign_*.sh", "agy_zh_admin_*.sh"):
        for p in glob.glob(os.path.join(tmp, pat)):
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass


def _mac_resign(app_bundle, use_sudo=True, auto_yes=False):
    """执行重签。采用 Apple 推荐方式：内层组件裸签 -> 外层主包带 entitlements 签。
    返回 True 表示签名最终有效。

    为什么不用 `codesign --deep --entitlements ...` 一把梭：
      Apple 官方明确反对 --deep（"Considered Harmful"），因为它把同一份签名选项
      套用到每个嵌套代码项。Electron 的 Helper 进程与主程序权限需求不同
      （Helper 应继承沙盒而非声明 JIT 等主进程权限），混签会生成无效签名，
      表现为 codesign 校验通过但 App 启动即崩。
    """
    if not _codesign_available():
        log("未找到 codesign 命令（需安装 Xcode 命令行工具：xcode-select --install）", warn=True)
        return False

    # 清理历史残留（上次运行被中断时留下的）
    _mac_cleanup_stale_temp()

    # 1) 清扩展属性（quarantine / 资源分叉等，它们会独立导致"已损坏"）
    try:
        subprocess.run(["xattr", "-cr", app_bundle], capture_output=True, timeout=300)
    except Exception:
        pass

    # 2) 导出原 entitlements（保留 Electron 必需的 JIT 等权限）
    ent = _mac_export_entitlements(app_bundle)
    if ent:
        log("已导出原包权限清单（entitlements），重签时将保留", ok=True)
    else:
        ent = _write_default_entitlements()
        if ent:
            log("未能导出原包 entitlements，改用通用 Electron 权限重签", warn=True)
        else:
            log("无法准备 entitlements 文件，将裸签（可能影响启动）", warn=True)

    # 从这里开始，无论成功失败甚至被 Ctrl+C 中断，都必须清理临时文件
    try:
        return _mac_resign_inner(app_bundle, ent, use_sudo)
    finally:
        _mac_cleanup(ent)


def _mac_resign_inner(app_bundle, ent, use_sudo):
    """实际签名流程（由 _mac_resign 包装，确保临时文件一定被清理）"""
    # 3) 先签所有嵌套代码——不带 entitlements（Apple 明确要求）
    nested = _mac_nested_code_items(app_bundle)
    if nested:
        log(f"正在签名 {len(nested)} 个嵌套组件（不带权限清单）...")
    failed_nested = []
    for item in nested:
        ok, err = _mac_sign_once(item, "-", None, sudo=False)
        if not ok:
            ok2, err2 = _mac_sign_once(item, "-", None, sudo=use_sudo and IS_MAC)
            if not ok2:
                failed_nested.append((os.path.basename(item), err2 or err))
    if failed_nested:
        log(f"{len(failed_nested)} 个嵌套组件签名失败："
            + ", ".join(n for n, _ in failed_nested[:3]), warn=True)
    elif nested:
        log("嵌套组件签名完成", ok=True)

    # 4) 最后签主包（带 entitlements + 稳定签名要求）
    #    -r 写入「只锚定签名标识」的 designated requirement，
    #    否则 ad-hoc 默认生成 cdhash 锚定的身份，官方应用内更新必然被拒。
    dr = _mac_update_friendly_requirement(app_bundle)
    log("正在签名主应用包（带权限清单）...")

    # 4a) 先试普通权限。绝大多数情况下 /Applications 已有写权限。
    ok, err = _mac_sign_once(app_bundle, "-", ent, sudo=False, requirements=dr)

    # 4b) 失败 → 先判断是不是权限问题，**保持 DR** 走管理员重试。
    #     这里不能先回退：一旦把 dr 置空，sudo 重试就不带稳定身份了，
    #     结果是「签名成功但更新能力被静默丢掉」。
    if not ok and use_sudo and IS_MAC:
        log("需要管理员权限，正在弹出系统密码框...", warn=True)
        ok, err = _mac_sign_once(app_bundle, "-", ent, sudo=True, requirements=dr)

    # 4c) 带 DR 仍然失败，才判定为「系统不接受 --requirements」，回退普通重签。
    if not ok and dr:
        log(f"稳定签名要求写入失败，回退普通重签（应用内更新将不可用）：{err}", warn=True)
        dr = None
        ok, err = _mac_sign_once(app_bundle, "-", ent, sudo=use_sudo and IS_MAC)

    if not ok:
        log(f"主包签名失败：{err}", warn=True)
        return False

    # 4d) 校验（--deep 在校验场景下是官方认可的用法）
    valid, msg = _mac_verify_signature(app_bundle)
    if not valid and use_sudo and IS_MAC:
        ok2, _ = _mac_sign_once(app_bundle, "-", ent, sudo=True, requirements=dr)
        if ok2:
            valid, msg = _mac_verify_signature(app_bundle)
        if not valid and dr:
            # 带稳定身份仍校验不过：以「能打开」为最高优先级，退回普通重签
            log("带稳定签名要求仍校验失败，回退普通重签", warn=True)
            ok3, _ = _mac_sign_once(app_bundle, "-", ent, sudo=True)
            if ok3:
                dr = None
                valid, msg = _mac_verify_signature(app_bundle)

    # 4e) 回读签名身份（放在最终签名之后，避免报告过期状态）
    dr_text = _mac_read_designated_requirement(app_bundle)
    dr_line = _mac_dr_line(dr_text)
    if _mac_dr_is_stable(dr_text):
        log("签名身份已固定为 bundle id（官方应用内自动更新可用）", ok=True)
        if dr_line:
            log(f"  当前签名身份：{dr_line[:160]}")
    else:
        log("签名身份仍是 ad-hoc 单版本锚定（cdhash）——"
            "官方应用内自动更新会被拒绝，这是可预期的降级", warn=True)
        if dr_line:
            log(f"  当前签名身份：{dr_line[:160]}", warn=True)

    # 临时文件清理由外层 _mac_resign 的 finally 统一负责
    if valid:
        log("代码签名重签完成并校验通过", ok=True)
        return True
    log(f"重签后校验未通过：{msg}", warn=True)
    return False


def _shquote(s):
    """单引号转义，供写入 shell 脚本使用（POSIX 规则）。
    注意：仅在"写入脚本文件后由 bash 直接解析"的场景下有效，
    不可用于 eval（eval 会二次解析导致转义失效）。"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def _has_control_chars(s):
    """路径是否含控制字符（换行/NUL/制表符等）。
    这类字符无法安全地写入单行 shell 脚本，遇到时直接拒绝而非冒险执行。"""
    return any(ord(c) < 32 or ord(c) == 127 for c in str(s))


# macOS 系统命令的报错是英文，这里做常见错误的中文化。
# 未收录的原文保留（便于用户搜索或反馈），前面加「系统提示：」以示区分。
_CODESIGN_ERRORS = [
    ("Permission denied", "权限不足，请重试并输入 Mac 密码"),
    ("Operation not permitted", "权限不足（系统禁止该操作），请确认已允许修改"),
    ("No such file or directory", "文件或目录不存在"),
    ("code object is not signed at all", "目标未被签名（这是正常状态，重签即可）"),
    ("invalid signature", "签名无效"),
    ("signature is invalid", "签名无效"),
    ("bundle format unrecognized, invalid, or unsuitable",
     "应用包格式无法识别（可能不是 Antigravity.app）"),
    ("resource fork, Finder information, or similar detritus not allowed",
     "存在资源分叉或 Finder 附加信息，请先执行：xattr -cr <应用路径>"),
    ("Command line tool invalid", "签名参数无效"),
    ("invalid or unsupported format for signature", "签名格式不受支持"),
    ("The signature was invalidated", "签名已失效"),
    ("Unable to open", "无法打开目标文件"),
    ("No such directory", "目录不存在"),
]


def _zh_error(raw, limit=300):
    """把系统英文报错转为可读中文；未收录则保留原文并标注来源。"""
    s = (raw or "").strip()
    if not s:
        return ""
    low = s.lower()
    for needle, zh in _CODESIGN_ERRORS:
        if needle.lower() in low:
            extra = ""
            # 保留关键英文关键词，方便用户自行搜索
            if needle == "resource fork, Finder information, or similar detritus not allowed":
                extra = ""
            return f"{zh}（系统原文：{s[:limit]}）" if len(s) < 40 else f"{zh}（{s[:limit]}）"
    return f"系统提示：{s[:limit]}"


_DEFAULT_ENTITLEMENTS = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.inherit</key><true/>
</dict>
</plist>
"""


def _write_default_entitlements():
    """兜底 entitlements：Electron 运行时必需的 JIT 等权限"""
    p = os.path.join(tempfile.gettempdir(), f"agy_zh_ent_default_{os.getpid()}.plist")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(_DEFAULT_ENTITLEMENTS)
        return p
    except OSError:
        return None


def mac_post_deploy(resources_dir, auto_yes=False):
    """部署完成后调用：清除扩展属性 + 重签 + 校验。
    Windows 下此函数不做任何事（直接返回）。"""
    if not IS_MAC:
        return True
    bundle = find_app_bundle(resources_dir)
    if not bundle:
        log("未能定位 .app 包，跳过签名步骤（不影响汉化内容）", warn=True)
        return True
    log(f"macOS 签名处理：{bundle}", ok=True)
    ok, msg = _mac_verify_signature(bundle)
    if ok:
        log(f"当前签名状态：{msg}", ok=True)
    else:
        log(f"签名已因改动 app.asar 而失效（预期内）：{msg}", warn=True)
    return _mac_resign(bundle, use_sudo=True, auto_yes=auto_yes)


def _mac_run_admin(argv, timeout=900):
    """以管理员权限执行一个命令（参数列表）。
    做法：命令落盘成临时 shell 脚本，osascript 只执行脚本路径——
    应用路径不进入 AppleScript 字符串，绕开两层转义。
    返回 True 表示执行成功。"""
    if _has_control_chars(" ".join(str(a) for a in argv)):
        return False
    body = "#!/bin/bash\n" + " ".join(_shquote(x) for x in argv) + "\n"
    sh_path = os.path.join(tempfile.gettempdir(),
                           f"agy_zh_admin_{os.getpid()}_{int(time.time() * 1000) % 100000}.sh")
    try:
        with open(sh_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.chmod(sh_path, 0o755)
    except OSError:
        return False
    try:
        osa = ('do shell script "/bin/bash \'%s\'" with administrator privileges'
               % sh_path)
        r = subprocess.run(["osascript", "-e", osa], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        try:
            os.remove(sh_path)
        except OSError:
            pass


def _mac_replace_with_admin(src, dst):
    """用管理员权限把 src 覆盖到 dst（同一卷内仍保持原子替换语义）。"""
    if not IS_MAC:
        return False
    if not os.path.isfile(src):
        return False
    return _mac_run_admin(["mv", "-f", src, dst])


def mac_status_extra(resources_dir):
    """macOS 状态检查追加项：签名健康度 + 更新能力"""
    if not IS_MAC:
        return
    # 没装 codesign 时后面所有检查都会失败，先给一条准确的提示，
    # 避免用户把「工具缺失」误读成「汉化坏了」。
    if not _codesign_available():
        log("代码签名：无法检查（未安装 codesign，请执行 xcode-select --install）", warn=True)
        return
    bundle = find_app_bundle(resources_dir)
    if not bundle:
        return
    ok, msg = _mac_verify_signature(bundle)
    if ok:
        log("代码签名：有效", ok=True)
    else:
        log(f"代码签名：已失效（{msg}）", warn=True)
        log("  修复：选 1 重新汉化，或运行同目录下的「修复签名.command」。", warn=True)
    # 签名身份决定「官方应用内更新」能不能用，单独报一行
    dr_text = _mac_read_designated_requirement(bundle)
    if not dr_text:
        log("更新能力：无法读取签名身份（建议重新汉化一次）", warn=True)
    elif _mac_dr_is_stable(dr_text):
        log("更新能力：官方应用内自动更新可用（签名身份已固定）", ok=True)
    else:
        log("更新能力：官方应用内自动更新不可用（签名身份为单版本哈希）", warn=True)
        log("  原因：ad-hoc 签名默认把身份锚定在当前二进制哈希上，"
            "官方新版哈希不同 → Squirrel 拒绝安装。", warn=True)
        log("  处理：重新运行一次汉化（v1.16+ 会写入稳定身份并补签更新安装器）；"
            "若仍不行，请从官网下载新版覆盖安装后再汉化。", warn=True)


_FIX_SH = r"""#!/bin/bash
# ============================================================
# Antigravity 一键汉化 · macOS 签名修复脚本
# ------------------------------------------------------------
# 什么时候需要用它：
#   1) 汉化完成后双击 Antigravity 提示「应用已损坏，无法打开」，
#      说明 app.asar 被改动后代码签名失效了。运行本脚本重新签名即可。
#   2) 点「重启以更新」后应用退出、却既不安装也不重启（安装器 ShipIt 没跟着重签），
#      运行本脚本会把它一并补签。
#
# 用法：双击本文件 → 输入 Mac 密码 → 等待完成
# ============================================================

APP="__APP_BUNDLE__"

if [ ! -d "$APP" ]; then
  echo "[错误] 找不到应用包：$APP"
  echo "请确认 Antigravity 已安装在 /Applications 下。"
  read -r -p "按回车键关闭..." _
  exit 1
fi

echo ""
echo "=============================================================="
echo "  Antigravity 签名修复"
echo "  应用路径：$APP"
echo "=============================================================="
echo ""

# ---- 1. 请关闭 Antigravity ----
if pgrep -f "$APP/Contents/MacOS" >/dev/null 2>&1; then
  echo "[!] 检测到 Antigravity 正在运行，将先关闭它。"
  osascript -e 'quit app "Antigravity"' >/dev/null 2>&1
  sleep 2
  pkill -f "$APP/Contents/MacOS" 2>/dev/null
  sleep 1
fi

# ---- 2. 清除扩展属性（quarantine 会独立导致"已损坏"）----
echo "[1/4] 清除扩展属性..."
xattr -cr "$APP" 2>/dev/null

# ---- 3. 导出原包 entitlements（Electron 运行时必需）----
echo "[2/4] 导出原包权限清单..."
ENT="$(mktemp -t agy_ent).plist"
if codesign -d --entitlements - --xml "$APP" 2>&1 | tr -d '\000' | sed -n '/<?xml/,/<\/plist>/p' > "$ENT" \
   && [ -s "$ENT" ] && plutil -lint "$ENT" >/dev/null 2>&1; then
  echo "      已保留原包权限清单"
else
  cat > "$ENT" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-dyld-environment-variables</key><true/>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.inherit</key><true/>
</dict>
</plist>
PLIST
  echo "      原包无可用清单，改用通用 Electron 权限"
fi

# ---- 4. 分层签名：先签嵌套组件（不带 entitlements），最后签主包（带）----
# Apple 要求：framework 等嵌套代码逐个签名且不得携带 entitlements
echo "[3/4] 重新签名（如提示权限不足，请输入 Mac 密码）..."
FAIL=0

# codesign 只把 Frameworks / PlugIns / XPCServices / Helpers 这些固定位置当「嵌套代码」
# 递归处理；Resources（和 Libraries）里的可执行文件只被记一条文件哈希，不会被重签。
# Squirrel.framework/Versions/A/Resources/ShipIt 正是这种情况：它保持官方签名，
# 却要加载被我们改成 ad-hoc 的 Mantle/ReactiveObjC，库校验会以「Team ID 不同」
# 拒绝加载 —— 表现为点「重启以更新」后主程序退出、ShipIt 起不来，既不安装也不重启。
# 所以先补签这些散落的 Mach-O，再签 framework 本体；顺序不能反，
# 否则先签本体时记录的哈希会立刻过期，--verify 直接报错。
sign_framework() {
  local v="$1" sub bin
  for sub in Resources Libraries; do
    [ -d "$v/$sub" ] || continue
    while IFS= read -r -d '' bin; do
      case "$(file -b "$bin" 2>/dev/null)" in
        *Mach-O*) codesign --force --sign - "$bin" 2>/dev/null || FAIL=$((FAIL+1)) ;;
      esac
    done < <(find -H "$v/$sub" -type f \( -perm -111 -o -name '*.dylib' -o -name '*.so' \) -print0 2>/dev/null)
  done
  codesign --force --sign - "$v" 2>/dev/null || FAIL=$((FAIL+1))
}

if [ -d "$APP/Contents/Frameworks" ]; then
  for item in "$APP/Contents/Frameworks"/*.framework; do
    [ -e "$item" ] || continue
    if [ -d "$item/Versions" ]; then
      for v in "$item/Versions"/*; do
        [ -e "$v" ] || continue
        [ "$(basename "$v")" = "Current" ] && continue
        sign_framework "$v"
      done
    else
      sign_framework "$item"
    fi
  done
  for item in "$APP/Contents/Frameworks"/*.app "$APP/Contents/Frameworks"/*.dylib; do
    [ -e "$item" ] || continue
    codesign --force --sign - "$item" 2>/dev/null || FAIL=$((FAIL+1))
  done
fi

# 主包：优先写入「只锚定 bundle id」的签名身份，让官方应用内更新不被拒绝；
# 写入失败则回退普通重签（App 能打开优先于能更新）
BID="$(plutil -extract CFBundleIdentifier raw -o - "$APP/Contents/Info.plist" 2>/dev/null)"
SIGN_OK=0
if [ -n "$BID" ]; then
  if codesign --force --sign - --requirements "=designated => identifier \"$BID\"" \
       --entitlements "$ENT" "$APP" 2>/dev/null; then
    SIGN_OK=1
  fi
fi
if [ "$SIGN_OK" -eq 0 ]; then
  codesign --force --sign - --entitlements "$ENT" "$APP" || FAIL=$((FAIL+1))
fi

# ---- 5. 校验 ----
# 注意：$ENT 必须留到校验之后。带稳定身份签完若校验不过，
# 还要用它回退普通重签；提前删掉会让回退变成裸签。
echo "[4/4] 校验签名..."
VERIFY_OK=0
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
  VERIFY_OK=1
elif [ "$SIGN_OK" -eq 1 ]; then
  echo "  [!] 带稳定身份校验未通过，回退普通重签..."
  if codesign --force --sign - --entitlements "$ENT" "$APP" 2>/dev/null \
     && codesign --verify --deep --strict "$APP" 2>/dev/null; then
    VERIFY_OK=1
  fi
fi
rm -f "$ENT"

if [ "$VERIFY_OK" -eq 1 ]; then
  DR="$(codesign -d -r- "$APP" 2>&1 | tr -d '\000')"
  echo ""
  echo "=============================================================="
  echo "  修复成功！现在可以正常打开 Antigravity 了。"
  case "$DR" in
    *cdhash*)
      echo "  注意：签名身份为单版本哈希，官方「应用内自动更新」会被拒绝。"
      echo "        更新请从官网下载新版覆盖安装，再重新汉化。"
      ;;
    *)
      echo "  签名身份已固定，官方「应用内自动更新」可用。"
      echo "        （更新后界面会变回英文，重新汉化即可）"
      ;;
  esac
  echo "=============================================================="
  echo ""
  open "$APP" 2>/dev/null
  read -r -p "按回车键关闭窗口..." _
  exit 0
else
  echo ""
  echo "  [!] 签名校验仍未通过。"
  echo "      请先重新运行一次「一键汉化」——它会重新导出原包权限清单再签，"
  echo "      比手动裸签安全（裸签会丢 entitlements，App 可能启动即崩）。"
  echo "      若仍失败，可打开「终端」手动执行排查："
  echo "      sudo xattr -cr \"$APP\""
  echo "      codesign -d --entitlements - --xml \"$APP\"   # 确认权限清单是否还在"
  echo ""
  read -r -p "按回车键关闭窗口..." _
  exit 1
fi
"""


def _mac_write_fix_script(resources_dir):
    """生成「修复签名.command」到脚本所在目录，返回路径（失败返回 None）。
    用占位符替换而非 str.format，避免 shell 脚本里的 { } 引发 KeyError。"""
    bundle = find_app_bundle(resources_dir)
    if not bundle:
        return None
    if _has_control_chars(bundle):
        log(f"应用路径含控制字符，无法生成修复脚本：{bundle!r}", warn=True)
        return None
    path = os.path.join(SCRIPT_DIR, "修复签名.command")
    try:
        # 防注入：应用路径里的引号在 bash 双引号中需转义
        safe = bundle.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_FIX_SH.replace("__APP_BUNDLE__", safe))
        os.chmod(path, 0o755)
        return path
    except OSError as e:
        log(f"生成修复脚本失败：{e}", warn=True)
        return None


# ----------------------------------------------------------------------------
# 注入逻辑（全部幂等：先剥离旧注入/还原锚点，再重新注入）
# ----------------------------------------------------------------------------
def strip_block(content, start_marker, end_marker):
    """移除标记之间的注入块（含标记行）"""
    if start_marker not in content:
        return content
    s = content.index(start_marker)
    e = content.find(end_marker, s)
    if e == -1:  # 有头无尾（异常态）：删到文件尾
        return content[:s]
    e += len(end_marker)
    # 吃掉尾部换行
    while e < len(content) and content[e] in "\r\n":
        e += 1
    return content[:s] + content[e:]


def revert_replaces(content, pairs):
    """把锚点替换还原为官方原文（幂等重入的关键）"""
    for hooked, original in pairs:
        if hooked in content:
            content = content.replace(hooked, original)
    return content


def apply_anchor_replace(content, find, replace, label, report, alt=None):
    """锚点替换：原文在→替换；替换结果已在→跳过；都不在→记录未命中

    alt：跨版本兼容备选锚点，形如 [{"find": ..., "replace": ...}, ...]。
    官方在新版里改了文案（例如 2.34.0 把 HUD 的 Resume 改成 Allow again）时，
    旧锚点必然找不到；直接报「未命中」会让用户看到一条其实已经处理好的告警。
    这里按「主锚点 → 备选锚点 → 译文是否已在位」的顺序判定，任一命中都不算失败。
    """
    if find in content:
        report.append(("替换", label, True))
        return content.replace(find, replace)
    for a in (alt or []):
        af, ar = a.get("find"), a.get("replace")
        if af and ar and af in content:
            report.append(("替换", f"{label}（旧版文案）", True))
            return content.replace(af, ar)
    probe = replace if len(replace) < 200 else replace[:200]
    if probe in content:
        report.append(("已生效", label, True))
        return content
    for a in (alt or []):
        ar = a.get("replace") or ""
        ap = ar if len(ar) < 200 else ar[:200]
        if ap and ap in content:
            report.append(("已生效", label, True))
            return content
    report.append(("未命中", label, False))
    return content


def inject_engine(content, engine_code, data_json):
    """把引擎追加到 preload 类文件末尾"""
    content = strip_block(content, ENGINE_START, ENGINE_END)
    block = (f"\n{ENGINE_START}\n"
             f"// 由 localize.py 于部署时生成，词典已内联。恢复英文请运行 restore。\n"
             f"{engine_code}\n"
             f"{ENGINE_END}\n")
    return content.rstrip("\n") + "\n" + block


def build_menu_block(data):
    menu_map = data.get("menuMap", {})
    return (f"\n{MENU_START}\n"
            f"const __AGY_ZH_MENU_MAP__ = {json.dumps(menu_map, ensure_ascii=False)};\n"
            f"function __AGY_ZH_MENU_TRANSLATE__(mi) {{\n"
            f"    if (mi && mi.label && __AGY_ZH_MENU_MAP__[mi.label]) {{\n"
            f"        if (mi.__agyZhOrigLabel === undefined) mi.__agyZhOrigLabel = mi.label;\n"
            f"        mi.label = __AGY_ZH_MENU_MAP__[mi.label];\n"
            f"    }}\n"
            f"    if (mi && mi.submenu) {{\n"
            f"        if (mi.submenu.items) mi.submenu.items.forEach(__AGY_ZH_MENU_TRANSLATE__);\n"
            f"        else if (Array.isArray(mi.submenu)) mi.submenu.forEach(__AGY_ZH_MENU_TRANSLATE__);\n"
            f"    }}\n"
            f"}}\n"
            f"{MENU_END}\n")


def build_tray_block():
    return (f"\n{TRAY_START}\n"
            f"function __AGY_ZH_TRAY_ACTIONS__(actions) {{\n"
            f"    const M = {{ 'No agents running': '没有智能体在运行', 'Quit': '退出' }};\n"
            f"    return actions.map(a => {{\n"
            f"        if (a && a.label) {{\n"
            f"            if (M[a.label]) a.label = M[a.label];\n"
            f"            else if (/^Open /.test(a.label)) a.label = '打开 Antigravity';\n"
            f"        }}\n"
            f"        return a;\n"
            f"    }});\n"
            f"}}\n"
            f"{TRAY_END}\n")


# ----------------------------------------------------------------------------
# 原生右键菜单（Electron Menu）
# ----------------------------------------------------------------------------
# 背景：Antigravity 2.31.x 的右键菜单走 `window.electronNative.showContextMenu(items)`
# → 主进程 `Menu.buildFromTemplate` → OS 原生弹窗。它不产生任何 DOM 节点，
# DOM 引擎（preload 里的 MutationObserver）根本看不到，所以整块菜单保持英文。
# 2.31.2 实测：右键会话行后 DOM 中 .agy-overlay 数量为 0，而屏幕上确实弹出菜单，
# 且菜单项无图标、快捷键右对齐 —— 与 renderer 侧 `xYa()` 把项目压成
# {id,label,type,checked,disabled,accelerator}（**丢掉 icon**）后再 IPC 完全吻合。
#
# 修法：在 preload 暴露 `electronNative` 之前包一层，只翻译 label，
# id / disabled / 点击回调 / 快捷键一律原样透传（主进程正是用 id 回传选中的项）。
CTX_ORIG_EXPOSE = "electron_1.contextBridge.exposeInMainWorld('electronNative', electronNativeAPI);"
CTX_HOOKED_EXPOSE = ("electron_1.contextBridge.exposeInMainWorld('electronNative', "
                     "(typeof __AGY_ZH_WRAP_NATIVE__ === 'function' "
                     "? __AGY_ZH_WRAP_NATIVE__(electronNativeAPI) : electronNativeAPI));")
# 2.33.0 起 preload 多了 isRemoteWindow 分支：它手工把 electronNativeAPI 的字段拷成
# remoteNative 再暴露，showContextMenu 拿的是**未包装**的原函数。不单独挂钩的话，
# SSH 远程窗口里的右键菜单永远保持英文（本地窗口正常）。
CTX_REMOTE_ORIG = "electron_1.contextBridge.exposeInMainWorld('electronNative', remoteNative);"
CTX_REMOTE_HOOKED = ("electron_1.contextBridge.exposeInMainWorld('electronNative', "
                     "(typeof __AGY_ZH_WRAP_NATIVE__ === 'function' "
                     "? __AGY_ZH_WRAP_NATIVE__(remoteNative) : remoteNative));")


def build_ctxmenu_block():
    # 注意：这里必须用 function 声明。注入块在文件末尾，而 expose 调用在文件前面，
    # 只有声明才会被提升；写成 const/var 会在 expose 时拿到 undefined。
    return f"""
{CTX_START}
// 把菜单项的 label 过一遍词典；失败一律返回原值，绝不让汉化弄坏右键菜单。
function __AGY_ZH_CTX_LABEL__(label) {{
    try {{
        if (typeof window !== 'undefined' && typeof window.__AGY_ZH_MENU_LABEL__ === 'function') {{
            var out = window.__AGY_ZH_MENU_LABEL__(label);
            return typeof out === 'string' ? out : label;
        }}
    }} catch (e) {{ /* 翻译失败保留英文 */ }}
    return label;
}}
// 深拷贝一层：只换 label，其余字段（含函数、React 元素、子菜单）原样保留。
// 不原地改的原因：同一份 items 数组还可能被网页版菜单复用，改原对象会污染调用方。
function __AGY_ZH_CTX_ITEMS__(items) {{
    if (!Array.isArray(items)) return items;
    var out = [];
    for (var i = 0; i < items.length; i++) {{
        var it = items[i];
        if (!it || typeof it !== 'object') {{ out.push(it); continue; }}
        var copy = {{}};
        for (var k in it) {{
            if (Object.prototype.hasOwnProperty.call(it, k)) copy[k] = it[k];
        }}
        if (typeof it.label === 'string' && it.label) copy.label = __AGY_ZH_CTX_LABEL__(it.label);
        if (Array.isArray(it.items)) copy.items = __AGY_ZH_CTX_ITEMS__(it.items);
        if (Array.isArray(it.submenu)) copy.submenu = __AGY_ZH_CTX_ITEMS__(it.submenu);
        out.push(copy);
    }}
    return out;
}}
function __AGY_ZH_WRAP_NATIVE__(api) {{
    try {{
        if (!api || typeof api !== 'object' || typeof api.showContextMenu !== 'function') return api;
        var wrapped = {{}};
        for (var k in api) {{
            if (Object.prototype.hasOwnProperty.call(api, k)) wrapped[k] = api[k];
        }}
        wrapped.showContextMenu = function (items) {{
            var payload = items;
            try {{ payload = __AGY_ZH_CTX_ITEMS__(items); }} catch (e) {{ payload = items; }}
            return api.showContextMenu(payload);
        }};
        return wrapped;
    }} catch (e) {{
        return api;
    }}
}}
{CTX_END}
"""


def patch_ctx_menu(path, report):
    """给 preload 挂上原生右键菜单翻译钩子（幂等）"""
    c = open(path, encoding='utf-8').read()
    c = strip_block(c, CTX_START, CTX_END)
    if CTX_HOOKED_EXPOSE in c:
        report.append(("已生效", "preload.js 原生右键菜单挂钩", True))
    elif CTX_ORIG_EXPOSE in c:
        c = c.replace(CTX_ORIG_EXPOSE, CTX_HOOKED_EXPOSE)
        report.append(("替换", "preload.js 原生右键菜单挂钩（electronNative 暴露点）", True))
    else:
        report.append(("未命中", "preload.js 原生右键菜单挂钩（官方结构变动，右键菜单可能保持英文）", False))
    # 远程窗口分支（2.33.0+）：单独包一层，否则 SSH 窗口的右键菜单保持英文
    if CTX_REMOTE_HOOKED in c:
        report.append(("已生效", "preload.js 远程窗口右键菜单挂钩", True))
    elif CTX_REMOTE_ORIG in c:
        c = c.replace(CTX_REMOTE_ORIG, CTX_REMOTE_HOOKED)
        report.append(("替换", "preload.js 远程窗口右键菜单挂钩（remoteNative 暴露点）", True))
    else:
        # 2.31.x 及更早没有 isRemoteWindow 分支，属正常
        report.append(("跳过", "preload.js 远程窗口右键菜单挂钩（当前版本无远程窗口）", True))
    c = c.rstrip("\n") + "\n" + build_ctxmenu_block()
    open(path, 'w', encoding='utf-8', newline='').write(c)



# 菜单挂钩点（hooked → original 成对，用于幂等还原）
def menu_hook_pairs():
    set_hooked = ("{ if (typeof __AGY_ZH_MENU_TRANSLATE__ === 'function') "
                  "{ try { menu.items.forEach(__AGY_ZH_MENU_TRANSLATE__); } catch (e) {} } "
                  "electron_1.Menu.setApplicationMenu(menu); }")
    set_orig = "electron_1.Menu.setApplicationMenu(menu);"
    find_hooked = ("const submenuItem = appMenu.items.find((item) => item.label === submenuLabel || "
                   "(typeof __AGY_ZH_MENU_MAP__ !== 'undefined' && item.label === __AGY_ZH_MENU_MAP__[submenuLabel]));")
    find_orig = "const submenuItem = appMenu.items.find((item) => item.label === submenuLabel);"
    tray_hooked = "electron_1.Menu.buildFromTemplate(__AGY_ZH_TRAY_ACTIONS__(actions));"
    tray_orig = "electron_1.Menu.buildFromTemplate(actions);"
    # 更新菜单项的点击处理用 menuItem.label 查动作表（updateActions 的键是英文枚举）。
    # 标签被翻成中文后查不到 → 点击静默无效。加一条回退到原始英文标签。
    upd_click_orig = "const action = updater_1.updateActions[menuItem.label];"
    upd_click_hooked = ("const action = updater_1.updateActions[menuItem.label] || "
                        "updater_1.updateActions[menuItem.__agyZhOrigLabel];")
    return [(set_hooked, set_orig), (find_hooked, find_orig), (tray_hooked, tray_orig),
            (upd_click_hooked, upd_click_orig)]


def patch_all(extract_dir, engine_code, data, report):
    """对解包目录执行全部注入，返回被修改文件列表"""
    modified = []

    def rd(p):
        return open(p, encoding='utf-8').read()

    def wr(p, s):
        open(p, 'w', encoding='utf-8', newline='').write(s)
        # 同一个文件可能被注入多次（menu.js 挂钩 + 子菜单 + 更新菜单；tray.js 挂钩 + 注入），
        # 这里去重：对外报的应该是「被改动的文件数」，不是「写盘次数」。
        if p not in modified:
            modified.append(p)

    data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # engine.js 中的占位符替换为真实词典
    engine_final = engine_code.replace("/*__AGY_ZH_DATA__*/ {}",
                                       "__AGY_ZH_DATA__JSON__").replace(
                                           "__AGY_ZH_DATA__JSON__", data_json)

    # ---- 1. dist/preload.js：DOM 翻译引擎 + 原生右键菜单 ----
    p = os.path.join(extract_dir, "dist", "preload.js")
    if os.path.isfile(p):
        wr(p, inject_engine(rd(p), engine_final, data_json))
        report.append(("注入", "dist/preload.js 界面翻译引擎", True))
        # 引擎块之后追加右键菜单钩子；两者在同一文件，钩子里的函数声明会被提升，
        # 所以 exposeInMainWorld 那行在文件更前面也能取到。
        patch_ctx_menu(p, report)
        if p not in modified:
            modified.append(p)

    # ---- 2. dist/ideInstall/wizardPreload.js：安装向导 ----
    p = os.path.join(extract_dir, "dist", "ideInstall", "wizardPreload.js")
    if os.path.isfile(p):
        wr(p, inject_engine(rd(p), engine_final, data_json))
        report.append(("注入", "dist/ideInstall/wizardPreload.js 向导翻译", True))

    # ---- 3. dist/menu.js：原生菜单 ----
    p = os.path.join(extract_dir, "dist", "menu.js")
    if os.path.isfile(p):
        c = rd(p)
        c = strip_block(c, MENU_START, MENU_END)
        c = revert_replaces(c, menu_hook_pairs())
        # 先挂钩点，再追加定义（函数声明提升，使用点在前也安全）
        set_hooked, set_orig = menu_hook_pairs()[0]
        if set_orig in c:
            c = c.replace(set_orig, set_hooked)
            report.append(("替换", "menu.js 菜单注册挂钩（setApplicationMenu）", True))
        elif set_hooked in c:
            report.append(("已生效", "menu.js 菜单注册挂钩", True))
        else:
            report.append(("未命中", "menu.js 菜单注册挂钩（官方结构变动，菜单可能保持英文）", False))
        find_hooked, find_orig = menu_hook_pairs()[1]
        if find_orig in c:
            c = c.replace(find_orig, find_hooked)
            report.append(("替换", "menu.js 子菜单双语查找兼容", True))
        elif find_hooked in c:
            report.append(("已生效", "menu.js 子菜单双语查找", True))
        else:
            report.append(("未命中", "menu.js 子菜单双语查找", False))
        # 更新菜单项点击：翻译后 label 变中文，updateActions 查不到 → 回退原始 label
        upd_hooked, upd_orig = menu_hook_pairs()[3]
        if upd_orig in c:
            c = c.replace(upd_orig, upd_hooked)
            report.append(("替换", "menu.js 更新菜单点击兼容翻译", True))
        elif upd_hooked in c:
            report.append(("已生效", "menu.js 更新菜单点击兼容", True))
        else:
            report.append(("未命中", "menu.js 更新菜单点击兼容（官方结构变动，菜单项可能失效）", False))
        c = c.rstrip("\n") + "\n" + build_menu_block(data)
        wr(p, c)
        report.append(("注入", "dist/menu.js 菜单翻译映射", True))

    # ---- 4. dist/tray.js：托盘 ----
    p = os.path.join(extract_dir, "dist", "tray.js")
    if os.path.isfile(p):
        c = rd(p)
        c = strip_block(c, TRAY_START, TRAY_END)
        tray_hooked, tray_orig = menu_hook_pairs()[2]
        if tray_orig in c:
            c = c.replace(tray_orig, tray_hooked)
            report.append(("替换", "tray.js 托盘菜单挂钩", True))
        elif tray_hooked in c:
            report.append(("已生效", "tray.js 托盘菜单挂钩", True))
        else:
            report.append(("未命中", "tray.js 托盘菜单挂钩", False))
        c = c.rstrip("\n") + "\n" + build_tray_block()
        wr(p, c)
        report.append(("注入", "dist/tray.js 托盘翻译", True))

    # ---- 5. 静态锚点替换（对话框/向导文案，来自 zh_data.json，均位于 dist/ 下） ----
    for rel, patches in data.get("staticPatches", {}).items():
        rel_norm = rel.replace("/", os.sep)
        if not rel_norm.startswith("dist" + os.sep):
            rel_norm = os.path.join("dist", rel_norm)
        p = os.path.join(extract_dir, rel_norm)
        if not os.path.isfile(p):
            report.append(("跳过", f"{rel}（当前版本无此文件）", True))
            continue
        c = rd(p)
        changed = False
        for patch in patches:
            new = apply_anchor_replace(c, patch["find"], patch["replace"],
                                       f"{rel}: {patch['find'][:30]}", report,
                                       patch.get("alt"))
            changed = changed or new != c
            c = new
        if changed:
            wr(p, c)

    # ---- 6. dist/updater.js：更新菜单项译文兼容 ----
    # updateMenuState() 里 `item.label = step` 会把译文覆盖回英文；
    # 同时该 label 又被 updateActions 当键用。这里保留英文原值到 __agyZhOrigLabel，
    # 显示层用译文，查表仍用英文原值。
    p = os.path.join(extract_dir, "dist", "updater.js")
    if os.path.isfile(p):
        c = rd(p)
        find_orig = ("            item.label = step;\n"
                     "            item.enabled = exports.updateActions[step] !== undefined;")
        step_map = {k: v for k, v in data.get("menuMap", {}).items()
                    if k in ("Check for Updates", "Checking for Updates...",
                             "Downloading Update...", "Restart to Update")}
        find_hooked = ("            item.__agyZhOrigLabel = step;\n"
                       f"            item.label = {json.dumps(step_map, ensure_ascii=False)}[step] || step;\n"
                       "            item.enabled = exports.updateActions[step] !== undefined;")
        if find_orig in c:
            c = c.replace(find_orig, find_hooked)
            wr(p, c)
            report.append(("替换", "updater.js 更新菜单项译文兼容", True))
        elif find_hooked in c:
            report.append(("已生效", "updater.js 更新菜单项译文兼容", True))
        else:
            report.append(("未命中", "updater.js 更新菜单项译文兼容（官方结构变动）", False))

    return modified


# ----------------------------------------------------------------------------
# 备份策略（修复业界通病：官方更新后还原即降级）
# ----------------------------------------------------------------------------
def asar_is_localized(asar_path):
    """字节级检测 asar 是否包含汉化注入标记"""
    try:
        with open(asar_path, "rb") as f:
            chunk_size = 8 * 1024 * 1024
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    return False
                if MARKER_CHECK in chunk:
                    return True
    except OSError:
        return False


def read_asar_header(asar_path):
    """读取 asar 头部 JSON 目录树（Pickle 头：4+4+4+4 字节后是 JSON）"""
    import struct
    with open(asar_path, "rb") as f:
        magic, size, _u, hs = struct.unpack("<4I", f.read(16))
        if magic != 4:
            raise ValueError("非标准 asar 头")
        return json.loads(f.read(hs).decode("utf-8"))


def get_unpacked_set(asar_path):
    """返回 asar 中标记为 unpacked 的全部文件路径集合"""
    header = read_asar_header(asar_path)
    out = set()

    def walk(node, prefix):
        for name, info in node.get("files", {}).items():
            p = f"{prefix}/{name}" if prefix else name
            if "files" in info:
                walk(info, p)
            elif info.get("unpacked"):
                out.add(p)

    walk(header, "")
    return out


def derive_unpack_pattern(asar_path):
    """从官方 asar 自动推导 --unpack-dir 模式（花括号组合），
    取代硬编码的 chrome-devtools-mcp——未来官方往 unpacked 加新内容也能自动适配"""
    entries = get_unpacked_set(asar_path)
    if not entries:
        return None, entries
    dirs = set()
    for p in entries:
        parts = p.split("/")
        # 取两级目录（node_modules/<包名> 粒度）；根级文件单独处理
        dirs.add("/".join(parts[:2]) if len(parts) >= 2 else parts[0])
    # 花括号组合成单条 glob（@electron/asar 的 minimatch 支持花括号展开）
    items = sorted(dirs)
    if len(items) == 1:
        return items[0], entries
    return "{" + ",".join(items) + "}", entries


def _linux_replace_with_admin(src, dst):
    """Linux：当安装目录位于 /opt 或 /usr/lib 等需 root 权限的位置时，通过 sudo 或 pkexec 完成原子替换。

    权限位沿用目标文件原有的 mode（目标不存在时才用 0644）——不能硬编码 644，
    否则会把用户自定义的权限（比如为了共享刻意设的 664）悄悄改掉。"""
    if not IS_LINUX or not os.path.isfile(src):
        return False
    try:
        mode = "%o" % (os.stat(dst).st_mode & 0o7777)
    except OSError:
        mode = "644"
    dst_tmp = dst + f".agyzh.{os.getpid()}.admintmp"
    script = (f"cp -f {_shquote(src)} {_shquote(dst_tmp)}"
              f" && chmod {mode} {_shquote(dst_tmp)}"
              f" && mv -f {_shquote(dst_tmp)} {_shquote(dst)}")
    for tool in ("sudo", "pkexec"):
        if shutil.which(tool):
            try:
                r = subprocess.run([tool, "/bin/sh", "-c", script], timeout=120)
                if r.returncode == 0:
                    return True
            except Exception:
                continue
    return False


def ensure_backup(asar_path):
    """当前 asar 为官方英文时（重新）生成备份；已汉化时不动备份。
    这样官方更新后再次汉化，备份会自动刷新为新版英文，还原永不降级。"""
    bak = asar_path + ".bak"
    if asar_is_localized(asar_path):
        if os.path.isfile(bak):
            return bak, False
        return None, False  # 已汉化但无备份（异常态，调用方负责警告）
    # 当前是官方英文 → 刷新备份
    tmp = bak + ".tmp"
    try:
        shutil.copy2(asar_path, tmp)
        os.replace(tmp, bak)
    except PermissionError:
        if IS_LINUX:
            log("目标目录需要 root 权限写入备份，正在请求 sudo/pkexec 授权...", warn=True)
            if _linux_replace_with_admin(asar_path, bak):
                return bak, True
        raise
    return bak, True


# ----------------------------------------------------------------------------
# language_server 对话标题生成提示词汉化（等字节长度替换，零运行时开销）
# ----------------------------------------------------------------------------
# 背景：
#   Antigravity 左侧会话列表的「对话名称」与「章节标题」由 resources/bin/language_server
#   后台进程调用云端模型自动总结生成。该内部请求使用独立硬编码的系统提示词（不加载
#   ~/.gemini/config/rules/ 下的全局规则），且原提示词写死了：
#     "Respond with ONLY a short conversation title (3-5 words, title-cased, no prefix)"
#   导致即使用户全程用中文提问，生成的对话标题也会被强制翻译成英文（Title Case）。
#
# 解法：
#   对 language_server 中的这两条提示词片段做**严格等字节长度**（214B -> 214B、
#   238B -> 238B）替换，将其改为要求生成简体中文标题。
#   由于替换前后的 UTF-8 字节长度完全一致，ELF / PE / Mach-O 二进制内的段偏移、
#   符号表与 Go 字符串长度头（movq $0x272 / $0x1e1）均保持 100% 原样，零运行时开销。
LS_TITLE_PATCHES = [
    (
        "对话标题中文生成提示词",
        b"Respond with ONLY a short conversation title (3-5 words, title-cased, no prefix) "
        b"describing the USER's intent. Keep abbreviations and acronyms from the USER's "
        b"request as-is rather than guessing what they stand for.",
        "Respond with ONLY a short Simplified Chinese conversation title "
        "(简体中文标题, 4-12 Chinese characters, no quotes, no prefix) "
        "describing the USER's intent. Keep abbreviations/acronyms from USER request as-is.".encode("utf-8"),
    ),
    (
        "章节标题中文生成提示词",
        b"Reply with only a plain 3 to 6 word title in sentence case, with no quotes or "
        b"trailing period, naming what the USER worked on; use the replies only as context. "
        b"For example: Fixing the login redirect bug, or Planning the websocket rollout.",
        "Reply with only a plain Simplified Chinese title (简体中文, 4-14 chars), with no quotes "
        "or trailing period, naming what the USER worked on; use the replies only as context. "
        "E.g. 修复登录重定向问题, or 规划 WebSocket 部署.".encode("utf-8"),
    ),
]


def find_language_server(resources_dir):
    """定位 resources/bin 下的 language_server 二进制文件（不存在时返回 None）。"""
    bin_dir = os.path.join(resources_dir, "bin")
    if not os.path.isdir(bin_dir):
        return None
    for name in ("language_server", "language_server.exe"):
        p = os.path.join(bin_dir, name)
        if os.path.isfile(p):
            return p
    return None


def ls_is_localized(ls_path):
    """字节级检测 language_server 是否已打上中文对话标题补丁。"""
    if not ls_path or not os.path.isfile(ls_path):
        return False
    try:
        with open(ls_path, "rb") as f:
            data = f.read()
        return any(rep in data for _, _, rep in LS_TITLE_PATCHES)
    except OSError:
        return False


def patch_language_server(resources_dir, work_dir, report):
    """对 language_server 执行等字节长度的对话标题提示词汉化（带防降级备份与原子替换）。
    macOS 下因重签名开销与严格校验默认不改二进制；Linux（及 Windows）下自动生效。"""
    if IS_MAC:
        return
    ls_path = find_language_server(resources_dir)
    if not ls_path:
        return
    try:
        with open(ls_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        log(f"读取 language_server 失败，跳过标题汉化：{e}", warn=True)
        return

    # 1. 防降级备份：仅当当前二进制未包含汉化补丁时才刷新 .bak
    bak = ls_path + ".bak"
    already_patched = any(rep in raw for _, _, rep in LS_TITLE_PATCHES)
    if not already_patched:
        tmp_bak = bak + f".agyzh.{os.getpid()}.tmp"
        try:
            shutil.copy2(ls_path, tmp_bak)
            os.replace(tmp_bak, bak)
        except PermissionError:
            if IS_LINUX:
                _linux_replace_with_admin(ls_path, bak)
        except OSError:
            pass
        finally:
            try:
                if os.path.isfile(tmp_bak):
                    os.remove(tmp_bak)
            except OSError:
                pass

    # 2. 等长字节替换（严格断言长度相等，绝不改变二进制任何偏移）
    new_raw = raw
    changed = False
    for label, orig_b, rep_b in LS_TITLE_PATCHES:
        if len(orig_b) != len(rep_b):
            report.append(("跳过", f"language_server {label}（字节长度不一致，安全跳过）", False))
            continue
        if orig_b in new_raw:
            new_raw = new_raw.replace(orig_b, rep_b, 1)
            changed = True
            report.append(("替换", f"language_server {label}（{len(orig_b)} 字节等长替换）", True))
        elif rep_b in new_raw:
            report.append(("已生效", f"language_server {label}", True))
        else:
            report.append(("未命中", f"language_server {label}（官方提示词变动，不影响主界面）", False))

    if not changed or len(new_raw) != len(raw):
        return

    # 3. 保持原有可执行权限位（0755）并做 POSIX 原子替换（即使进程正在运行也不会 ETXTBSY）
    try:
        st_mode = os.stat(ls_path).st_mode & 0o7777
    except OSError:
        st_mode = 0o755
    bin_dir = os.path.dirname(ls_path)
    temp_dir = bin_dir if os.access(bin_dir, os.W_OK) else work_dir
    temp_ls = os.path.join(temp_dir, f"language_server.agyzh.{os.getpid()}.tmp")
    try:
        with open(temp_ls, "wb") as f:
            f.write(new_raw)
        os.chmod(temp_ls, st_mode)
        try:
            os.replace(temp_ls, ls_path)
            log("对话标题中文化：language_server 提示词补丁已部署（原版备份在同目录 .bak）", ok=True)
        except (PermissionError, OSError) as e:
            import errno as _errno
            if isinstance(e, OSError) and getattr(e, "errno", None) == _errno.EXDEV and os.access(bin_dir, os.W_OK):
                local_tmp = os.path.join(bin_dir, f"language_server.agyzh.{os.getpid()}.tmp")
                shutil.copy2(temp_ls, local_tmp)
                os.chmod(local_tmp, st_mode)
                os.replace(local_tmp, ls_path)
                log("对话标题中文化：language_server 提示词补丁已部署（原版备份在同目录 .bak）", ok=True)
            elif IS_LINUX and _linux_replace_with_admin(temp_ls, ls_path):
                log("对话标题中文化：language_server 提示词补丁已通过提权部署", ok=True)
            else:
                log(f"更新 language_server 失败（跳过对话标题补丁，不影响界面汉化）：{e}", warn=True)
    finally:
        try:
            if os.path.isfile(temp_ls):
                os.remove(temp_ls)
        except OSError:
            pass


def restore_language_server(resources_dir):
    """若 language_server 存在官方备份且已被汉化，则一并原子还原。"""
    ls_path = find_language_server(resources_dir)
    if not ls_path or not ls_is_localized(ls_path):
        return False
    bak = ls_path + ".bak"
    if not os.path.isfile(bak) or ls_is_localized(bak):
        log("未找到干净的 language_server.bak，跳过标题生成器还原", warn=True)
        return False
    try:
        st_mode = os.stat(ls_path).st_mode & 0o7777
    except OSError:
        st_mode = 0o755
    tmp = ls_path + f".restore.{os.getpid()}.tmp"
    try:
        shutil.copy2(bak, tmp)
        os.chmod(tmp, st_mode)
        os.replace(tmp, ls_path)
        log("已同步恢复 language_server 官方原版", ok=True)
        return True
    except PermissionError:
        if IS_LINUX and _linux_replace_with_admin(bak, ls_path):
            log("已通过提权恢复 language_server 官方原版", ok=True)
            return True
    except OSError as e:
        log(f"恢复 language_server 失败：{e}", warn=True)
    finally:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return False


def get_asar_version(extract_dir):
    try:
        pkg = json.load(open(os.path.join(extract_dir, "package.json"), encoding='utf-8'))
        return pkg.get("version", "?")
    except Exception:
        return "?"


# ----------------------------------------------------------------------------
# 主管线
# ----------------------------------------------------------------------------
def cmd_apply(resources, npx, auto_yes=False, no_kill=False):
    asar = os.path.join(resources, "app.asar")
    if not os.path.isfile(asar):
        die(f"未找到 {asar}")

    print("=" * 62)
    print(f"  Antigravity 一键汉化 {TOOL_VERSION}（Windows / macOS / Linux 三平台）")
    print("=" * 62)
    log(f"目标：{asar}")

    # 0. 引擎与词典自检（支持同目录外部文件 & 单文件编译版内置归档）
    engine_code = load_asset_text("engine.js", ENGINE_PATH)
    if not engine_code:
        die(f"缺少引擎文件：{ENGINE_PATH}")
    data_text = load_asset_text("zh_data.json", DATA_PATH)
    if not data_text:
        die(f"缺少词典文件：{DATA_PATH}")
    data = json.loads(data_text)
    log(f"词典装载：主词典 {len(data.get('exact', {}))} 条 / 正则 {len(data.get('regex', []))} 条 "
        f"/ 菜单 {len(data.get('menuMap', {}))} 条", ok=True)

    # 1. 备份（防降级策略）
    bak, refreshed = ensure_backup(asar)
    if bak:
        log(f"官方原版备份：{bak}" + ("（已刷新为当前官方版本）" if refreshed else ""), ok=True)
    else:
        log("警告：当前 asar 已是汉化状态且找不到原版备份。", warn=True)
        log("仍可继续（注入是幂等的），但将无法一键还原英文。", warn=True)
        if not auto_yes:
            if input("    是否继续？(y/N)：").strip().lower() not in ("y", "yes"):
                die("已取消。", log=False)

    # 2. 关闭进程（按路径过滤，只杀 Antigravity 自己的）
    kill_processes(auto=auto_yes, resources_dir=resources, no_kill=no_kill)

    # 3. 解包
    work = tempfile.mkdtemp(prefix="agy_zh_")
    # 用 atexit 兜底：即使用户 Ctrl+C 强杀，进程退出时也会清理工作目录，
    # 避免在系统临时目录里留下几十 MB 的残留
    import atexit
    _work_ref = [work]

    def _cleanup_work():
        w = _work_ref[0]
        if w and os.path.isdir(w):
            shutil.rmtree(w, ignore_errors=True)

    atexit.register(_cleanup_work)
    extract_dir = os.path.join(work, "x")
    log("正在解包 app.asar ...")
    extract_any(npx, asar, extract_dir)
    ver = get_asar_version(extract_dir)
    log(f"检测到 Antigravity 版本：v{ver}", ok=True)
    # 身份守卫：确认这是 Antigravity 的程序包（防止 --path 指错目录误伤其他软件）
    try:
        pkg = json.load(open(os.path.join(extract_dir, "package.json"), encoding='utf-8'))
        pkg_name = (pkg.get("name") or pkg.get("productName") or "").strip().lower()
    except Exception:
        pkg_name = ""
    if pkg_name != "antigravity":
        die(f"目标程序包的身份是「{pkg_name or '未知'}」，不是 Antigravity。\n"
            "  为避免影响其他软件，已中止。请检查安装路径是否指向 Antigravity。")
    if ver != TESTED_VERSION:
        log(f"注意：深度适配版本为 v{TESTED_VERSION}，当前为 v{ver}。", warn=True)
        log("锚点替换均带容错（未命中只跳过不报错），一般可正常使用。", warn=True)

    # 4. 注入
    report = []
    modified = patch_all(extract_dir, engine_code, data, report)
    log(f"注入完成，共修改 {len(modified)} 个文件", ok=True)

    # 5. 语法门禁（有 node 用 node --check；没有则走结构自检）
    if NODE_OK:
        bad = node_check(modified)
        if bad:
            for f, err in bad:
                print(_fit_glyphs(f"[✗] 语法校验失败：{os.path.basename(f)}\n{err}"))
            cleanup_temp(resources, work)
            die("语法门禁未通过，已中止（原 app.asar 未被修改，软件不受影响）。")
        log(f"语法门禁：{len(modified)} 个文件全部通过 node --check", ok=True)
    else:
        problems, warns = structure_check(modified)
        for w in warns:
            log("  " + w, warn=True)
        if problems:
            for p in problems:
                print(_fit_glyphs(f"[✗] 结构自检失败：{p}"))
            cleanup_temp(resources, work)
            die("结构自检未通过，已中止（原 app.asar 未被修改，软件不受影响）。")
        log(f"结构自检：{len(modified)} 个文件通过（未装 Node.js，跳过 node --check）", warn=True)

    # 6. 打包（排除目录自动从 asar 推导，未来版本新增 unpacked 内容也能适配；
    #    临时文件名带 PID：即使极端情况下并发执行也不会互相覆盖）
    #    Linux 下若安装目录（如 /opt/Antigravity/resources）普通用户不可写，则先打到临时目录 work 再提权部署
    temp_dir = resources if os.access(resources, os.W_OK) else work
    temp_asar = os.path.join(temp_dir, f"app.asar.agyzh.{os.getpid()}.tmp")
    # 结构参照：优先用干净的官方备份（当前部署包本身可能带历史布局偏差）
    bak = asar + ".bak"
    ref_asar = bak if (os.path.isfile(bak) and not asar_is_localized(bak)) else asar
    try:
        official_unpacked = get_unpacked_set(ref_asar)
    except Exception as e:
        # 备份文件损坏（磁盘错误、被半截写入）时会解析失败。直接抛就是 Python 栈，
        # 用户完全看不懂。退一步用当前包当参照：结构守卫照样生效，只是参照对象换了。
        log(f"参照包头部解析失败（{e}），改用当前包作为结构参照", warn=True)
        ref_asar = asar
        official_unpacked = get_unpacked_set(ref_asar)
    # 参照包里「索引声明为 unpacked、但外部文件实际不存在」的条目，本来就读不到，
    # 不能算成打包遗漏——否则 2.28.1 那种官方漏文件会让结构守卫误报中止。
    #
    # 两个坑（都实测踩过，别改回去）：
    # 1) 外部文件统一放在**当前** asar 的 .unpacked 目录里，`.bak` 没有配套的
    #    `app.asar.bak.unpacked`。所以判断实际存在性必须用 asar + ".unpacked"，
    #    不能拿 ref_asar 拼路径，否则会全部判为"缺失"、把守卫变成空壳。
    # 2) 不能只依赖 skipped_unpacked：从「已汉化 asar」出发时，包本身不含坏条目，
    #    官方工具能正常解包，skipped_unpacked 为空，但参照包（.bak 官方原版）里
    #    仍然有那条缺失项 → 守卫误报。必须独立按实际文件存在性判定。
    _udir = asar + ".unpacked"
    if official_unpacked and os.path.isdir(_udir):
        absent = {p for p in official_unpacked
                  if not os.path.isfile(os.path.join(_udir, p.replace('/', os.sep)))}
        if absent:
            official_unpacked -= absent
            log(f"参照包有 {len(absent)} 个外部文件实际不存在（官方包自身问题），已从结构守卫排除", warn=True)
    # 内置打包器按"目录前缀"判定外部化，与官方 --unpack-dir 的 startsWith 语义一致。
    # 不用 derive_unpack_pattern 的花括号 glob：那个写法只在单目录时有效，
    # 多目录时 startsWith("{a,b}") 永远不成立，会把外部文件全塞回包内。
    # 这里保留 original 集合（未剔除缺失项）来推导前缀——前缀是目录级的，
    # 与单个文件在不在无关。
    unpack_pattern, all_unpacked_entries = derive_unpack_pattern(ref_asar)
    unpack_prefixes = []
    for _p in sorted(all_unpacked_entries):
        _parts = _p.split("/")
        _pref = "/".join(_parts[:2]) if len(_parts) >= 2 else _parts[0]
        if _pref not in unpack_prefixes:
            unpack_prefixes.append(_pref)
    log("正在重新打包 app.asar ..."
        + (f"（外部目录模式：{unpack_pattern}）" if unpack_pattern else "（无外部目录）"))
    try:
        pack_any(npx, extract_dir, temp_asar, unpack_pattern, unpack_prefixes,
                 force_builtin=len(unpack_prefixes) > 1)
    except AsarToolError as e:
        cleanup_temp(resources, work)
        die(f"打包失败，已中止（原 app.asar 未被修改）。\n  {e}")
    if not os.path.isfile(temp_asar):
        cleanup_temp(resources, work)
        die("打包产物缺失，已中止。")

    # 6b. 结构守卫：新包必须包含参照清单的全部外部文件（缺文件=运行风险，零容忍）；
    #     多出的项视为向官方结构靠拢的修正，放行并提示
    try:
        new_unpacked = get_unpacked_set(temp_asar)
    except Exception as e:
        cleanup_temp(resources, work)
        die(f"打包产物头部解析失败（{e}），已中止。原文件未被修改。")
    missing = official_unpacked - new_unpacked
    if missing:
        cleanup_temp(resources, work)
        die("打包产物缺少官方外部文件，已中止（原文件未被修改）。\n"
            f"  缺失 {len(missing)} 项，示例：{sorted(missing)[:3]}\n"
            "  这通常意味着打包结构发生变化，请把此信息反馈给维护者。")
    extra = new_unpacked - official_unpacked
    if extra:
        log(f"外部文件清单修正 {len(extra)} 项（向官方结构对齐，更安全）", ok=True)

    # 7. 部署后复检：解包临时产物，验证标记与语法
    verify_dir = os.path.join(work, "v")
    extract_any(npx, temp_asar, verify_dir, quiet=True)
    marker_checks = [
        (os.path.join(verify_dir, "dist", "preload.js"), ENGINE_START),
        # 右键菜单块与引擎同在一个 preload.js，之前只查了引擎标记，
        # 万一右键块注入失败（函数名冲突等）复检发现不了，这里补上。
        (os.path.join(verify_dir, "dist", "preload.js"), CTX_START),
        (os.path.join(verify_dir, "dist", "menu.js"), MENU_START),
        (os.path.join(verify_dir, "dist", "tray.js"), TRAY_START),
    ]
    missing = [p for p, mk in marker_checks
               if not os.path.isfile(p) or mk not in open(p, encoding='utf-8', errors='replace').read()]
    if missing:
        cleanup_temp(resources, work)
        die(f"部署后复检失败（标记缺失：{missing}），已中止。原文件未被修改。")
    # preload.js 在校验清单里出现两次（引擎块 + 右键块），去重后再交给门禁，
    # 否则同一个文件会被 node --check / 结构自检 跑两遍，问题也会重复报。
    verify_files = sorted({p for p, _ in marker_checks})
    if NODE_OK:
        bad = node_check(verify_files)
        if bad:
            cleanup_temp(resources, work)
            die("打包产物语法校验失败，已中止。原文件未被修改。")
        log("部署后复检：注入标记与语法全部验证通过", ok=True)
    else:
        problems, warns = structure_check(verify_files)
        for w in warns:
            log("  " + w, warn=True)
        if problems:
            cleanup_temp(resources, work)
            die("打包产物结构自检失败，已中止。原文件未被修改。\n  " + "\n  ".join(problems))
        log("部署后复检：注入标记与结构全部通过（未装 Node.js，未做语法级校验）", warn=True)

    # 8. 原子替换（同卷 os.replace）
    #先做磁盘空间预检：备份 + 新包会同时存在，空间不足会中途失败留下垃圾
    try:
        need = os.path.getsize(temp_asar) + os.path.getsize(asar)
        free = shutil.disk_usage(resources).free
        if free < need + 50 * 1024 * 1024:
            cleanup_temp(resources, work)
            die(f"磁盘空间不足：本次汉化约需 {need // 1048576} MB 可用空间，"
                f"当前仅剩 {free // 1048576} MB。\n"
                "  请清理磁盘空间后重试。")
    except AttributeError:
        pass          # 极老的Python 没有 disk_usage，跳过检查
    except OSError:
        pass

    replaced = False
    try:
        # Windows：杀进程之后文件仍可能被 Defender / 搜索索引短暂占用，
        # 单次 os.replace 会直接把一次正常汉化判成"文件被占用"。
        # 重试 6 次（约 1.5 秒），真的占用才报错。macOS 不重试（权限问题重试无意义）。
        for _try in range(1 if (IS_MAC or IS_LINUX) else 6):
            try:
                os.replace(temp_asar, asar)
                replaced = True
                break
            except (PermissionError, OSError) as e:
                # 跨分区 temp_dir -> resources 时 os.replace 会抛 EXDEV (errno 18)
                import errno as _errno
                if isinstance(e, OSError) and getattr(e, "errno", None) == _errno.EXDEV:
                    if os.access(resources, os.W_OK):
                        local_tmp = os.path.join(resources, f"app.asar.agyzh.{os.getpid()}.tmp")
                        shutil.copy2(temp_asar, local_tmp)
                        os.replace(local_tmp, asar)
                        replaced = True
                        break
                    raise PermissionError(str(e))
                if IS_MAC or IS_LINUX or _try >= 5:
                    raise
                time.sleep(0.25)
    except PermissionError:
        # macOS：/Applications 权限不足时用管理员密码重试
        if IS_MAC:
            log("权限不足，正在弹出系统密码框以完成替换...", warn=True)
            if _mac_replace_with_admin(temp_asar, asar):
                replaced = True
            else:
                cleanup_temp(resources, work)
                die("替换 app.asar 时权限不足，且管理员授权未完成。\n"
                    "  请在「系统设置 → 隐私与安全性」中允许本工具访问"
                    "「应用程序」文件夹后重试。")
        elif IS_LINUX:
            log("安装目录需要 root 权限，正在通过 sudo/pkexec 完成替换...", warn=True)
            if _linux_replace_with_admin(temp_asar, asar):
                replaced = True
            else:
                cleanup_temp(resources, work)
                die("替换 app.asar 时权限不足，且 sudo/pkexec 提权未完成。\n"
                    "  请使用 sudo 运行本工具，或检查安装目录权限。")
        else:
            cleanup_temp(resources, work)
            die("替换 app.asar 时被拒绝（文件仍被占用）。"
                "请完全退出 Antigravity（含托盘）后重试。\n"
                "  若已退出仍报此错，通常是杀毒软件/搜索索引正在扫描该文件，等十几秒再试。")
    if not replaced:
        cleanup_temp(resources, work)
        die("替换 app.asar 失败，已中止（原文件未被修改）。")
    # 8a-2. 对话标题生成提示词汉化（对 resources/bin/language_server 做等字节替换）
    patch_language_server(resources, work, report)
    # unpacked 目录：内容与官方完全一致（我们只改 dist/），无需变动；
    # 打包产生的临时 unpacked 目录清理掉
    cleanup_temp(resources, work)
    log("部署完成：app.asar 已替换（原版备份保留在同目录 app.asar.bak）", ok=True)

    # 8b. macOS：重签（Windows 下自动跳过）
    signed = mac_post_deploy(resources, auto_yes=auto_yes)
    # 无论本次签名成功与否，都把「修复签名.command」刷新成当前版本。
    # 旧版（v1.14 及更早）留下的脚本不会写入稳定身份，用户以后运行它
    # 会把更新能力悄悄退回 cdhash 状态——必须覆盖掉。
    fix = _mac_write_fix_script(resources) if IS_MAC else None
    if IS_MAC and not signed:
        print()
        print("  [!] 汉化内容已成功写入，但代码签名重签未完成。")
        print("      在此状态下 Antigravity 可能提示「应用已损坏」而无法打开。")
        if fix:
            print("      已为你生成修复脚本，双击运行即可（会弹窗索要 Mac 密码）：")
            print(f"        {fix}")
            print("      运行完成后重新双击 Antigravity 就能正常打开。")
        else:
            print("      自动修复脚本生成失败，请查看 mac/README.md 的「打不开怎么办」一节。")
        if not auto_yes:
            input("\n  按回车键返回菜单...")

    # 9. 结果报告
    print("-" * 62)
    for kind, label, ok_ in report:
        mark = _fit_glyphs("✓") if ok_ else _fit_glyphs("△")
        print(_fit_glyphs(f"  {mark} [{kind}] {label}"))
    fails = [r for r in report if not r[2]]
    if fails:
        # 区分两类未命中，避免 macOS / Linux 用户把"平台本来就没有的弹窗"误当成故障
        win_only = [r for r in fails
                    if any(k in r[1] for k in ("WSL", "Windows", "windows filesystem"))]
        real = [r for r in fails if r not in win_only]
        if (IS_MAC or IS_LINUX) and win_only:
            plat_label = "Mac" if IS_MAC else "Linux"
            print(f"\n[·] {len(win_only)} 处为 Windows 专属弹窗（WSL 等），"
                  f"{plat_label} 版本就没有这些界面，属正常现象")
        if real:
            print(f"[!] 有 {len(real)} 处锚点未命中（官方版本变动所致，"
                  "对应位置保持英文，不影响其他功能）")
        if not real and not ((IS_MAC or IS_LINUX) and win_only):
            print("\n[!] 有锚点未命中，请查看上方明细")
    print("-" * 62)

    # 10. 启动
    if not auto_yes:
        # 若在 Linux 下当前 Antigravity 仍在运行（热替换模式），提示用户重启即可
        if IS_LINUX and _linux_running_pids(resources):
            print("\n[✓] 汉化包已热替换完成！由于 Antigravity 当前正在运行，请手动重启一次 Antigravity 即可生效。")
        elif input("是否立即启动 Antigravity？(Y/n)：").strip().lower() in ("", "y", "yes"):
            launch_app(resources)
    print("\n汉化完成！如界面有少量英文（AI 思考过程、模型名等属于刻意保留），"
          "可编辑 zh_data.json 增补词条后重新运行。")


def cleanup_temp(resources, work):
    # 清理本次 PID 的临时产物 + 任意历史残留（app.asar.agyzh.*.tmp / language_server.agyzh.*.tmp）
    for d in (resources, os.path.join(resources, "bin")):
        try:
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                # .tmp.part 是内置打包器的写入中间态：进程被强杀时会残留，
                # 不认它就会在 resources 里留一个几十 MB 的垃圾文件。
                if (name.startswith("app.asar.agyzh.") or name.startswith("language_server.agyzh.")) and (
                        name.endswith(".tmp") or name.endswith(".tmp.unpacked")
                        or name.endswith(".tmp.part")):
                    p = os.path.join(d, name)
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                    elif os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass
    shutil.rmtree(work, ignore_errors=True)


def cmd_restore(resources, auto_yes=False, no_kill=False):
    asar = os.path.join(resources, "app.asar")
    bak = asar + ".bak"
    ls_path = find_language_server(resources)
    ls_loc = ls_is_localized(ls_path)
    print("=" * 62)
    print("  恢复官方英文原版")
    print("=" * 62)
    # 关键：当前 asar 已是官方英文时必须直接返回。
    # 否则会拿旧备份覆盖新版本 —— 例如用户汉化过 2.31.2、官方自动升级到 2.33.0 后
    # 再点「恢复原版」，旧备份会把 2.31.2 的 asar 塞进 2.33.0 的安装目录，
    # 造成 asar 与 exe 版本错配，应用直接起不来。
    if not asar_is_localized(asar):
        if ls_loc:
            restore_language_server(resources)
        log("当前已是官方英文原版，无需恢复。", ok=True)
        if os.path.isfile(bak):
            log("（检测到旧备份，但它可能是更早的版本，已保留未动）", warn=True)
        return
    if not os.path.isfile(bak):
        die(f"未找到备份文件 {bak}，无法恢复。\n"
            "（当前 asar 已汉化且无备份。可重装 Antigravity 恢复英文。）")
    if asar_is_localized(bak):
        die("备份文件本身已被汉化污染（非官方原版），拒绝执行恢复以免损坏。\n"
            "请重装 Antigravity 获取官方原版。")
    if asar_is_localized(asar):
        kill_processes(auto=auto_yes, resources_dir=resources, no_kill=no_kill)
    tmp = asar + ".restore.tmp"
    try:
        shutil.copy2(bak, tmp)
        os.replace(tmp, asar)
    except PermissionError:
        if IS_LINUX and _linux_replace_with_admin(bak, asar):
            pass
        elif IS_MAC and _mac_replace_with_admin(bak, asar):
            pass
        else:
            raise
    restore_language_server(resources)
    log("已恢复官方英文原版", ok=True)

    # macOS：还原 asar 同样破坏了签名，必须重签，否则还原后反而打不开
    if IS_MAC:
        if _codesign_available():
            mac_post_deploy(resources, auto_yes=auto_yes)
            # 与汉化流程一致：顺手把修复脚本刷新到当前版本，
            # 避免旧版脚本残留（它不会写稳定身份）
            _mac_write_fix_script(resources)
        else:
            log("未找到 codesign，无法自动重签（还原后软件可能提示「已损坏」）", warn=True)
            log("  解决：安装 Xcode 命令行工具（xcode-select --install），"
                "再运行一次本还原流程。", warn=True)

    if not auto_yes:
        if input("是否立即启动 Antigravity？(Y/n)：").strip().lower() in ("", "y", "yes"):
            launch_app(resources)


def cmd_status(resources):
    asar = os.path.join(resources, "app.asar")
    bak = asar + ".bak"
    print("=" * 62)
    print("  状态检查")
    print("=" * 62)
    log(f"安装目录：{resources}")
    if not os.path.isfile(asar):
        die("未找到 app.asar")
    loc = asar_is_localized(asar)
    log(f"当前状态：{'已汉化' if loc else '官方英文原版'}", ok=True)
    if os.path.isfile(bak):
        bak_loc = asar_is_localized(bak)
        log(f"原版备份：存在（{'正常' if not bak_loc else '警告：已被污染'}）", ok=not bak_loc)
    else:
        log("原版备份：不存在" + ("（已汉化状态无备份，无法还原英文！）" if loc else ""), warn=loc)
    try:
        log(f"app.asar 大小：{os.path.getsize(asar) / 1048576:.2f} MB")
    except OSError:
        pass
    ls_path = find_language_server(resources)
    if ls_path and not IS_MAC:
        ls_loc = ls_is_localized(ls_path)
        log(f"对话标题生成：{'已中文化（language_server 已补丁）' if ls_loc else '官方英文标题'}", ok=ls_loc)
    # macOS 追加：签名健康度（这是 Mac 上最常见的故障点）
    mac_status_extra(resources)


RULE_NAME = "chinese-replies.md"
RULE_BODY = """# 全局规则：始终使用简体中文回复

- 无论用户使用什么语言提问，你的**最终回复**必须使用简体中文。
- 思考过程（Thinking）与内部推理可以用英文，但展示给用户的正文、总结、
  计划、步骤说明一律使用简体中文。
- 代码、命令、文件路径、错误信息原文、专有名词（如 Gemini、MCP、Git）保持原文，
  不强行翻译；代码注释遵循用户现有注释语言。
- 与用户交流时语言自然简洁，不逐字直译。
"""

def rules_dir():
    base = os.path.join(os.path.expanduser("~"), ".gemini", "config", "rules")
    return base

def cmd_chinese_output(auto_yes=False):
    """写入/移除"AI 用中文回复"的全局规则（输出汉化的正解：让模型自己说中文）"""
    rd = rules_dir()
    path = os.path.join(rd, RULE_NAME)
    print("=" * 62)
    print("  AI 回复中文化（全局规则）")
    print("=" * 62)
    if os.path.isfile(path):
        print(_fit_glyphs("[✓] 当前状态：已启用（规则文件存在）"))
        print("-" * 62)
        print(RULE_BODY)
        ans = "y" if auto_yes else input("输入 1=保留并刷新 / 2=移除该规则 / 回车=取消：").strip()
        if ans == "2":
            os.remove(path)
            log("已移除规则：AI 将恢复默认语言行为", ok=True)
            return
        if ans not in ("1", "y"):
            print("已取消。")
            return
    else:
        print("说明：界面汉化管不了 AI 生成的回复内容——正确的做法是加一条全局规则，")
        print("让模型自己用中文回复（思考过程仍保留英文，不影响推理质量）。")
        print("-" * 62)
        print("将写入以下规则到：" + path)
        print(RULE_BODY)
        ans = "y" if auto_yes else input("是否写入？(Y/n)：").strip().lower()
        if ans not in ("", "y", "yes"):
            print("已取消。")
            return
    os.makedirs(rd, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding='utf-8') as f:
        f.write(RULE_BODY)
    os.replace(tmp, path)
    log(f"规则已写入：{path}", ok=True)
    print("提示：规则对新对话生效；已在进行的对话可能需要新开一轮才受影响。")


# ----------------------------------------------------------------------------
# 检查更新
# ----------------------------------------------------------------------------
# 设计边界（改这里前请先读）：
#   1. 全程序唯一的联网入口。只有用户主动点「检查更新」才会发请求，
#      汉化 / 还原 / 状态 / AI 中文化 全程零网络行为，这是本工具的核心承诺。
#   2. 只读取 Release 元数据，不下载执行任何远端代码；
#      下载也只落一个 zip 到桌面，绝不自动替换自身文件。
#   3. 任何网络异常都必须静默降级成一句话，绝不能甩堆栈、绝不能卡住主流程。
def _safe_text(s):
    """远端文本可能带 emoji，GBK 终端直接打印会崩；这里按终端能力降级。"""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return s.encode(enc, "replace").decode(enc, "replace")
    except (UnicodeEncodeError, LookupError, UnicodeDecodeError):
        return "".join(ch if ord(ch) < 128 else "?" for ch in s)


def _parse_ver(tag):
    """'v1.9.2' / '1.10.0-rc1' -> (1, 9, 2)；解析不了返回 None。"""
    s = (tag or "").strip().lstrip("vV")
    if not s:
        return None
    out = []
    for seg in s.split("."):
        m = re.match(r"^\d+", seg)
        if not m:
            return None
        out.append(int(m.group(0)))
    return tuple(out) if out else None


def _ver_cmp(a, b):
    """逐位比数字段，位数不同补 0。必须这么比：字符串比会让 1.10 < 1.9。"""
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def _brief_err(e):
    """把网络异常压成一句人话"""
    if isinstance(e, urllib.error.HTTPError):
        code = getattr(e, "code", "?")
        if code == 403:
            return "GitHub 接口限流了（同一网络一小时最多 60 次）"
        return f"GitHub 返回 {code}"
    if isinstance(e, urllib.error.URLError):
        return f"连不上 GitHub（{e.reason}）"
    low = str(e).lower()
    if "timed out" in low or "timeout" in low:
        return "连接超时"
    return type(e).__name__


def _http_get(url, timeout, binary=False):
    """发一个 GET，不带任何本机信息（只有 UA，用于满足 GitHub 的接口要求）"""
    req = urllib.request.Request(url, headers={
        "User-Agent": f"Antigravity-YiJianHanHua/{TOOL_VERSION}",
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw if binary else json.loads(raw.decode("utf-8", "replace"))


def _pick_zip(assets):
    """按当前系统挑对应的压缩包（包命名：...-windows-vX.Y.zip / ...-mac-vX.Y.zip / ...-linux-vX.Y.*）"""
    key = "mac" if IS_MAC else ("windows" if IS_WIN else "linux")
    for a in assets or []:
        name = (a.get("name") or "").lower()
        if (name.endswith(".zip") or name.endswith(".tar.gz")) and key in name and a.get("browser_download_url"):
            return a
    return None


def _pick_sums(assets):
    for a in assets or []:
        if (a.get("name") or "").strip().lower() == "sha256sums.txt" and a.get("browser_download_url"):
            return a
    return None


def _desktop_dir():
    """找桌面目录，找不到返回 None（调用方回退到脚本目录）"""
    home = os.path.expanduser("~")
    cands = []
    if IS_LINUX:
        try:
            r = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                cands.append(r.stdout.strip())
        except Exception:
            pass
        cands.append(os.path.join(home, "桌面"))
    cands.append(os.path.join(home, "Desktop"))
    if IS_WIN:
        cands.append(os.path.join(os.environ.get("USERPROFILE", home), "Desktop"))
        cands.append(os.path.join(home, "OneDrive", "Desktop"))
    for p in cands:
        if os.path.isdir(p):
            return p
    return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _discard(tmp):
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except OSError:
        pass


def _unique_path(path):
    """目标已存在就自动改名，绝不静默覆盖用户已有的文件"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(1, 100):
        cand = f"{base}({i}){ext}"
        if not os.path.exists(cand):
            return cand
    return path


def _download_to(url, dest, expect_size=0):
    """分块下载 + 进度显示；失败清掉半成品。返回 (ok, err)"""
    tmp = dest + ".part"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"Antigravity-YiJianHanHua/{TOOL_VERSION}",
        })
        got = 0
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(tmp, "wb") as f:
            while True:
                chunk = resp.read(DL_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if expect_size:
                    pct = min(100, got * 100 // expect_size)
                    print(f"\r  下载中 {pct}%（{got // 1024} KB / {expect_size // 1024} KB）   ",
                          end="", flush=True)
                else:
                    print(f"\r  下载中 {got // 1024} KB   ", end="", flush=True)
        print("", flush=True)
        os.replace(tmp, dest)
        return True, None
    except KeyboardInterrupt:
        # 用户中途按 Ctrl+C：必须清掉半成品，否则下次会留一个 .part 垃圾文件
        print("", flush=True)
        _discard(tmp)
        return False, "已取消"
    except Exception as e:
        _discard(tmp)
        return False, _brief_err(e)


def cmd_check_update(auto_yes=False):
    """检查 GitHub 上的最新版本；只提示 / 下载到桌面，绝不自动替换自身文件。"""
    print("=" * 62)
    print("  检查更新")
    print("=" * 62)
    print(f"  当前版本：{TOOL_VERSION}")
    print(f"  正在查询 GitHub Release（最多等 {UPDATE_TIMEOUT} 秒）...")
    try:
        data = _http_get(UPDATE_API, UPDATE_TIMEOUT)
    except Exception as e:
        log(f"暂时没查到：{_brief_err(e)}", warn=True)
        print("-" * 62)
        print("  这不影响汉化功能。你也可以自己打开这里看最新版：")
        print(f"  {UPDATE_PAGE}")
        return

    tag = _safe_text(data.get("tag_name") or "")
    page = data.get("html_url") or UPDATE_PAGE
    cur = _parse_ver(TOOL_VERSION)
    new = _parse_ver(tag)
    if cur is None or new is None:
        log(f"版本号无法自动比较（本地 {TOOL_VERSION} / 远端 {tag or '空'}）", warn=True)
        print(f"  发布页：{page}")
        return
    if _ver_cmp(new, cur) <= 0:
        log(f"已是最新版本（{TOOL_VERSION}）", ok=True)
        return

    assets = data.get("assets") or []
    zip_asset = _pick_zip(assets)
    print("-" * 62)
    log(f"发现新版本：{tag}（当前 {TOOL_VERSION}）", ok=True)
    pub = (data.get("published_at") or "")[:10]
    if pub:
        print(f"  发布时间：{pub}")
    if zip_asset:
        print(f"  适用包：{_safe_text(zip_asset['name'])}"
              f"（{(zip_asset.get('size') or 0) / 1048576:.2f} MB）")
    print("-" * 62)

    body = (data.get("body") or "").strip()
    if body:
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        print("  更新说明（摘要）：")
        for ln in lines[:12]:
            t = _safe_text(ln)
            print("    " + (t[:70] + "..." if len(t) > 70 else t))
        if len(lines) > 12:
            print(f"    ...（共 {len(lines)} 行，完整内容见发布页）")
    print("-" * 62)
    print("  本工具不会自动替换自己的文件 —— 下载后由你决定怎么换。")

    if not zip_asset:
        print("  没找到适用于当前系统的安装包，请到发布页手动选择：")
        print(f"  {page}")
        return

    print("  [1] 下载到桌面（下载完你自己解压替换）")
    print("  [2] 只要下载链接（自己用浏览器或下载工具下）")
    print("  [0] 取消")
    if auto_yes:
        ans = "1"
    else:
        try:
            ans = input("请选择：").strip()
        except (EOFError, KeyboardInterrupt):
            return
    if ans == "0":
        print("已取消。")
        return
    if ans == "2":
        print(f"  下载链接：{zip_asset['browser_download_url']}")
        print(f"  发布页：{page}")
        return
    if ans != "1":
        print("  输入无效，已取消。")
        return

    dest_dir = _desktop_dir() or SCRIPT_DIR
    dest = _unique_path(os.path.join(dest_dir, zip_asset["name"]))
    if os.path.exists(os.path.join(dest_dir, zip_asset["name"])):
        print("  桌面上已经有同名文件了，这次存成另一个名字，不会覆盖你原来的：")
    print(f"  下载到：{dest}")
    ok, err = _download_to(zip_asset["browser_download_url"], dest, zip_asset.get("size") or 0)
    if not ok:
        log(f"下载失败：{err}", warn=True)
        print("  下不动的时候建议自己去浏览器下载：")
        print(f"  {zip_asset['browser_download_url']}")
        return
    log("下载完成", ok=True)

    digest = _sha256_file(dest)
    print(f"  文件 SHA256：{digest}")
    sums = _pick_sums(assets)
    if sums:
        try:
            txt = _http_get(sums["browser_download_url"], UPDATE_TIMEOUT, binary=True)
            expect = None
            for ln in txt.decode("utf-8", "replace").splitlines():
                parts = ln.split()
                if len(parts) >= 2 and os.path.basename(parts[-1]) == zip_asset["name"]:
                    expect = parts[0].strip().lower()
                    break
            if expect is None:
                log("校验文件里没有本包的记录，跳过校验", warn=True)
            elif expect == digest:
                log("SHA256 校验通过", ok=True)
            else:
                log("SHA256 校验不通过！文件可能不完整或被掉包，请勿使用", warn=True)
                print(f"  期望：{expect}")
                print(f"  实际：{digest}")
                return
        except Exception as e:
            log(f"校验文件下载失败（{_brief_err(e)}），跳过校验", warn=True)
    else:
        log("该 Release 未附带 SHA256SUMS.txt，跳过校验", warn=True)

    print("-" * 62)
    print(f"  已保存到：{dest}")
    print("  下一步：先退出本程序，再解压这个 zip，把里面的文件覆盖到你现在的目录，")
    print("  然后重新运行。正在运行时替换文件会让当前窗口读错，顺序别反。")


# ----------------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------------
def _say_goodbye():
    """明确告诉用户这是正常结束，不是崩溃。

    网友反馈里最常见的误判就是"按了个键，窗口就没了=闪退"。窗口确实会在按键后
    关闭（Windows 的 pause 就是这么设计的），但必须让人看懂这是正常收尾。
    bat 那边因为编码限制只能写英文，所以这句中文在这里补上。
    """
    launcher = "一键汉化.command" if IS_MAC else ("一键汉化.bat" if IS_WIN else "一键汉化.sh")
    print("\n  已退出。窗口会在你按任意键后关闭——这是正常结束，不是闪退。")
    print(f"  想再操作一次，重新运行 {launcher} 即可。")


def menu_pause():
    """操作结束后停一下让用户看结果；回车回主菜单，输入 0 直接退出本程序。

    注意：这里以前只把输入丢掉、不判断内容，导致提示写着"输入 0 可退出"但输 0 照样
    回主菜单，用户以为程序卡住了。现在真按提示执行。

    另外，Ctrl+C / 输入流断开时**不能静默返回 0**：批处理看到 0 会当成"全部成功"
    直接关窗，用户什么都没看清就闪退。这里统一用 130 表示"用户主动中断"，
    由 .bat 单独识别并给出说明。
    """
    try:
        ans = input("\n  按回车返回主菜单（输入 0 直接退出）...").strip()
    except (EOFError, KeyboardInterrupt):
        # 这里操作本身已经做完了，只是"回菜单"这一步被打断，说"已退出"即可，
        # 不要吓唬成"中断"，免得用户以为刚才的汉化没成功。
        print("\n  已退出。")
        raise SystemExit(EXIT_CANCELLED)
    if ans == "0":
        _say_goodbye()
        raise SystemExit(0)


def main():
    args = [a for a in sys.argv[1:]]
    auto_yes = "--yes" in args or "/y" in args
    no_kill = "--no-kill" in args
    if no_kill and not IS_LINUX:
        # 这个参数依赖 POSIX 的 os.replace() 原子性：进程还开着也能换掉 app.asar。
        # Windows 上文件被占用时替换必然失败，macOS 上签名会失效，都不能免关进程。
        # 与其让用户撞上"文件被占用"的报错，不如在这里明确忽略并说明原因。
        log("--no-kill 仅在 Linux 上生效（Windows/macOS 必须先关闭应用才能替换 app.asar），本次已忽略该参数", warn=True)
        no_kill = False
    args = [a for a in args if a not in ("--yes", "/y", "--no-kill")]
    path_override = None
    if "--path" in args:
        i = args.index("--path")
        path_override = args[i + 1] if i + 1 < len(args) else None
        args = [a for a in args if a != "--path" and a != path_override]

    cmd = args[0] if args else ""

    # 检查更新不依赖 Antigravity 是否安装：没装软件的人也要能查新版本
    if cmd in ("update", "check-update", "u", "5"):
        cmd_check_update(auto_yes)
        return

    resources = find_resources(path_override)

    if cmd in ("apply", "a", "1"):
        npx = check_env()
        lock = acquire_lock()
        try:
            cmd_apply(resources, npx, auto_yes, no_kill=no_kill)
        finally:
            release_lock(lock)
    elif cmd in ("restore", "r", "2"):
        lock = acquire_lock()
        try:
            cmd_restore(resources, auto_yes, no_kill=no_kill)
        finally:
            release_lock(lock)
    elif cmd in ("status", "s", "3"):
        cmd_status(resources)
    elif cmd in ("chinese-output", "chinese", "cn-output", "4"):
        cmd_chinese_output(auto_yes)
    elif cmd == "":
        while True:
            plat = "macOS" if IS_MAC else ("Windows" if IS_WIN else "Linux")
            print("=" * 62)
            print(f"  Antigravity 一键汉化 {TOOL_VERSION}（当前系统：{plat}）")
            print("=" * 62)
            print("  [1] 一键汉化（官方更新后重新运行即可）")
            print("  [2] 恢复官方英文原版")
            print("  [3] 查看状态")
            print("  [4] AI 回复中文化（让 AI 用中文回答，可开可关）")
            print("  [5] 检查更新（查 GitHub 上有没有新版本）")
            print("  [0] 退出")
            print("  （每个操作做完会回到本菜单，可以接着做下一件事）")
            if IS_MAC:
                print("  提示：Mac 上如提示「应用已损坏」，选3 查看状态里的签名项")
            try:
                c = input("请选择：").strip()
            except (EOFError, KeyboardInterrupt):
                # 不能直接 return：退出码 0 会让 .bat 判定"全部成功"立刻关窗，
                # 用户看到的就是"随便按一下窗口就没了"。统一按中断处理。
                print("\n  已中断（Ctrl+C 或输入已关闭）。软件没有被改动。")
                raise SystemExit(EXIT_CANCELLED)
            if c == "1":
                npx = check_env()
                lock = acquire_lock()
                try:
                    cmd_apply(resources, npx, auto_yes=False)
                finally:
                    release_lock(lock)
                menu_pause()
                continue
            if c == "2":
                lock = acquire_lock()
                try:
                    cmd_restore(resources)
                finally:
                    release_lock(lock)
                menu_pause()
                continue
            if c == "3":
                cmd_status(resources)
                menu_pause()
                continue
            if c == "4":
                cmd_chinese_output()
                menu_pause()
                continue
            if c == "5":
                cmd_check_update()
                menu_pause()
                continue
            if c == "0":
                _say_goodbye()
                return
            print("\n  输入无效，请重新选择（1 / 2 / 3 / 4 / 5 / 0）。")
    else:
        print(__doc__.replace("__VERSION__", TOOL_VERSION))


if __name__ == "__main__":
    # 统一兜底：用户在哪一步按 Ctrl+C、或出现没预料到的异常，
    # 都不许把 Python traceback 甩到用户脸上（那是"闪退/坏了"观感的最大来源）。
    # 一律翻译成一句人话 + 一份失败日志 + 明确的退出码。
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n  已中断（Ctrl+C 或输入已关闭）。软件没有被改动。")
        sys.exit(EXIT_CANCELLED)
    except Exception as e:
        # 把最常见的 Windows 文件错误翻译成人话。技术细节仍然完整写进日志，
        # 但屏幕上不能甩 "WinError 32" 这种术语——网友看不懂，只会觉得程序坏了。
        winerr = getattr(e, "winerror", None)
        human = None
        if winerr == 32:
            human = ("文件正被其他程序占用（最常见：Antigravity 没完全退出，或杀毒软件正在扫描）。\n"
                     "  请彻底退出 Antigravity（包括右下角托盘图标）后重新运行本程序；\n"
                     "  若装了杀毒软件，等十几秒再试一次。\n"
                     "  本次没有改动你的 Antigravity，可以放心重试。")
        elif winerr == 5:
            human = ("没有权限修改 Antigravity 的安装目录。\n"
                     "  请右键本程序 → 选择「以管理员身份运行」，然后重试。\n"
                     "  本次没有改动你的 Antigravity，可以放心重试。")
        msg = human or f"程序异常终止：{type(e).__name__}: {e}"
        print(_fit_glyphs(f"[✗] {msg}"))
        _write_fail_log(traceback.format_exc())
        sys.exit(1)
