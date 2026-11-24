@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo == 线上更新：重建 Pages 版 + 推送到 GitHub ==
echo.
echo 首次使用请先编辑本文件，把下面的 REPO 改成你的仓库，并把 token 写入 gh_token.txt
set REPO=tinghai0724-cmd/huichuan-workbench
rem 后端地址：本机跑着 server.py 时用 http://127.0.0.1:8787；换成隧道域名时改成 https://xxx
set API=http://127.0.0.1:8787
set MSG=%~1

pwsh -NoProfile -ExecutionPolicy Bypass -File "tools\build-docs.ps1" -Api "%API%"
if errorlevel 1 goto err

pwsh -NoProfile -ExecutionPolicy Bypass -File "tools\publish.ps1" -Repo "%REPO%" -Message "%MSG%" -Pages
if errorlevel 1 goto err
echo.
echo 完成。
pause
exit /b 0

:err
echo.
echo 出错了，看上面的报错信息。
pause
exit /b 1
