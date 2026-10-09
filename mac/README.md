# Antigravity 一键汉化 · macOS 版

在 Mac 上把 Antigravity 界面完整汉化。**18845+ 词条、零联网、一键还原、双击即用。**

Windows 用户请用仓库根目录的 `一键汉化.bat`，本目录只服务 macOS。

---

## 使用前先了解一件事

Mac 和 Windows 最大的不同：**macOS 需要额外做一次代码签名**。

不用担心，工具已经自动处理了。你只需要知道：

- 汉化过程中系统会**弹窗问你要 Mac 密码**，这是正常的，输入即可
- 万一提示「应用已损坏」，同目录的 `修复签名.command` 双击就能修好
- 任何一步出问题，界面都会用中文告诉你下一步该做什么

遇到问题可以先运行菜单 `3`（查看状态），这个操作**只读取信息、不修改任何文件**，
能帮你确认工具是否正常识别了你的 Antigravity。

---

## 先看这里：Mac 和 Windows 最大的不同

macOS 会对 `.app` 应用包做**代码签名**。本工具要修改应用包里的文件，
这会让原有签名失效，**签名失效的 App 在 Mac 上会被拒绝启动**，
提示「应用已损坏，无法打开」（Apple Silicon 芯片上更可能直接启动失败）。

所以 Mac 版比 Windows 版多一步：**汉化完成后自动重新签名**。

| | Windows 版 | macOS 版（你在这里） |
|---|---|---|
| 启动方式 | 双击 `一键汉化.bat` | 双击 `一键汉化.command` |
| 安装位置 | `%LOCALAPPDATA%\Programs\antigravity` | `/Applications/Antigravity.app` |
| 改包后需要重签名 | 不需要 | **需要，脚本已自动完成** |
| 需要管理员密码 | 否 | 重签时可能弹窗要密码 |
| 词典 / 引擎 / 备份策略 | 与 Mac 版完全一致 | 同左 |

---

## 使用方法

1. 解压本压缩包（macOS 会自动解压，得到一个文件夹）
2. 进入文件夹，**右键点击 `一键汉化.command` → 选择「打开」→ 确认打开**

   > 为什么不直接双击？macOS 对"从网上下载的脚本"有安全限制，
   > 右键「打开」相当于你手动确认一次信任，只需做这一次。
   > 之后双击就能正常运行。

3. 在弹出的终端窗口里按数字键选择：

| 选项 | 功能 |
|---|---|
| `1` 一键汉化 | 关闭进程 → 备份原版 → 注入汉化 → 校验 → 部署 → 重签名（约 1-2 分钟） |
| `2` 恢复官方英文原版 | 用备份一键还原（同样会自动重签名） |
| `3` 查看状态 | 当前是否已汉化、备份是否完好、**代码签名是否有效** |
| `4` AI 回复中文化 | 写入一条全局规则，让 **AI 自己用中文回答**（可随时移除） |

官方更新后界面变回英文，**再运行一次选项 1 就重新汉化好了**。

命令行用法：

```bash
python3 localize.py apply      # 直接汉化
python3 localize.py restore    # 恢复英文原版
python3 localize.py status     # 查看状态
python3 localize.py apply --path "/Applications/Antigravity.app"   # 指定路径
```

---

## 环境要求

- macOS 12(Monterey)或更高
- 已安装 Antigravity（默认在 `/Applications`，其他常见位置也能自动找到）
- **Python 3.8+**：终端执行 `brew install python`
- **Node.js 18+**：终端执行 `brew install node`（解包/打包 app.asar 必需）
- **Xcode 命令行工具**：终端执行 `xcode-select --install`
  （提供 `codesign` / `xattr`，**没有它汉化后 App 会打不开**，务必确认已装）

前两项没装也没关系，双击启动器会检测并告诉你怎么装。
Xcode 命令行工具如果缺失，主程序会在汉化开始前就明确报错并给出安装命令。

检查是否已装：

```bash
xcode-select -p    # 有输出即已安装
codesign --help    # 能打印用法即正常
```

### 安装位置自动识别

以下位置都会被自动找到，无需手动指定：

