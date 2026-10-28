@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Проверка понимания фраз
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
if not exist eval\results mkdir eval\results

echo Проверка подключения LLM...
"%PY%" scripts\check_llm.py > eval\results\check_llm.txt 2> eval\results\warnings.txt
set LLM_OK=%ERRORLEVEL%
type eval\results\check_llm.txt
echo.

for %%S in (phrases holdout fresh) do (
    call :run %%S rules
    if "%LLM_OK%"=="0" (
        call :run %%S llm
        call :run %%S llm-only
    )
)
echo.
echo Подробности по каждой фразе: папка eval\results
pause
exit /b 0

:run
echo Набор %1, режим %2...
"%PY%" scripts\eval_nlu.py --file eval\%1.jsonl --nlu %2 > eval\results\%1_%2.txt 2>> eval\results\warnings.txt
findstr /b /c:"NLU:" /c:"LLM:" eval\results\%1_%2.txt
exit /b 0
