@echo off
chcp 65001 > nul
echo.
echo ╔══════════════════════════════════════╗
echo ║     Установка Game Dev Bot           ║
echo ╚══════════════════════════════════════╝
echo.

:: Проверяем Python
python --version > nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python не найден!
    echo Скачай Python 3.11+ с https://python.org
    echo Обязательно поставь галочку "Add Python to PATH"
    pause
    exit /b 1
)
echo [OK] Python найден

:: Создаём виртуальное окружение
echo.
echo Создаю виртуальное окружение...
python -m venv venv
call venv\Scripts\activate

:: Обновляем pip
python -m pip install --upgrade pip -q

:: Устанавливаем зависимости
echo.
echo Устанавливаю зависимости (это займёт 1-2 минуты)...
pip install -r requirements.txt -q

echo.
echo ╔══════════════════════════════════════╗
echo ║        Установка завершена!          ║
echo ╚══════════════════════════════════════╝
echo.
echo Следующие шаги:
echo.
echo 1. Установи Ollama: https://ollama.ai
echo    После установки запусти в терминале:
echo    ollama pull qwen2.5-coder:7b
echo    ollama pull llava
echo.
echo 2. Установи Tesseract OCR (для скриншотов):
echo    https://github.com/UB-Mannheim/tesseract/wiki
echo.
echo 3. Установи ffmpeg (для голосовых):
echo    https://ffmpeg.org/download.html
echo.
echo 4. Положи код своей игры в папку game_repo\
echo.
echo 5. Запусти бота: run.bat
echo.
pause
