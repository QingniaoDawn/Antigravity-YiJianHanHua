@echo off
rem Antigravity One-Click Chinese Localization (Windows / macOS)
rem Run this file again after every official Antigravity update.
cd /d "%~dp0"

echo.
echo  ==============================================================
echo    Antigravity YiJian HanHua v1.8
echo    (首次汉化请耐心等待约 1 分钟，全程结束零残留)
echo  ==============================================================
echo.

rem ---- find python ----
rem 这里刻意不用多行 if(...) 块：cmd 对只用 LF 换行的批处理解析多行块会出错，
rem 表现是一堆 ".xxx 不是内部或外部命令"。全部改成单行 goto，行尾出错也能跑。
set "PYCMD="
python --version >nul 2>&1 && set "PYCMD=python"
if defined PYCMD goto :RUN
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if defined PYCMD goto :RUN

echo  [错误] 未检测到 Python.
echo  请安装 Python 3.8+: https://www.python.org/downloads/
echo  安装时请勾选 "Add Python to PATH".
goto :END

:RUN
%PYCMD% localize.py
set "EXITCODE=%ERRORLEVEL%"
if "%EXITCODE%"=="0" goto :END

echo.
echo  [提示] 执行出错, 返回 %EXITCODE%.
echo  具体原因见 README.md 的「常见问题」一节.

:END
if "%EXITCODE%"=="0" goto :EOF
echo.
pause
