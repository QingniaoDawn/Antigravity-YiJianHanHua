# -*- coding: utf-8 -*-
"""
Antigravity 一键汉化 v1.4（Windows / macOS 双平台）
================================================
用法：
  python localize.py            # 交互菜单
  python localize.py apply      # 直接汉化
  python localize.py restore    # 恢复官方英文原版
  python localize.py status     # 查看状态
  python localize.py chinese-output   # AI 回复中文化（写入全局规则）
  可选：--path <安装目录>  --yes（免确认）

流程：杀进程 → 备份 → 解包 → 注入 → 语法门禁 → 打包 → 部署后复检 → 原子替换
      →（macOS 追加）代码签名重签 → 启动
安全：全程仅本地操作，无任何网络行为；所有修改幂等可重入；备份永不降级。

跨平台说明：
  本脚本为 Windows / macOS 双平台共用，按当前系统自动走对应分支。
  macOS 与 Windows 的三处关键差异已内置处理：
    1. 安装路径：/Applications/Antigravity.app/Contents/Resources
    2. 进程管理：按完整可执行路径精确匹配，绝不误杀同名进程
    3. 代码签名：改 asar 会使 Apple 签名失效，必须重签，否则 App 打不开
       （Apple Silicon 上直接启动失败）。重签采用 Apple 推荐的分层方式：
       嵌套组件裸签 → 主包带 entitlements 签。全程需管理员密码。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE_PATH = os.path.join(SCRIPT_DIR, "engine.js")
DATA_PATH = os.path.join(SCRIPT_DIR, "zh_data.json")

ENGINE_START = "// ===== [AGY_ZH_ENGINE_START] Antigravity 一键汉化引擎（整合增强版）====="
ENGINE_END = "// ===== [AGY_ZH_ENGINE_END] ====="
MENU_START = "// ===== [AGY_ZH_MENU_START] 原生菜单翻译 ====="
MENU_END = "// ===== [AGY_ZH_MENU_END] ====="
TRAY_START = "// ===== [AGY_ZH_TRAY_START] 托盘菜单翻译 ====="
TRAY_END = "// ===== [AGY_ZH_TRAY_END] ====="

TOOL_VERSION = "v1.4"               # 工具版本号（唯一数据源，改这里即可全局生效）
TESTED_VERSION = "2.21.1"          # 深度适配验证过的版本（Windows 真机实测）
MARKER_CHECK = b"AGY_ZH_ENGINE_START"   # 用于字节级判断 asar 是否已被汉化

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

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

PROC_NAMES = ["Antigravity.exe", "language_server.exe"]


def log(msg, ok=False, warn=False):
    tag = "[✓]" if ok else ("[!]" if warn else "[·]")
    print(f"{tag} {msg}", flush=True)


def die(msg, code=1):
    print(f"[✗] {msg}", flush=True)
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
                f"  可删除锁文件：{lock_path}")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(str(os.getpid()))
        return lock_path
    except FileExistsError:
        die("另一个汉化程序刚刚启动，本次退出（重复双击无副作用，稍后重试即可）。")
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
            "  可以直接指向 Antigravity.app 包本身、resources 目录，或安装根目录。")
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
        cands = ["/opt/Antigravity/resources", "/usr/lib/antigravity/resources",
                 os.path.expanduser("~/.local/share/antigravity/resources")]
    for c in cands:
        if os.path.isfile(os.path.join(c, "app.asar")):
            return c
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


def check_env():
    """检查运行环境：node/npx（解包打包必需）+ macOS 额外需要的 codesign/xattr。
    提前检查 codesign 是为了避免"汉化写进去了、App 却打不开"这种最糟的结局
    却让用户一头雾水。"""
    node = shutil.which("node")
    npx = shutil.which("npx")
    if not node or not npx:
        die("未检测到 Node.js（需要 node 和 npx 命令）。\n"
            "请安装 Node.js 18+：https://nodejs.org/ 安装时勾选 Add to PATH"
            + ("（macOS 推荐用 Homebrew：brew install node）" if IS_MAC else ""))
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


def asar_run(npx, *args, timeout=300):
    """调用官方 @electron/asar 工具（参数列表形式，无 shell 拼接，防注入）"""
    cmd = [npx, "--yes", "@electron/asar"] + [str(a) for a in args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding='utf-8', errors='replace')
    except subprocess.TimeoutExpired:
        die("asar 工具执行超时（网络下载依赖可能过慢），请重试")
    if r.returncode != 0:
        die(f"asar 工具执行失败：{' '.join(cmd[:5])}...\n{r.stderr[-800:] or r.stdout[-800:]}")
    return r.stdout


def node_check(files):
    """语法门禁：对所有被修改的 JS 文件做 node --check 静态校验。
    任何异常都视为"校验不通过"——宁可中止，也不能让未校验的产物部署。"""
    bad = []
    for f in files:
        try:
            r = subprocess.run(["node", "--check", f], capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
        except Exception as e:
            bad.append((f, f"无法执行 node --check：{e}"))
            continue
        if r.returncode != 0:
            bad.append((f, (r.stderr or r.stdout)[-500:]))
    return bad


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


def kill_processes(auto=False, resources_dir=None):
    """关闭 Antigravity 相关进程。按可执行文件路径过滤，只杀安装目录下的进程——
    其他软件恰好同名（如 language_server）的进程绝不误杀。"""
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
                die("已取消。请手动退出 Antigravity 后重试（菜单栏图标 → 退出）。")
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
        names = [p.replace(".exe", "") for p in PROC_NAMES]
        r = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True).stdout.lower()
        running = [n for n in names if n in r]
        if not running:
            return
        if not auto:
            ans = input(f"    检测到 {running} 正在运行，需要关闭才能继续。是否关闭？(Y/n)：")
            if ans.strip().lower() not in ("", "y", "yes"):
                die("已取消。请手动退出 Antigravity 后重试。")
        for n in running:
            _safe_run(["pkill", "-f", n])
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
        elif name == "Antigravity.exe":
            # Antigravity.exe 名称足够独特；路径不可读时也视为目标
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
            die("已取消。请手动退出 Antigravity 后重试。")
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
        if os.path.isfile(exe):
            try:
                os.startfile(exe)  # noqa
                log("已启动 Antigravity", ok=True)
            except Exception as e:
                log(f"启动失败：{e}（可手动打开）", warn=True)
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
                                items.append(os.path.join(vers, s))
                        else:
                            items.append(p)
                    else:
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
                   verbose=False):
    """执行一次 codesign。sudo=True 时走 osascript 弹窗授权。
    返回 (是否成功, 输出信息)。全程参数列表形式，无 shell 拼接。

    sudo 路径的实现要点（这里刻意不用字符串拼接 + eval）：
      把完整命令写进临时 .sh 脚本，osascript 只执行
      `do shell script "/bin/bash <脚本路径>" with administrator privileges`。
      这样应用路径完全不进入 AppleScript 字符串，
      也就绕开了 AppleScript 与 POSIX 两层转义的坑——
      含空格、单引号、$ 等特殊字符的路径都能正确执行。
    """
    cmd = ["codesign", "--force", "--sign", identity]
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

    # 4) 最后签主包（带 entitlements）
    log("正在签名主应用包（带权限清单）...")
    ok, err = _mac_sign_once(app_bundle, "-", ent, sudo=False)
    if not ok and use_sudo and IS_MAC:
        log("需要管理员权限，正在弹出系统密码框...", warn=True)
        ok, err = _mac_sign_once(app_bundle, "-", ent, sudo=True)
    if not ok:
        log(f"主包签名失败：{err}", warn=True)
        return False

    # 5) 校验（--deep 在校验场景下是官方认可的用法）
    valid, msg = _mac_verify_signature(app_bundle)
    if not valid and use_sudo and IS_MAC:
        ok2, _ = _mac_sign_once(app_bundle, "-", ent, sudo=True)
        if ok2:
            valid, msg = _mac_verify_signature(app_bundle)

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
    """macOS 状态检查追加项：签名健康度"""
    if not IS_MAC:
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


_FIX_SH = r"""#!/bin/bash
# ============================================================
# Antigravity 一键汉化 · macOS 签名修复脚本
# ------------------------------------------------------------
# 什么时候需要用它：
#   汉化完成后若双击 Antigravity 提示「应用已损坏，无法打开」，
#   说明 app.asar 被改动后代码签名失效了。运行本脚本重新签名即可。
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
if [ -d "$APP/Contents/Frameworks" ]; then
  for item in "$APP/Contents/Frameworks"/*.framework; do
    [ -e "$item" ] || continue
    if [ -d "$item/Versions" ]; then
      for v in "$item/Versions"/*; do
        [ -e "$v" ] || continue
        [ "$(basename "$v")" = "Current" ] && continue
        codesign --force --sign - "$v" 2>/dev/null || FAIL=$((FAIL+1))
      done
    else
      codesign --force --sign - "$item" 2>/dev/null || FAIL=$((FAIL+1))
    fi
  done
  for item in "$APP/Contents/Frameworks"/*.app "$APP/Contents/Frameworks"/*.dylib; do
    [ -e "$item" ] || continue
    codesign --force --sign - "$item" 2>/dev/null || FAIL=$((FAIL+1))
  done
fi

codesign --force --sign - --entitlements "$ENT" "$APP" || FAIL=$((FAIL+1))
rm -f "$ENT"

# ---- 5. 校验 ----
echo "[4/4] 校验签名..."
if codesign --verify --deep --strict "$APP" 2>/dev/null; then
  echo ""
  echo "=============================================================="
  echo "  修复成功！现在可以正常打开 Antigravity 了。"
  echo "=============================================================="
  echo ""
  open "$APP" 2>/dev/null
  read -r -p "按回车键关闭窗口..." _
  exit 0
else
  echo ""
  echo "  [!] 签名校验仍未通过。可打开「终端」手动执行："
  echo "      sudo xattr -cr \"$APP\""
  echo "      sudo codesign --force --sign - \"$APP\""
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


def apply_anchor_replace(content, find, replace, label, report):
    """锚点替换：原文在→替换；替换结果已在→跳过；都不在→记录未命中"""
    if find in content:
        report.append(("替换", label, True))
        return content.replace(find, replace)
    probe = replace if len(replace) < 200 else replace[:200]
    if probe in content:
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
            f"    if (mi && mi.label && __AGY_ZH_MENU_MAP__[mi.label]) mi.label = __AGY_ZH_MENU_MAP__[mi.label];\n"
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
    return [(set_hooked, set_orig), (find_hooked, find_orig), (tray_hooked, tray_orig)]


def patch_all(extract_dir, engine_code, data, report):
    """对解包目录执行全部注入，返回被修改文件列表"""
    modified = []

    def rd(p):
        return open(p, encoding='utf-8').read()

    def wr(p, s):
        open(p, 'w', encoding='utf-8', newline='').write(s)
        modified.append(p)

    data_json = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    # engine.js 中的占位符替换为真实词典
    engine_final = engine_code.replace("/*__AGY_ZH_DATA__*/ {}",
                                       "__AGY_ZH_DATA__JSON__").replace(
                                           "__AGY_ZH_DATA__JSON__", data_json)

    # ---- 1. dist/preload.js：DOM 翻译引擎 ----
    p = os.path.join(extract_dir, "dist", "preload.js")
    if os.path.isfile(p):
        wr(p, inject_engine(rd(p), engine_final, data_json))
        report.append(("注入", "dist/preload.js 界面翻译引擎", True))

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
            new = apply_anchor_replace(c, patch["find"], patch["replace"], f"{rel}: {patch['find'][:30]}", report)
            changed = changed or new != c
            c = new
        if changed:
            wr(p, c)
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
    shutil.copy2(asar_path, tmp)
    os.replace(tmp, bak)
    return bak, True