| 位置 | 说明 |
|---|---|
| `/Applications/Antigravity.app` | 标准安装位置 |
| `~/Applications/Antigravity.app` | 用户自装 |
| `~/Downloads/Antigravity.app` | 下载后未拖入「应用程序」 |
| `~/Desktop/Antigravity.app` | 桌面直接运行 |
| `/Volumes/*/Antigravity.app` | dmg 挂载后直接运行 |
| `/opt/homebrew/Caskroom/antigravity/*/` | Homebrew 安装（Apple Silicon） |
| `/usr/local/Caskroom/antigravity/*/` | Homebrew 安装（Intel） |

若以上都没找到，脚本会在上述目录中逐层搜索（限深度，不扫全盘，约 0.02 秒）。
仍找不到时按提示用 `--path` 指定即可：

```bash
python3 localize.py apply --path "/你的路径/Antigravity.app"
```

---

## 汉化覆盖范围

与 Windows 版完全一致：

- **网页界面**：侧边栏、对话流、设置全部子页、模型与用量、MCP 管理、权限沙盒、
  插件中心、远程控制、会话历史、IDE 面板词条
- **原生菜单**：顶部菜单栏（文件/编辑/视图/分屏 等 74 条）
- **系统托盘**：右键菜单、智能体运行计数
- **系统弹窗**：退出确认、更新检查、工作区选择等
- **安装向导**：欢迎页文案
- **权限确认弹窗**：一直允许 / 仅允许一次 / 拒绝 / 跳过 全套按钮
- **输入框提示**：placeholder / aria-label（不触碰你输入的内容本身）

### 与 Windows 版的两点客观差异（不是缺陷）

**1. 有 3 处弹窗 Mac 上本来就没有**

退出确认、更新检查、工作区选择这些弹窗两平台都有，会正常汉化。
但其中 3 处是 **Windows 专属**的——WSL 相关提示（"未找到 WSL 发行版"、
"Antigravity 已在 Windows 上打开"、"文件夹位于 Windows 文件系统"）。
Mac 没有 WSL，这些界面在 Mac 版 Antigravity 里压根不存在，
所以运行时会提示"3 处为 Windows 专属弹窗，Mac 版本就没有这些界面，属正常现象"。

**2. 沙盒权限的实现两平台不同**

Antigravity 官方文档说明：macOS 与 Linux 使用新版统一权限系统
（沙盒默认开启），Windows 沿用旧版行为。
这属于 **Antigravity 自身的差异**，不是本工具的问题——
本工具汉化的是界面文字，而两平台的界面文案一致，所以翻译效果相同。

### 刻意保留英文的内容（行业共识，不是遗漏）

- **AI 思考过程**（Thinking/Thought 流式输出）——避免机翻污染推理内容
- **模型名**（Gemini / Claude 等产品名）
- **代码块、文件路径、命令、你在输入框里打的任何字**——物理隔离，绝不触碰
想让 **AI 的回复**也是中文？选菜单 `4`。界面汉化管不了 AI 生成的内容，
正确做法是加一条"用简体中文回复"的全局规则，脚本已内置。

---

## 常见问题

### Q：双击 `一键汉化.command` 没反应 / 提示"未验证的开发者"

**这是 macOS 的安全机制，不是 bug。** 处理办法：

1. 右键点击 `一键汉化.command` → 选择「打开」→ 点「打开」
2. 若仍然不行，打开「系统设置 → 隐私与安全性」，往下找到"已阻止使用"，
   点「仍要打开」

命令行等价做法：

```bash
chmod +x 一键汉化.command      # 补上可执行权限
xattr -d com.apple.quarantine 一键汉化.command   # 去掉下载标记
```

### Q：汉化完提示"应用已损坏，无法打开"

**这是 Mac 最常见的故障，几乎都是签名问题。** 先跑一次菜单 `3`，
看「代码签名」这一项是不是显示"已失效"。

如果汉化时签名已经重签成功但仍打不开，同目录下会有一个
**`修复签名.command`**，双击运行即可（会弹窗索要 Mac 密码）。

> 如果连这个文件都没有，说明汉化时签名工具缺失，
> 请先在终端执行 `xcode-select --install` 装好 Xcode 命令行工具，
> 再重新运行一次汉化（菜单 `1`）。

彻底手工修复（打开「终端」粘贴）：

