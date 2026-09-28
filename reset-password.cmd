@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" goto missing
if not exist "%ROOT%scripts\reset_local_password.py" goto missing
if /I "%~1"=="--check" goto check
"%ROOT%.venv\Scripts\python.exe" "%ROOT%scripts\reset_local_password.py"
if errorlevel 1 goto failed
echo [SkillPulse] Password reset complete. Sign in with your new password.
pause
exit /b 0

:check
"%ROOT%.venv\Scripts\python.exe" --version
if errorlevel 1 exit /b 1
echo [SkillPulse] Reset tool is ready in: "%ROOT%"
exit /b 0

:missing
echo [SkillPulse] Project files or .venv are missing beside this script.
echo [SkillPulse] Run it from the extracted project folder, not inside the ZIP file.
echo [SkillPulse] Script location: "%ROOT%"
pause
exit /b 1

:failed
echo [SkillPulse] Password was not changed.
pause
exit /b 1
