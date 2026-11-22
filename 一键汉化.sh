#!/usr/bin/env bash
# ============================================================
# Antigravity 一键汉化 · Linux 版
# ------------------------------------------------------------
# 用法：
#   1. 交互菜单：  ./一键汉化.sh
#   2. 直接汉化：  ./一键汉化.sh apply
#   3. 恢复原版：  ./一键汉化.sh restore
#   4. 查看状态：  ./一键汉化.sh status
#   5. AI中文化：  ./一键汉化.sh chinese-output
#
# 版本号不写死在这里：下方会从 localize.py 的 TOOL_VERSION 读取，
# 避免本文件与主程序版本各说各话（曾出现过横幅显示旧版本的问题）。
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
  if [ -t 0 ]; then read -r -p "按回车键关闭..." _; fi
  exit 1
fi
cd "$(dirname "$PY_FILE")" || exit 1

# 从主程序读版本号（唯一数据源），读不到就只显示名字，绝不硬编码
TOOLVER="$(sed -n 's/^TOOL_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$PY_FILE" 2>/dev/null | head -n 1)"

echo ""
echo "=============================================================="
if [ -n "$TOOLVER" ]; then
  echo "  Antigravity 一键汉化 $TOOLVER  ·  Linux 版"
else
  echo "  Antigravity 一键汉化  ·  Linux 版"
fi
echo "  (支持自动路径探测、/proc 精确进程识别与 POSIX 原子热替换)"
echo "=============================================================="
echo ""

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

if [ -z "$PYCMD" ]; then
  echo "[错误] 未检测到 Python 3.8 或更高版本。"
  echo "  请使用系统包管理器安装 Python 3（例如：sudo apt install python3）。"
  if [ -t 0 ]; then read -r -p "按回车键关闭..." _; fi
  exit 1
fi

echo "[✓] 已找到 Python：$($PYCMD --version 2>&1)"

if command -v node >/dev/null 2>&1; then
  echo "[✓] 已找到 Node.js：$(node --version 2>&1)"
else
  echo "[!] 未检测到 Node.js：将跳过 node --check，改用内置结构自检"
fi
if ! command -v npx >/dev/null 2>&1; then
  echo "[!] 未找到 npx，本次将直接使用内置打包器（不影响汉化结果）"
fi
echo ""

$PYCMD "$PY_FILE" "$@"
EXITCODE=$?

# 若带命令行参数直接调用（如 ./一键汉化.sh status），执行完直接返回退出码
if [ $# -gt 0 ]; then
  exit $EXITCODE
fi

echo ""
if [ "$EXITCODE" = "0" ]; then
  echo "  [完成] 程序正常结束（退出码 0）。"
elif [ "$EXITCODE" = "130" ]; then
  echo "  [取消] 操作被中断（Ctrl+C 或输入被关闭），退出码 130。"
else
  echo "  [提示] 执行未成功结束，退出码 $EXITCODE。"
  echo "  若目录下生成了 hanhua_error_log.txt，可查看详细原因。"
fi
echo ""
if [ -t 0 ]; then
  read -r -p "看完上面的结果按回车键关闭窗口..." _ || true
fi
exit $EXITCODE
