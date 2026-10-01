# -*- coding: utf-8 -*-
"""
Antigravity 一键汉化 v1.2
=========================
用法：
  python localize.py            # 交互菜单
  python localize.py apply      # 直接汉化
  python localize.py restore    # 恢复官方英文原版
  python localize.py status     # 查看状态
  可选：--path <安装目录>  --yes（免确认）

流程：杀进程 → 备份 → 解包 → 注入 → 语法门禁 → 打包 → 部署后复检 → 原子替换
安全：全程仅本地操作，无任何网络行为；所有修改幂等可重入；备份永不降级。
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

TESTED_VERSION = "2.18.1"          # 深度适配验证过的版本
MARKER_CHECK = b"AGY_ZH_ENGINE_START"   # 用于字节级判断 asar 是否已被汉化

WIN_PATHS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "antigravity"),
    r"C:\Program Files\Antigravity",
    r"C:\Program Files (x86)\Antigravity",
]
MAC_PATHS = ["/Applications/Antigravity.app/Contents/Resources"]

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
def find_resources(override=None):
    """定位 Antigravity 的 resources 目录"""
    if override:
        cand = override if override.endswith("resources") else os.path.join(override, "resources")
        if os.path.isfile(os.path.join(cand, "app.asar")):
            return cand
        die(f"指定路径下未找到 app.asar：{cand}")
    if sys.platform == "win32":
        cands = [os.path.join(p, "resources") for p in WIN_PATHS if p]
    elif sys.platform == "darwin":
        cands = MAC_PATHS
    else:
        cands = ["/opt/Antigravity/resources", "/usr/lib/antigravity/resources"]
    for c in cands:
        if os.path.isfile(os.path.join(c, "app.asar")):
            return c
    die("未找到 Antigravity 安装目录。\n"
        "请用 --path 参数指定安装路径（例如：--path \"%LOCALAPPDATA%\\Programs\\antigravity\"）")


def check_env():
    """检查 node/npx 环境（asar 解包打包需要）"""
    node = shutil.which("node")
    npx = shutil.which("npx")
    if not node or not npx:
        die("未检测到 Node.js（需要 node 和 npx 命令）。\n"
            "请安装 Node.js 18+：https://nodejs.org/ 安装时勾选 Add to PATH")
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
    """语法门禁：对所有被修改的 JS 文件做 node --check 静态校验"""
    bad = []
    for f in files:
        r = subprocess.run(["node", "--check", f], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            bad.append((f, (r.stderr or r.stdout)[-500:]))
    return bad


# ----------------------------------------------------------------------------
# 进程管理
# ----------------------------------------------------------------------------
def kill_processes(auto=False, resources_dir=None):
    """关闭 Antigravity 相关进程。按可执行文件路径过滤，只杀安装目录下的进程——
    其他软件恰好同名（如 language_server.exe）的进程绝不误杀。"""
    if sys.platform != "win32":
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
            subprocess.run(["pkill", "-f", n], capture_output=True)
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
        subprocess.run(["taskkill", "/F", "/PID", str(p["ProcessId"])], capture_output=True)
    for _ in range(20):
        time.sleep(0.3)
        task = subprocess.run(["tasklist"], capture_output=True, text=True,
                              encoding='utf-8', errors='replace').stdout.lower()
        if not any(f"{p['Name'].lower()} " in task or task.endswith(p["Name"].lower()) for p in targets):
            log("进程已全部退出", ok=True)
            return
    log("部分进程未退出，继续尝试（若部署失败请重启电脑后再试）", warn=True)


def launch_app(resources_dir):
    exe = os.path.join(os.path.dirname(resources_dir), "Antigravity.exe")
    if sys.platform == "win32" and os.path.isfile(exe):
        try:
            os.startfile(exe)  # noqa
            log(f"已启动 Antigravity", ok=True)
        except Exception as e:
            log(f"启动失败：{e}（可手动打开）", warn=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Antigravity"])
        log("已启动 Antigravity", ok=True)


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
    print("  Antigravity 一键汉化 v1.2")
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
    try:
        os.replace(temp_asar, asar)
    except PermissionError:
        cleanup_temp(resources, work)
        die("替换 app.asar 时被拒绝（文件仍被占用）。请完全退出 Antigravity（含托盘）后重试。")
    # unpacked 目录：内容与官方完全一致（我们只改 dist/），无需变动；
    # 打包产生的临时 unpacked 目录清理掉
    cleanup_temp(resources, work)
    log("部署完成：app.asar 已替换（原版备份保留在同目录 app.asar.bak）", ok=True)

    # 9. 结果报告
    print("-" * 62)
    for kind, label, ok_ in report:
        mark = "✓" if ok_ else "△"
        print(f"  {mark} [{kind}] {label}")
    fails = [r for r in report if not r[2]]
    if fails:
        print(f"\n[!] 有 {len(fails)} 处锚点未命中（官方版本变动所致，对应位置保持英文，不影响其他功能）")
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
    ver_note = ""
    if asar_is_localized(asar):
        kill_processes(auto=auto_yes, resources_dir=resources)
    tmp = asar + ".restore.tmp"
    shutil.copy2(bak, tmp)
    os.replace(tmp, asar)
    log("已恢复官方英文原版", ok=True)
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
        import struct
        with open(asar, "rb") as f:
            f.read(16)
            import json as _j
            hdr_size = struct.unpack("<I", open(asar, "rb").read(16)[12:16])[0]
        # 简化：从 asar 头读取 package.json 太绕，这里直接报文件大小
        log(f"app.asar 大小：{os.path.getsize(asar) / 1048576:.2f} MB")
    except Exception:
        pass


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
            print("=" * 62)
            print("  Antigravity 一键汉化 v1.2")
            print("=" * 62)
            print("  [1] 一键汉化（官方更新后重新运行即可）")
            print("  [2] 恢复官方英文原版")
            print("  [3] 查看状态")
            print("  [4] AI 回复中文化（让 AI 用中文回答，可开可关）")
            print("  [0] 退出")
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