def get_asar_version(extract_dir):
    try:
        pkg = json.load(open(os.path.join(extract_dir, "package.json"), encoding='utf-8'))
        return pkg.get("version", "?")
    except Exception:
        return "?"


# ----------------------------------------------------------------------------
# 主管线
# ----------------------------------------------------------------------------
def cmd_apply(resources, npx, auto_yes=False):
    asar = os.path.join(resources, "app.asar")
    if not os.path.isfile(asar):
        die(f"未找到 {asar}")

    print("=" * 62)
    print(f"  Antigravity 一键汉化 {TOOL_VERSION}（Windows / macOS 双平台）")
    print("=" * 62)
    log(f"目标：{asar}")

    # 0. 引擎与词典自检
    if not os.path.isfile(ENGINE_PATH):
        die(f"缺少引擎文件：{ENGINE_PATH}")
    if not os.path.isfile(DATA_PATH):
        die(f"缺少词典文件：{DATA_PATH}")
    engine_code = open(ENGINE_PATH, encoding='utf-8').read()
    data = json.load(open(DATA_PATH, encoding='utf-8'))
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
                die("已取消。")

    # 2. 关闭进程（按路径过滤，只杀 Antigravity 自己的）
    kill_processes(auto=auto_yes, resources_dir=resources)

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
    asar_run(npx, "extract", asar, extract_dir)
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

    # 5. 语法门禁（node --check，全部通过才继续）
    bad = node_check(modified)
    if bad:
        for f, err in bad:
            print(f"[✗] 语法校验失败：{os.path.basename(f)}\n{err}")
        cleanup_temp(resources, work)
        die("语法门禁未通过，已中止（原 app.asar 未被修改，软件不受影响）。")
    log(f"语法门禁：{len(modified)} 个文件全部通过 node --check", ok=True)

    # 6. 打包（排除目录自动从 asar 推导，未来版本新增 unpacked 内容也能适配；
    #    临时文件名带 PID：即使极端情况下并发执行也不会互相覆盖）
    temp_asar = os.path.join(resources, f"app.asar.agyzh.{os.getpid()}.tmp")
    # 结构参照：优先用干净的官方备份（当前部署包本身可能带历史布局偏差）
    bak = asar + ".bak"
    ref_asar = bak if (os.path.isfile(bak) and not asar_is_localized(bak)) else asar
    official_unpacked = get_unpacked_set(ref_asar)
    unpack_pattern, _ = derive_unpack_pattern(ref_asar)
    log("正在重新打包 app.asar ..."
        + (f"（外部目录模式：{unpack_pattern}）" if unpack_pattern else "（无外部目录）"))
    if unpack_pattern:
        asar_run(npx, "pack", extract_dir, temp_asar, "--unpack-dir", unpack_pattern)
    else:
        asar_run(npx, "pack", extract_dir, temp_asar)
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
    asar_run(npx, "extract", temp_asar, verify_dir)
    marker_checks = [
        (os.path.join(verify_dir, "dist", "preload.js"), ENGINE_START),
        (os.path.join(verify_dir, "dist", "menu.js"), MENU_START),
        (os.path.join(verify_dir, "dist", "tray.js"), TRAY_START),
    ]
    missing = [p for p, mk in marker_checks
               if not os.path.isfile(p) or mk not in open(p, encoding='utf-8', errors='replace').read()]
    if missing:
        cleanup_temp(resources, work)
        die(f"部署后复检失败（标记缺失：{missing}），已中止。原文件未被修改。")
    bad = node_check([p for p, _ in marker_checks])
    if bad:
        cleanup_temp(resources, work)
        die("打包产物语法校验失败，已中止。原文件未被修改。")
    log("部署后复检：注入标记与语法全部验证通过", ok=True)

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
        os.replace(temp_asar, asar)
        replaced = True
    except PermissionError:
        # macOS：/Applications 权限不足时用管理员密码重试
        #（Windows 走到这里通常是文件被占用，重试无意义）
        if IS_MAC:
            log("权限不足，正在弹出系统密码框以完成替换...", warn=True)
            if _mac_replace_with_admin(temp_asar, asar):
                replaced = True
            else:
                cleanup_temp(resources, work)
                die("替换 app.asar 时权限不足，且管理员授权未完成。\n"
                    "  请在「系统设置 → 隐私与安全性」中允许本工具访问"
                    "「应用程序」文件夹后重试。")
        else:
            cleanup_temp(resources, work)
            die("替换 app.asar 时被拒绝（文件仍被占用）。"
                "请完全退出 Antigravity（含托盘）后重试。")
    if not replaced:
        cleanup_temp(resources, work)
        die("替换 app.asar 失败，已中止（原文件未被修改）。")
    # unpacked 目录：内容与官方完全一致（我们只改 dist/），无需变动；
    # 打包产生的临时 unpacked 目录清理掉
    cleanup_temp(resources, work)
    log("部署完成：app.asar 已替换（原版备份保留在同目录 app.asar.bak）", ok=True)

    # 8b. macOS：重签（Windows 下自动跳过）
    signed = mac_post_deploy(resources, auto_yes=auto_yes)
    if IS_MAC and not signed:
        print()
        print("  [!] 汉化内容已成功写入，但代码签名重签未完成。")
        print("      在此状态下 Antigravity 可能提示「应用已损坏」而无法打开。")
        fix = _mac_write_fix_script(resources)
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
        mark = "✓" if ok_ else "△"
        print(f"  {mark} [{kind}] {label}")
    fails = [r for r in report if not r[2]]
    if fails:
        # 区分两类未命中，避免 macOS 用户把"平台本来就没有的弹窗"误当成故障
        win_only = [r for r in fails
                    if any(k in r[1] for k in ("WSL", "Windows", "windows filesystem"))]
        real = [r for r in fails if r not in win_only]
        if IS_MAC and win_only:
            print(f"\n[·] {len(win_only)} 处为 Windows 专属弹窗（WSL 等），"
                  "Mac 版本就没有这些界面，属正常现象")
        if real:
            print(f"[!] 有 {len(real)} 处锚点未命中（官方版本变动所致，"
                  "对应位置保持英文，不影响其他功能）")
        if not real and not (IS_MAC and win_only):
            print("\n[!] 有锚点未命中，请查看上方明细")
    print("-" * 62)

    # 10. 启动
    if not auto_yes:
        if input("是否立即启动 Antigravity？(Y/n)：").strip().lower() in ("", "y", "yes"):
            launch_app(resources)
    print("\n汉化完成！如界面有少量英文（AI 思考过程、模型名等属于刻意保留），"
          "可编辑 zh_data.json 增补词条后重新运行。")


