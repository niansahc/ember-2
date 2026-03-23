@echo off
cd /d %~dp0
call .venv\Scripts\activate
rem Set --host to your Tailscale IP (e.g. 100.x.x.x) to allow remote access via Tailscale,
rem or use 127.0.0.1 for local-only access.
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
