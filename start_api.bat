@echo off
cd /d C:\Users\<username>\OneDrive\Desktop\Ember-2\ember-2
call .venv\Scripts\activate
python -m uvicorn src.api.main:app --host <your-tailscale-ip> --port 8000 --reload