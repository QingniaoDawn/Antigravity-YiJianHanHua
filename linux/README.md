# Antigravity 一键汉化 · Linux 版说明

Linux 版与 Windows / macOS 版共用同一套 `localize.py`、`engine.js` 与 `zh_data.json`（20871 翻译单元），并在 Linux 真机环境（Antigravity v2.33.0 / v2.34.0）下完成完整适配与验证。

---

## 快速开始

在仓库根目录或 `linux/` 目录下运行：

```bash
chmod +x ./一键汉化.sh
./一键汉化.sh
```

也可以直接使用命令行子命令：

```bash
# 直接汉化（交互确认）
./一键汉化.sh apply

# 静默汉化 + 免关进程热替换（适合在 Antigravity 内置终端中直接运行，完成后重启应用生效）
./一键汉化.sh apply --yes --no-kill

# 查看当前汉化与备份状态
./一键汉化.sh status

# 一键恢复官方英文原版
./一键汉化.sh restore
```

---

## Linux 版核心适配特性

1. **自动定位多种 Linux 安装路径**：
   - 用户级安装：`~/.local/share/antigravity/resources`、`~/Antigravity/resources`
   - 系统级安装：`/opt/Antigravity/resources`、`/opt/antigravity/resources`、`/usr/lib/antigravity/resources`、`/usr/share/antigravity/resources`
   - `PATH` 软链接反推：自动解析 `which antigravity` / `~/.local/bin/antigravity` 的真实物理路径
   - 运行态进程反推：通过 `/proc/<pid>/exe` 自动定位当前正在运行的 Antigravity 安装目录
   - 自定义路径：支持 `--path "/你的路径/antigravity"` 手动指定

2. **`/proc/<pid>/exe` 内核级精确进程识别（绝不误杀其他软件）**：
   - 原版在 Linux 下无法区分其他编辑器（如 VS Code、Neovim、Cursor）的同名 `language_server` 进程，因此遇到同名进程只能直接中止。
   - Linux 适配版通过读取 `/proc/<pid>/exe` 内核符号链接，**严格只匹配可执行文件位于当前 Antigravity 安装目录内的进程**，彻底杜绝误杀其他软件的语言服务器。

3. **POSIX 原子热替换与内部终端防自杀保护（`--no-kill`）**：
   - 通过回溯 `/proc/<pid>/status` 的 `PPid` 祖先进程链，自动检测脚本是否正在 Antigravity 自身的内置终端或智能体会话中运行。
   - 若在内部会话中运行（或指定了 `--no-kill` / 在交互提示中选择 `h` 热替换），脚本**不会强杀当前会话**，而是利用 Linux/POSIX 文件系统的 `os.replace()` 原子替换特性直接更新 `app.asar`。替换完成后，你只需手动重启一次 Antigravity 即可切换为中文界面。

4. **系统级安装目录（`/opt`、`/usr/lib`）自动提权**：
   - 若 Antigravity 安装在普通用户无写权限的系统目录，脚本会先在临时目录完成解包、注入、语法门禁与打包复检，最后仅在落盘备份与原子替换一步自动调用 `sudo` 或 `pkexec` 提权。