```bash
# 1. 清除扩展属性
sudo xattr -cr "/Applications/Antigravity.app"

# 2. 分层重新签名（先签嵌套组件，再签主包）
APP="/Applications/Antigravity.app"
for f in "$APP/Contents/Frameworks"/*.framework; do
  [ -e "$f" ] || continue
  if [ -d "$f/Versions" ]; then
    for v in "$f/Versions"/*; do
      [ -e "$v" ] || continue
      [ "$(basename "$v")" = "Current" ] && continue
      codesign --force --sign - "$v"
    done
  else
    codesign --force --sign - "$f"
  fi
done
for f in "$APP/Contents/Frameworks"/*.app "$APP/Contents/Frameworks"/*.dylib; do
  [ -e "$f" ] || continue
  codesign --force --sign - "$f"
done

# 3. 主包签名（带Electron 必需的权限）
codesign --force --sign - --entitlements /dev/stdin "$APP" <<'PLIST'
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

# 4. 校验
codesign --verify --deep --strict "$APP" && echo "签名有效"
```

> **为什么要分层签名，不能直接 `codesign --force --deep --sign -`？**
> Apple 官方明确反对用 `--deep` 签名：它会把同一份签名选项套用到包内
> 每个嵌套代码项。而 Electron 的 Helper 进程与主程序权限需求不同
> （Helper 只需继承沙盒，不该声明 JIT 等主进程权限），
> 混签会生成**无效签名**——表现是签名校验能过，但 App 一启动就崩。
> 另外**只有主包才能带 entitlements**，给 framework 签名时带上它同样会产生无效签名。

> **为什么要单独装 Xcode 命令行工具？**
> `codesign` 和 `xattr` 都随 Xcode 命令行工具提供。
> 没装的话汉化能写进去，但签名做不了，App 就打不开。
> 安装命令：`xcode-select --install`

### Q：重签名会不会影响我以后更新 Antigravity？

不会，但要注意一点：

- 重签用的是苹果的**临时签名**（ad-hoc），不是 Google 的官方签名
- 官方自动更新会把整个 `.app` 换成全新的一份（签名恢复成官方的），
  界面也会变回英文 —— 这是正常的
- 更新完再运行一次选项 `1` 重新汉化即可，备份会自动刷新成新版官方原版

### Q：提示"另一个汉化程序正在运行"

防并发保护：两个窗口同时跑会互相破坏文件。关掉多余的窗口重试即可。
若上次是强制关窗导致的残留锁，10 分钟后会自动清除，也可手动删除：

```bash
rm -f /var/folders/*/*/T/antigravity_hanhua.lock 2>/dev/null
rm -f "$TMPDIR/antigravity_hanhua.lock" 2>/dev/null
```

### Q：提示"目标程序包的身份不是 Antigravity"

防误伤保护：`--path` 指到了别的 Electron 程序。
请确认路径指向的是 Antigravity.app。

### Q：有些界面还是英文？

AI 思考过程和模型名是刻意保留的。其他漏翻的，打开 `zh_data.json`，
在 `exact` 段加一行 `"英文原文": "中文翻译"`，重新运行汉化即可。

### Q：杀毒软件报毒？

本工具是明文 Python 脚本，无混淆无打包，可自行审阅每一行代码。
报毒通常是"修改其他程序文件"这类行为检测，属预期行为。
macOS 若拦截，直接在「系统设置 → 隐私与安全性」放行即可。

### Q：想看引擎运行状态？

Antigravity 里按 `Cmd+Option+I` 打开开发者工具，
控制台输入 `__AGY_ZH_DEBUG__.stats()`。

---

## 安全特性

- **零网络**：全程本地操作，引擎不含任何联网代码（唯一的 npx 调用只用于下载
  官方 @electron/asar 打包工具）
- **语法门禁**：所有改动先过 `node --check`，不过绝不写入；部署后还会二次复检
- **备份永不降级**：官方更新后重新汉化时，备份自动刷新为新版官方原版
- **幂等可重入**：重复运行不叠加注入（已实测两次执行产物字节级一致）
- **防并发**：文件锁 + 带进程号的临时文件名，后来的实例安全退出
- **防污染还原**：恢复前校验备份纯净性，被污染的备份拒绝执行恢复
- **原子替换**：先写临时文件再同卷替换，中途断电不会损坏
- **精确进程匹配**：按可执行文件完整路径筛选，只关 Antigravity 自己的进程，
  其他软件恰好同名也绝不误杀
