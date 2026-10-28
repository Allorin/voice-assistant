@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Выкладка на Hugging Face
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
echo Ставлю библиотеку Hugging Face...
"%PY%" -m pip install --disable-pip-version-check -q "huggingface_hub>=0.25" || goto fail
echo Выкладываю прототип. Это займёт до минуты, сборка на Hugging Face — ещё 3-5 минут.
"%PY%" scripts\deploy_hf.py > deploy_log.txt 2>&1
set RC=%ERRORLEVEL%
type deploy_log.txt
if not "%RC%"=="0" goto fail
echo.
echo Готово. Откройте ссылку «Прототип» через несколько минут, когда Space соберётся.
pause
exit /b 0
:fail
echo.
echo Не получилось, текст ошибки выше (он же в deploy_log.txt).
pause
exit /b 1
