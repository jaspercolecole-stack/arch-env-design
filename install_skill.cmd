@echo off
rem Wrapper: run install_skill.ps1 with ExecutionPolicy Bypass.
rem Usage:
rem   install_skill.cmd                 -> install to user-level skills dir
rem   install_skill.cmd -Scope Workspace -WorkspaceRoot "D:\some\workspace"
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_skill.ps1" %*
endlocal
