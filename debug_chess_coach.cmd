@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv312\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Chess Coach: не найден python.exe в .venv312 или .venv.
  echo Создайте окружение по инструкции README.md. Этот файл ничего не устанавливает.
  if defined LOCALAPPDATA (
    if not exist "%LOCALAPPDATA%\ChessCoach\logs" mkdir "%LOCALAPPDATA%\ChessCoach\logs" 2>nul
    >>"%LOCALAPPDATA%\ChessCoach\logs\chess-coach-launcher.log" echo event=launcher_error error_code=python_runtime_missing
  )
  pause
  exit /b 1
)
"%PYTHON%" -m chess_coach --manual-connect
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo Chess Coach завершился с кодом %RESULT%. Диагностика: %LOCALAPPDATA%\ChessCoach\logs\chess-coach.log
  pause
)
exit /b %RESULT%
