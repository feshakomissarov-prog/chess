@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHONW=%ROOT%.venv312\Scripts\pythonw.exe"
set "PYTHON=%ROOT%.venv312\Scripts\python.exe"
if not exist "%PYTHONW%" (
  set "PYTHONW=%ROOT%.venv\Scripts\pythonw.exe"
  set "PYTHON=%ROOT%.venv\Scripts\python.exe"
)
if not exist "%PYTHONW%" goto :missing
if not exist "%PYTHON%" goto :missing
if not defined PYTHON goto :missing
for /f "tokens=2" %%V in ('"%PYTHON%" --version 2^>^&1') do set "PYVER=%%V"
if not defined PYVER (
  echo Chess Coach: не удалось определить версию Python.
  call :write_log python_version_unavailable
  pause
  exit /b 1
)
for /f "tokens=1,2 delims=." %%A in ("%PYVER%") do (
  if %%A LSS 3 goto :unsupported
  if %%A EQU 3 if %%B LSS 12 goto :unsupported
)
start "Chess Coach" "%PYTHONW%" -m chess_coach --manual-connect
exit /b 0

:missing
  echo Chess Coach: не найдены pythonw.exe и python.exe в .venv312 или .venv.
  echo Создайте окружение по инструкции README.md. Этот файл ничего не устанавливает.
  call :write_log python_runtime_missing
  pause
  exit /b 1

:unsupported
echo Chess Coach требует Python 3.12 или новее. Найдено: %PYVER%
call :write_log unsupported_python_version
pause
exit /b 1

:write_log
if not defined LOCALAPPDATA exit /b 0
if not exist "%LOCALAPPDATA%\ChessCoach\logs" mkdir "%LOCALAPPDATA%\ChessCoach\logs" 2>nul
>>"%LOCALAPPDATA%\ChessCoach\logs\chess-coach-launcher.log" echo event=launcher_error error_code=%1
exit /b 0
