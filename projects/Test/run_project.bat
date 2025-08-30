@echo off
REM Auto-generated runner for project 'Test'
setlocal EnableDelayedExpansion

set PRJ_DIR=%~dp0
set PRJ_DIR=%PRJ_DIR:~0,-1%
for %%I in ("%PRJ_DIR%") do set PROJECT_NAME=%%~nI
for %%I in ("%PRJ_DIR%\..\..") do set ROOT_DIR=%%~fI

set PY_WIN=%ROOT_DIR%\.venv\Scripts\python.exe
if exist "%PY_WIN%" (
  set PY_CMD="%PY_WIN%"
) else (
  set PY_CMD=python
)

if "%SCRIPT_REL%"=="" set SCRIPT_REL=scripts\script.json
if "%OUT_MP4%"=="" set OUT_MP4=output\%PROJECT_NAME%.mp4
if "%OUT_SRT%"=="" set OUT_SRT=output\%PROJECT_NAME%.srt

%PY_CMD% "%ROOT_DIR%\adv_maker.py" ^
  --project "%PRJ_DIR%" ^
  --script "%SCRIPT_REL%" ^
  --out "%OUT_MP4%" ^
  --srt "%OUT_SRT%"
