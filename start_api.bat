@echo off
cd /d %~dp0
call .venv\Scripts\activate
set EMBER_HOST=127.0.0.1
for /f "tokens=1,* delims==" %%a in ('findstr /b "EMBER_HOST" .env 2^>nul') do set EMBER_HOST=%%b
python -m uvicorn src.api.main:app --host %EMBER_HOST% --port 8000
