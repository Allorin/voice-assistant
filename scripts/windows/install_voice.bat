@echo off
chcp 65001 >nul
rem батник лежит в scripts\windows, работаем из корня проекта
cd /d "%~dp0..\.."
title Распознавание речи на сервере
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo Сначала запустите start.bat — он создаст окружение .venv
    pause
    exit /b 1
)
echo [1/2] Ставлю Vosk...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-voice.txt > voice_log.txt 2>&1 || goto fail
echo [2/2] Скачиваю модель русской речи (~45 МБ)...
"%PY%" scripts\download_vosk_model.py >> voice_log.txt 2>&1 || goto fail
"%PY%" -c "import vosk; print('vosk', vosk.__file__)" >> voice_log.txt 2>&1 || goto fail
type voice_log.txt
echo.
echo Готово. Перезапустите start.bat — голосовой ввод заработает и в Firefox.
pause
exit /b 0
:fail
type voice_log.txt
echo.
echo Не получилось, подробности выше и в voice_log.txt
pause
exit /b 1