- **签名保留权限**：重签时导出并带回原包 entitlements（Electron 运行必需的
  JIT 等权限），不会因为重签导致启动崩溃

---

## 已知限制（如实说明）

1. **本版未在 Mac 真机完成全流程实测**。Windows 版在 v2.18.1 / v2.21.1 真机
   做过完整生命周期演练（还原 → 重汉化 → 幂等复测，两次产物 md5 字节级一致）；
   Mac 版与 Windows 版共用同一份引擎与词典（文件哈希相同），汉化文字效果一致，
   差别仅在 macOS 特有的代码签名环节。
   Mac 版经过 6 轮深度审计，覆盖：签名流程与命令参数、sudo 降级与异常分支、
   修复脚本内容、7 类安装位置识别、特殊字符路径转义、临时文件清理、
   界面中文化程度、边界场景（芯片差异 / 系统只读卷 / 进程占用 / 磁盘空间 /
   版本差异 / 身份守卫 / 并发锁 / 中文路径编码）。
   特殊字符路径经真实 bash 验证可正确还原且无命令注入。
   但**真机首次使用如遇问题，欢迎反馈**——这是唯一无法用测试替代的环节。
2. **AI 思考过程（Thinking）保持英文**——流式推理内容不适合词典机翻
3. **模型名保持英文**（Gemini / Claude 等产品名）
4. **个别下拉选项保持英文**：受控组件的 option 值若翻译会导致组件状态失配，
   采用保守策略，以"能用"优先于"全翻"
5. **官方大版本更新后**：部分锚点可能未命中（脚本会跳过并提示，不报错不损坏），
   界面主体翻译不受影响
6. **重签用的是苹果临时签名**：不是 Google 官方签名，因此官方自动更新后会变回
   英文，重跑一次选项 `1` 即可（备份会自动刷新为新版官方原版）

---

## 技术实现与来源

与 Windows 版共用同一套引擎与词典，整合了 **15 个开源项目**的最佳实践
（全部逐一通过安全审计）+ 自研增强，详见仓库根目录 README。

macOS 专属增强（本次新增）：

- **安装位置自动识别**：覆盖标准位置、用户目录、下载目录、桌面、
  dmg 挂载点、Homebrew（Intel 与 Apple Silicon）共 7 类；
  都没找到时逐层广搜（限深度，约 0.02 秒）
- 按完整可执行路径精确过滤进程，替代 Linux 风格的模糊匹配（不误杀同名程序）
- **代码签名重签模块（Apple 官方推荐的分层方式）**：
  1. `xattr -cr` 清除扩展属性（quarantine 独立导致"已损坏"）
  2. 导出原包 entitlements（`codesign -d --entitlements - --xml`），
     并处理 Xcode 13.3+ 输出尾部 NUL 字节的坑
  3. 逐个签嵌套组件（framework 签 `Versions/<版本>`、Helper、dylib、appex），
     **不带 entitlements**
  4. 最后签主包，**带上 entitlements**
  5. `codesign --verify --deep --strict` 校验
- 权限不足时的两处降级：重签、以及替换 app.asar 本身，
  均通过 `osascript` 弹窗请求管理员密码；
  命令先落盘成临时脚本再执行，避免 AppleScript 与 POSIX 两层转义叠加
- 部署前磁盘空间预检（备份 + 新包需约 2 倍 asar 空间）
- 重签失败时自动生成 `修复签名.command`，用户双击即可修复
- 环境前置检查：缺 `codesign` / `xattr` 时在汉化前明确报错并给出安装命令
- 状态检查新增签名健康度显示
- **界面全中文**：系统英文报错自动翻译（如 `Permission denied` →
  「权限不足，请重试并输入 Mac 密码」），并保留英文原文便于排查

---

## 免责声明

本工具仅供个人学习与效率优化，非 Google 官方产品。修改客户端可能违反
Antigravity 服务条款，风险自担。还原功能可随时回到官方纯净状态。

**永久免费**：任何收费售卖均为倒卖，请拒绝并向平台举报。
