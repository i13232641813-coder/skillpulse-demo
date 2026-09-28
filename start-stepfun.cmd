@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_with_stepfun.ps1"
if errorlevel 1 (
  echo [SkillPulse] StepFun startup failed. Please review the error above.
  pause
)
