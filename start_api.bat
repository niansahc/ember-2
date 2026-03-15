@echo off
cd /d C:\Users\<username>\OneDrive\Desktop\Ember-2\ember-2
call .venv\Scripts\activate
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000