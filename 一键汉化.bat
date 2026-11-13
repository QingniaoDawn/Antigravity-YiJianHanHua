@echo off
rem Antigravity One-Click Chinese Localization (Windows / macOS)
rem Run this file again after every official Antigravity update.
rem
rem IMPORTANT: keep this file pure ASCII, no CJK characters anywhere.
rem   cmd decodes .bat bytes using the *console code page*. On a non-CJK system
rem   (for example code page 437 on English Windows) the second byte of a GBK
rem   character can land on 0x7C or 0x5E, which cmd reads as a pipe or escape
rem   character. The line gets truncated and cmd tries to execute the rest.
rem   We measured exactly this: Chinese rem lines were reported as
rem   "xxx is not recognized as an internal or external command".
rem   Keeping the file ASCII removes the whole class of problems.
rem   All Chinese text is printed by localize.py, which goes through
rem   WriteConsoleW and is therefore independent of the console code page.
setlocal
cd /d "%~dp0"

rem Exit code must be initialized explicitly: cmd inherits the parent
rem environment, and a leftover EXITCODE=0 would make every failure below
rem look like success, closing the window without any message.
set "EXITCODE=1"

echo.
echo  ==============================================================
echo    Antigravity YiJian HanHua v1.14
echo  ==============================================================
echo.

rem ---- sanity check: are we really next to localize.py? ----
rem Common mistake: double-clicking the .bat straight from inside a ZIP.
rem Windows then extracts only the .bat to a temp folder, so localize.py is
rem simply not there and python would report a confusing "can't open file".
if not exist "localize.py" goto :NOLOC

rem ---- locate python ----
rem No if(...) blocks here on purpose: cmd block parsing is fragile and
rem breaks on unusual line endings. Plain single-line goto only.
rem Every python call uses call: pyenv-win and conda ship python as a .bat
rem shim, and calling a .bat without call never returns - the script would
rem just end on the spot, which looks exactly like a crash.
set "PYCMD="
call python --version >nul 2>&1 && set "PYCMD=python"
if defined PYCMD goto :VERCHK
call py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if defined PYCMD goto :VERCHK
goto :NOPY

:NOLOC
echo  [ERROR] localize.py was not found next to this file.
echo.
echo  If you double-clicked this .bat from inside a ZIP archive, Windows
echo  only extracted the .bat to a temp folder. Please extract the WHOLE
echo  folder first, then run the .bat from there.
goto :END

:VERCHK
rem Python 3.8 or newer is required (f-strings and other modern syntax).
rem Python 2.7 is still common on old machines and must be rejected clearly.
call %PYCMD% -c "import sys; sys.exit(0 if sys.version_info[0]==3 and sys.version_info[1]>=8 else 1)" >nul 2>&1
if errorlevel 1 goto :OLDPY
goto :RUN

:NOPY
echo  [ERROR] Python not found.
echo.
echo  Install Python 3.8 or newer: https://www.python.org/downloads/
echo  During setup, tick "Add Python to PATH".
goto :END

:OLDPY
echo  [ERROR] Python is too old. This tool needs 3.8 or newer.
echo.
echo  Your version:
call %PYCMD% --version
echo  Download: https://www.python.org/downloads/
goto :END

:RUN
call %PYCMD% localize.py
set "EXITCODE=%ERRORLEVEL%"
goto :END

:END
echo.
if "%EXITCODE%"=="0" goto :OK
if "%EXITCODE%"=="130" goto :CANCELLED
goto :FAILED

:OK
echo  [OK] Finished, exit code 0.
echo  If you never saw the completion message above, nothing was changed.
echo  Run this file again and pick option 1.
goto :PAUSE

:CANCELLED
rem Exit code 130 happens in two very different situations:
rem   a) Ctrl+C at the main menu, and nothing was done
rem   b) Ctrl+C at the "press Enter to return" prompt, where the work WAS done
rem So do not claim "nothing was changed" here; point at the log above instead.
rem Keep this note free of angle brackets: cmd treats them as redirection
rem even inside a rem line, which can truncate the script or create junk files.
echo  [CANCELLED] Interrupted, exit code 130.
echo  Scroll up to see what was actually done before the interruption.
goto :PAUSE

:FAILED
echo  [FAILED] Exit code %EXITCODE%.
echo  Please screenshot this whole window and send it to the author.
echo  If a file named hanhua_error_log.txt exists in this folder, attach it.
goto :PAUSE

:PAUSE
echo.
echo  ==============================================================
echo    This window stays open on purpose.
echo    Press any key to close it.
echo  ==============================================================
pause
exit /b %EXITCODE%
