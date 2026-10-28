@echo off
chcp 65001 >nul
rem батник лежит в scripts\windows, работаем из корня проекта
cd /d "%~dp0..\.."
title Выкладка на Amvera
where git >nul 2>&1 || (echo Git не найден. & pause & exit /b 1)

git remote get-url amvera >nul 2>&1
if errorlevel 1 (
    echo Адрес репозитория проекта есть в Amvera на вкладке «Репозиторий»,
    echo например https://git.msk0.amvera.ru/ваш-логин/voice-assistant
    set /p "AMVERA_URL=Вставьте адрес и нажмите Enter: "
)
if defined AMVERA_URL git remote add amvera %AMVERA_URL% || goto fail

echo.
echo Отправляю код в Amvera. Если появится окно входа, введите логин и пароль от Amvera.
rem репозиторий Amvera — только копия для сборки, поэтому отправляем с заменой
git push --force amvera main:master > amvera_log.txt 2>&1
set RC=%ERRORLEVEL%
type amvera_log.txt
if not "%RC%"=="0" goto fail
echo.
echo Готово. Сборка идёт на странице проекта в Amvera (вкладка «Лог сборки»), обычно 3-5 минут.
pause
exit /b 0

:fail
echo.
echo Не получилось, текст ошибки выше (он же в amvera_log.txt).
pause
exit /b 1
