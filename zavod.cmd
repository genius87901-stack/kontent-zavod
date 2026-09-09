@echo off
rem ЗАВОД — запуск чего угодно из папки завода, без путей к питону.
rem
rem Зачем файл существует. На чистой Windows команда "python" НЕ является питоном:
rem это заглушка магазина приложений. Настоящий питон коробка приносит с собой и
rem кладёт внутрь своей папки, вне PATH. Человеку приходилось печатать
rem ".venv\Scripts\python.exe github_backup.py --login" — три шанса ошибиться там,
rem где смысла ровно на одно действие. Этот файл ищет питон сам.
rem
rem Использование:  zavod.cmd github_backup.py --включи
chcp 65001 >nul 2>&1
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" %*
    exit /b %errorlevel%
)
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" %*
    exit /b %errorlevel%
)
where py >nul 2>&1
if %errorlevel%==0 (
    py %*
    exit /b %errorlevel%
)
echo.
echo Не нашёл питон внутри папки завода.
echo Похоже, завод установлен не полностью. Покажите эту строчку тому, кто ставил.
exit /b 1
