@echo off
chcp 65001 > nul

:: Активируем виртуальное окружение если есть
if exist venv\Scripts\activate (
    call venv\Scripts\activate
)

:: Запускаем бота
echo Запускаю бота...
echo Нажми Ctrl+C для остановки
echo.
python main.py

pause
