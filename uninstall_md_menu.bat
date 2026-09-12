@echo off
for %%e in (.md .markdown .mdown .mkd) do (
    reg delete "HKCU\Software\Classes\SystemFileAssociations%%e\shell\mdview" /f >nul 2>&1
)
echo 已移除右键菜单项（mdview.pyw 本身没有被删除）。
pause
