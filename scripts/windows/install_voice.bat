@echo off
chcp 65001 >nul
rem батник лежит в scripts\windows, работаем из корня проекта
cd /d "%~dp0..\.."
title Голос на сервере
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
    echo Сначала запустите start.bat — он создаст окружение .venv
    pause
    exit /b 1
)
echo [1/3] Ставлю Vosk и Piper...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements-voice.txt > voice_log.txt 2>&1 || goto fail
echo [2/3] Скачиваю модель распознавания русской речи (~45 МБ)...
"%PY%" scripts\download_vosk_model.py >> voice_log.txt 2>&1 || goto fail
echo [3/3] Скачиваю голос для озвучки (~63 МБ)...
"%PY%" scripts\download_piper_voice.py >> voice_log.txt 2>&1 || goto fail
"%PY%" -c "import vosk, piper; print('vosk', vosk.__file__); print('piper', piper.__file__)" >> voice_log.txt 2>&1 || goto fail
type voice_log.txt
echo.
echo Готово. Перезапустите start.bat — голосовой ввод и озвучка заработают в любом браузере.
pause
exit /b 0
:fail
type voice_log.txt
echo.
echo Не получилось, подробности выше и в voice_log.txt
pause
exit /b 1
