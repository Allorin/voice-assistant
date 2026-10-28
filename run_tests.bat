@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Тесты
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
echo Ставлю зависимости для тестов...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-dev.txt || goto fail
echo Запускаю тесты...
"%PY%" -m pytest -q -p no:cacheprovider > tests_log.txt 2>&1
set RC=%ERRORLEVEL%
type tests_log.txt
where node >nul 2>&1 && node --test tests\js\audio.test.cjs >> tests_log.txt 2>&1
if not "%RC%"=="0" goto fail
echo.
echo Все тесты пройдены. Журнал: tests_log.txt
pause
exit /b 0
:fail
echo.
echo Есть ошибки, подробности выше и в tests_log.txt
pause
exit /b 1
