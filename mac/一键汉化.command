#!/bin/bash
# ============================================================
# Antigravity 一键汉化 v1.14 · macOS 版
# ------------------------------------------------------------
# 用法：在 Finder 里双击本文件即可
# 若双击无反应 / 提示"未验证的开发者"：
#   右键点击本文件 → 选择「打开」→ 确认打开
# 首次运行可能需要在「系统设置 → 隐私与安全性」中放行
# ============================================================

# ---------- 定位脚本所在目录 ----------
# 本文件可能位于仓库的 mac/ 子目录下，而 localize.py 在其上一级，
# 因此逐级向上查找，找到 localize.py 为准（最多回溯 3 层）。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_FILE=""
for _try in 1 2 3; do
  if [ -f "$SCRIPT_DIR/localize.py" ]; then
    PY_FILE="$SCRIPT_DIR/localize.py"
    break
  fi
  SCRIPT_DIR="$(dirname "$SCRIPT_DIR")"
done

if [ -z "$PY_FILE" ]; then
  echo "[错误] 找不到 localize.py，请确认解压完整（整个文件夹一起解压）。"
  read -r -p "按回车键关闭..." _
  exit 1
fi
cd "$(dirname "$PY_FILE")" || exit 1

echo ""
echo "=============================================================="
echo "  Antigravity 一键汉化 v1.14  ·  macOS 版"
echo "  (首次汉化请耐心等待约 1-2 分钟，全程仅本地操作)"
echo "=============================================================="
echo ""

# ---------- 找Python ----------
PYCMD=""
if command -v python3 >/dev/null 2>&1; then
  if python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
    PYCMD="python3"
  fi
fi
if [ -z "$PYCMD" ] && command -v python >/dev/null 2>&1; then
  if python -c 'import sys; sys.exit(0 if sys.version_info>=(3,8) else 1)' 2>/dev/null; then
    PYCMD="python"
  fi
fi
if [ -z "$PYCMD" ] && command -v py >/dev/null 2>&1; then
  PYCMD="py -3"
fi

if [ -z "$PYCMD" ]; then
  echo "[错误] 未检测到 Python 3.8 或更高版本。"
  echo ""
  echo "  安装方法（任选其一）："
  echo "    1. 打开终端，粘贴执行：  brew install python"
  echo "    2. 或从官网下载安装：https://www.python.org/downloads/macos/"
  echo ""
  read -r -p "按回车键关闭..." _
  exit 1
fi

echo "[✓] 已找到 Python：$($PYCMD --version 2>&1)"

# ---------- 检查 Node.js ----------
# node 是硬要求（改完 JS 要做语法门禁）；npx 只负责调官方 asar 工具，
# 缺了会由主程序自动改用内置打包器，所以这里只提醒、不阻断。
if ! command -v node >/dev/null 2>&1; then
  echo ""
  echo "[错误] 未检测到 Node.js（语法校验需要 node 命令）。"
  echo ""
  echo "  安装方法（任选其一）："
  echo "    1. 打开终端，粘贴执行：  brew install node"
  echo "    2. 或从官网下载安装：https://nodejs.org/ （选 LTS 版本）"
  echo ""
  read -r -p "按回车键关闭..." _
  exit 1
fi
echo "[✓] 已找到 Node.js：$(node --version 2>&1)"
if ! command -v npx >/dev/null 2>&1; then
  echo "[!] 未找到 npx，本次将直接使用内置打包器（不影响汉化结果）"
fi

# ---------- 检查 macOS 签名工具（缺了汉化后 App 打不开） ----------
if ! command -v codesign >/dev/null 2>&1 || ! command -v xattr >/dev/null 2>&1; then
  echo ""
  echo "[错误] 未检测到 macOS 签名工具（codesign / xattr）。"
  echo ""
  echo "  这是汉化的必要条件：macOS 会对应用包做代码签名，"
  echo "  我们修改了应用包内容后必须重新签名，否则 Antigravity"
  echo "  会提示「应用已损坏」而无法打开。"
  echo ""
  echo "  解决方法（打开「终端」粘贴执行，需联网，约几分钟）："
  echo "      xcode-select --install"
  echo ""
  echo "  弹出安装界面后点「安装」，等待完成再重新运行本工具。"
  echo ""
  read -r -p "按回车键关闭..." _
  exit 1
fi
echo "[✓] 已找到 codesign（macOS 签名工具）"
echo ""

$PYCMD "$PY_FILE"
EXITCODE=$?

echo ""
# 无论成功还是失败都停一下再关窗。
# 原因：Terminal 的「shell 退出时关闭窗口」一旦开着，脚本跑完窗口会瞬间消失，
# 用户只看到黑窗一闪，会当成"按任意键就闪退"。留一步确认，结果才看得见。
if [ "$EXITCODE" = "0" ]; then
  echo "  [完成] 程序正常结束（退出码 0）。"
  echo "  如果你并没有看到「汉化完成」，说明刚才只是取消或没选任何操作，"
  echo "  软件没有被改动。重新双击本文件、按提示选择 1 即可。"
elif [ "$EXITCODE" = "130" ]; then
  # 130 有两种来源：一是在主菜单按 Ctrl+C（确实什么都没做），
  # 二是操作做完后在"按回车返回"处按 Ctrl+C（事情已经做了）。
  # 所以这里不能武断地说"没有被改动"，让用户自己往上翻确认。
  echo "  [取消] 操作被中断（Ctrl+C 或输入被关闭），退出码 130。"
  echo "  往上翻可以看到中断前实际做了什么。"
else
  echo "  [提示] 执行未成功结束，退出码 $EXITCODE。"
  echo "  常见原因见 README 的「常见问题」一节；"
  echo "  若目录下生成了 hanhua_error_log.txt，一并附上。"
fi
echo ""
# 这里绝对不要重定向 stderr：read -p 的提示文字就是写到 stderr 的，
# 加了 2>/dev/null 会把提示一起吞掉，用户只看到一个空行窗口就关了。
# 输入流断开时 read 返回非 0，用 || true 兜住即可，不影响后面的 exit。
read -r -p "看完上面的结果按回车键关闭窗口..." _ || true
exit $EXITCODE
