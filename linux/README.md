# Antigravity 一键汉化 · Linux 版说明

Linux 版与 Windows / macOS 版共用同一套 `localize.py`、`engine.js` 与 `zh_data.json`（20871 翻译单元）。

> **验证范围（如实说明）**：
> Linux 分支由社区贡献（[PR #1](https://github.com/QingniaoDawn/Antigravity-YiJianHanHua/pull/1)），
> 贡献者在 Antigravity **v2.33.0** 的 Linux x86_64 真机上完成了完整流程验证
> （解包、注入 18 个文件、`node --check` 语法门禁、重打包、二次复检、热替换部署）。
> **v2.34.0 未在 Linux 真机复测**；维护者已离线验证该分支与现有引擎/词典的结构兼容性，
> 并对 Windows 路径做了逐字节回归（产物 SHA-256 与改动前完全一致）。
> 如果你在 Linux 上遇到问题，欢迎提 Issue 反馈。

---

## 快速开始

**从 Release 下载的 zip** 已经带好可执行权限，解压后直接运行即可：

```bash
./一键汉化.sh
```

**从源码 / git clone 获取的**需要先补一次权限：

```bash
chmod +x ./一键汉化.sh
./一键汉化.sh
```

也可以在 `linux/` 目录下运行同名脚本（它会自动转发到仓库根目录的实现）。

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

---

## 已知限制

1. **未在 v2.34.0 的 Linux 真机复测**（详见顶部「验证范围」）。
2. **`/opt` 等系统目录需要交互授权**：`sudo` 会提示输入密码；若在无终端的环境（如某些图形化启动器）里运行，`pkexec` 会弹图形认证框。两者都不需要提前用 `sudo` 跑整个脚本。
3. **「检查更新」下载的包放在桌面**：优先用 `xdg-user-dir DESKTOP` 查询；若该命令不存在，回退到 `~/桌面` 和 `~/Desktop`。
4. **AppImage / Flatpak / Snap 等非标准分发形式未适配**：脚本按「安装目录下存在 `resources/app.asar`」来定位，上述打包方式若把资源放进只读镜像，汉化无法落盘。

---

## 出了问题时

先跑一次状态检查，确认目标路径和备份是否正常：

```bash
./一键汉化.sh status
```

若提示找不到 Antigravity，用 `--path` 直接指到安装目录（指到 `resources` 子目录也可以）：

```bash
./一键汉化.sh apply --path "/你的安装路径/antigravity"
```

失败时同目录会生成 `hanhua_error_log.txt`，里面有解包、注入、打包各阶段的具体报错。该文件已被 `.gitignore` 忽略，不会被误提交。
