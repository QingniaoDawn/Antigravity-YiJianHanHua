@echo off
rem Antigravity One-Click Chinese Localization (Windows / macOS)
rem Run this file again after every official Antigravity update.
cd /d "%~dp0"

echo.
echo  ==============================================================
echo    Antigravity YiJian HanHua v1.4
echo    (首次汉化请耐心等待约 1 分钟，全程仅本地操作)
echo  ==============================================================
echo.

rem ---- find python ----
set "PYCMD="
python --version >nul 2>&1 && set "PYCMD=python"
if not defined PYCMD (
    py -3 --version >nul 2>&1 && set "PYCMD=py -3"
)
if not defined PYCMD (
    echo  [错误] 未检测到 Python.
    echo  请安装 Python 3.8+: https://www.python.org/downloads/
    echo  安装时务必勾选 "Add Python to PATH".
    goto :END
)

%PYCMD% localize.py
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo  [提示] 执行出错, 代码 %EXITCODE%.
    echo  常见原因见 README.md 的「常见问题」一节.
)

:END
echo.
pause
