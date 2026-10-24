@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Голосовой ежедневник

if exist ".venv\Scripts\python.exe" goto dependencies

set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" || goto oldpython

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создаю виртуальное окружение .venv ...
    %PY% -m venv .venv || goto fail
)

:dependencies
echo [2/3] Проверяю зависимости, в первый раз это займёт пару минут...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto fail

echo [3/3] Запускаю сервер: http://localhost:8000
echo Чтобы остановить сервер, закройте это окно.
echo.
set "OPEN_BROWSER=1"
".venv\Scripts\python.exe" -m app
echo.
echo Сервер остановлен.
pause
exit /b

:nopython
echo Python не найден.
echo Установите Python 3.11 или новее с https://www.python.org/downloads/
echo и при установке отметьте "Add python.exe to PATH". Потом запустите этот файл снова.
pause
exit /b 1

:oldpython
echo Нужен Python 3.11 или новее. Сейчас установлен:
%PY% --version
pause
exit /b 1

:fail
echo.
echo Не получилось подготовить окружение. Текст ошибки выше.
pause
exit /b 1