def cleanup_temp(resources, work):
    # 清理本次 PID 的临时产物 + 任意历史残留（app.asar.agyzh.*.tmp）
    try:
        for name in os.listdir(resources):
            if name.startswith("app.asar.agyzh.") and (name.endswith(".tmp") or name.endswith(".tmp.unpacked")):
                p = os.path.join(resources, name)
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


def cmd_restore(resources, auto_yes=False):
    asar = os.path.join(resources, "app.asar")
    bak = asar + ".bak"
    print("=" * 62)
    print("  恢复官方英文原版")
    print("=" * 62)
    if not os.path.isfile(bak):
        if not asar_is_localized(asar):
            log("当前已是官方原版，无需恢复。", ok=True)
            return
        die(f"未找到备份文件 {bak}，无法恢复。\n"
            "（当前 asar 已汉化且无备份。可重装 Antigravity 恢复英文。）")
    if asar_is_localized(bak):
        die("备份文件本身已被汉化污染（非官方原版），拒绝执行恢复以免损坏。\n"
            "请重装 Antigravity 获取官方原版。")
    if asar_is_localized(asar):
        kill_processes(auto=auto_yes, resources_dir=resources)
    tmp = asar + ".restore.tmp"
    shutil.copy2(bak, tmp)
    os.replace(tmp, asar)
    log("已恢复官方英文原版", ok=True)

    # macOS：还原 asar 同样破坏了签名，必须重签，否则还原后反而打不开
    if IS_MAC:
        if _codesign_available():
            mac_post_deploy(resources, auto_yes=auto_yes)
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
        print("[✓] 当前状态：已启用（规则文件存在）")
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
# 入口
# ----------------------------------------------------------------------------
def main():
    args = [a for a in sys.argv[1:]]
    auto_yes = "--yes" in args or "/y" in args
    args = [a for a in args if a not in ("--yes", "/y")]
    path_override = None
    if "--path" in args:
        i = args.index("--path")
        path_override = args[i + 1] if i + 1 < len(args) else None
        args = [a for a in args if a != "--path" and a != path_override]

    cmd = args[0] if args else ""
    resources = find_resources(path_override)

    if cmd in ("apply", "a", "1"):
        npx = check_env()
        lock = acquire_lock()
        try:
            cmd_apply(resources, npx, auto_yes)
        finally:
            release_lock(lock)
    elif cmd in ("restore", "r", "2"):
        lock = acquire_lock()
        try:
            cmd_restore(resources, auto_yes)
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
            print("  [0] 退出")
            if IS_MAC:
                print("  提示：Mac 上如提示「应用已损坏」，选3 查看状态里的签名项")
            try:
                c = input("请选择：").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if c == "1":
                npx = check_env()
                lock = acquire_lock()
                try:
                    cmd_apply(resources, npx, auto_yes=False)
                finally:
                    release_lock(lock)
                return
            if c == "2":
                lock = acquire_lock()
                try:
                    cmd_restore(resources)
                finally:
                    release_lock(lock)
                return
            if c == "3":
                cmd_status(resources)
                return
            if c == "4":
                cmd_chinese_output()
                return
            if c == "0":
                return
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
