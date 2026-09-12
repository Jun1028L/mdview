@echo off
setlocal enabledelayedexpansion
set "SCRIPT=%~dp0mdview.pyw"
if not exist "%SCRIPT%" (
    echo [错误] 本脚本要和 mdview.pyw 放在同一个文件夹里。
    pause
    exit /b 1
)

set "PYTHONW="
for %%c in (pythonw.exe pythonw.exe pyw.exe) do if not defined PYTHONW (
    for /f "delims=" %%p in ('where %%c 2^>nul') do if not defined PYTHONW (
        echo %%p | find /i "WindowsApps" >nul
        if errorlevel 1 set "PYTHONW=%%p"
    )
)
if not defined PYTHONW for /f "delims=" %%p in ('py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul') do set "PYTHONW=%%p"
if not defined PYTHONW (
    echo [错误] 没有找到 pythonw.exe，请先安装 Python 3 并勾选 Add to PATH。
    pause
    exit /b 1
)
if not exist "%PYTHONW%" (
    echo [错误] 路径无效：%PYTHONW%
    pause
    exit /b 1
)

set "CMDLINE=\"%PYTHONW%\" \"%SCRIPT%\" \"%%1\""
for %%e in (.md .markdown .mdown .mkd) do (
    reg add "HKCU\Software\Classes\SystemFileAssociations%%e\shell\mdview" /f /v "" /d "用 Markdown 查看器打开" >nul
    reg add "HKCU\Software\Classes\SystemFileAssociations%%e\shell\mdview" /f /v "Icon" /d "%PYTHONW%,0" >nul
    reg add "HKCU\Software\Classes\SystemFileAssociations%%e\shell\mdview\command" /f /v "" /d "!CMDLINE!" >nul
)
echo 已完成：现在可以在 .md 文件上点右键，选“用 Markdown 查看器打开”。
echo 只写入当前用户的右键菜单，不改变 .md 的默认打开方式。
echo 菜单没出现的话，在资源管理器里按 F5 刷新一下即可。
pause
