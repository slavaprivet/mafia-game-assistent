@echo off
chcp 65001 > nul
echo.
echo ========================================
echo   Установка и запуск Game Dev Bot
echo ========================================
echo.

:: Папка назначения — Рабочий стол
set "DEST=%USERPROFILE%\Desktop\game-dev-bot"
set "SRC=%~dp0"

echo Копирую файлы на Рабочий стол...
if not exist "%DEST%" mkdir "%DEST%"
if not exist "%DEST%\handlers" mkdir "%DEST%\handlers"
if not exist "%DEST%\game_repo" mkdir "%DEST%\game_repo"
if not exist "%DEST%\temp" mkdir "%DEST%\temp"

xcopy /Y /Q "%SRC%main.py" "%DEST%\"
xcopy /Y /Q "%SRC%config.py" "%DEST%\"
xcopy /Y /Q "%SRC%memory.py" "%DEST%\"
xcopy /Y /Q "%SRC%ai_client.py" "%DEST%\"
xcopy /Y /Q "%SRC%game_expert.py" "%DEST%\"
xcopy /Y /Q "%SRC%git_manager.py" "%DEST%\"
xcopy /Y /Q "%SRC%vision.py" "%DEST%\"
xcopy /Y /Q "%SRC%voice.py" "%DEST%\"
xcopy /Y /Q "%SRC%limit_manager.py" "%DEST%\"
xcopy /Y /Q "%SRC%teacher.py" "%DEST%\"
xcopy /Y /Q "%SRC%requirements.txt" "%DEST%\"
xcopy /Y /Q "%SRC%.env" "%DEST%\"
xcopy /Y /Q "%SRC%run.bat" "%DEST%\"
xcopy /Y /Q "%SRC%handlers\__init__.py" "%DEST%\handlers\"
xcopy /Y /Q "%SRC%handlers\commands.py" "%DEST%\handlers\"
xcopy /Y /Q "%SRC%handlers\text_tasks.py" "%DEST%\handlers\"
xcopy /Y /Q "%SRC%handlers\media_tasks.py" "%DEST%\handlers\"
xcopy /Y /Q "%SRC%handlers\voice_tasks.py" "%DEST%\handlers\"
xcopy /Y /Q "%SRC%handlers\callbacks.py" "%DEST%\handlers\"

echo [OK] Файлы скопированы в %DEST%
echo.

:: Переходим в папку бота
cd /d "%DEST%"

:: Устанавливаем зависимости
echo Устанавливаю Python-зависимости...
echo (Это займёт 1-2 минуты, не закрывай окно)
echo.
python -m pip install --upgrade pip --quiet
python -m pip install aiogram==3.13.1 aiohttp aiofiles aiosqlite python-dotenv loguru gTTS gitpython Pillow

echo.
echo ========================================
echo   Установка завершена! Запускаю бота...
echo ========================================
echo.

:: Запускаем бота
python main.py

pause
