# Recovery Playbook

Step-by-step fixes for common Ember-2 problems. Start with the scenario that matches your situation.

---

## Scenario 1: API won't start

**Symptoms:** `start_api.bat` fails, browser shows connection refused, or uvicorn throws errors on launch.

**Steps:**

1. Check Python is available:
   ```
   python --version
   ```
   Should show 3.11 or later. If not, reinstall or activate your venv: `.venv\Scripts\activate`

2. Check the virtual environment works:
   ```
   pip list | grep fastapi
   ```
   If fastapi is missing, run `pip install -r requirements.txt`

3. Check `.env` exists and has a valid vault path:
   ```
   cat .env
   ```
   `PRIVATE_VAULT_PATH` must point to a real directory. Check it exists.

4. Check Ollama is running:
   ```
   ollama list
   ```
   If this fails, start Ollama (system tray icon or `ollama serve`).

5. Check port 8000 isn't in use:
   ```
   netstat -ano | findstr :8000
   ```
   If something else is using it, kill that process or change the port.

6. Run the test suite for clues:
   ```
   python -m pytest tests/ -q
   ```
   Any failures here will point directly at the broken component.

7. Check recent audit logs:
   ```
   dir logs\audit\
   ```
   The most recent log file may contain the error.

---

## Scenario 2: Sidebar shows wrong or no conversations

**Symptoms:** Sidebar is empty after you've had conversations, or shows old test sessions you thought were deleted.

**Steps:**

1. Confirm the API is reachable:
   ```
   curl http://localhost:8000/api/health
   ```
   Should return JSON with `"message": "Ember-2 API is running"`. If not, see Scenario 1.

2. Check conversations directly:
   ```
   curl http://localhost:8000/v1/conversations -H "Authorization: Bearer YOUR_KEY"
   ```
   If this returns sessions, the API is fine and the UI may be caching.

3. If empty: your vault path may be wrong. Check `.env`:
   ```
   cat .env | grep PRIVATE_VAULT_PATH
   ```
   Then check that directory has a `memory/session/` folder with JSON files in it.

4. If stale test sessions appear: they were created by the eval harness before the test session flag was added. Clean them up:
   ```
   python scripts/cleanup_test_sessions.py --dry-run
   python scripts/cleanup_test_sessions.py
   ```
   Or delete them one at a time via the UI (right-click > Delete).

5. Hard refresh the browser (Ctrl+Shift+R) to clear cached UI state.

---

## Scenario 3: Ember doesn't remember anything

**Symptoms:** Ember responds generically, doesn't reference your profile or past conversations, fabricates history.

**Steps:**

1. Check the vault path is correct:
   ```
   python -c "from src.core.config import get_private_vault_path; print(get_private_vault_path())"
   ```
   Verify this points to your actual vault with files in it.

2. Check profile records exist:
   ```
   dir C:\EmberVault\memory\profile\
   ```
   If empty, Ember has no profile data. Run the onboarding flow or seed with `scripts/seed_identity_template.py`.

3. Run the retrieval eval:
   ```
   python tools/eval_retrieval.py
   ```
   Scores below 5/10 indicate a retrieval problem.

4. Check embedding indexes exist:
   ```
   dir C:\EmberVault\embeddings\
   ```
   You should see `profile_index.json`, `conversation_index.json`, `reflection_index.json`, and `ingested.db`. If any are missing, delete all index files and restart the API. They rebuild on first startup (slow but automatic).

5. If indexes are present but retrieval is bad, the indexes may be stale. Delete them and restart:
   ```
   del C:\EmberVault\embeddings\*.json
   ```
   Then restart the API. First load will re-embed everything.

---

## Scenario 4: Cloud provider not working

**Symptoms:** Selecting Claude or GPT model returns errors, or Ember times out.

**Steps:**

1. Check the API key is stored:
   ```
   python scripts/set_provider_key.py --provider anthropic --check
   python scripts/set_provider_key.py --provider openai --check
   ```

2. If not configured, set it:
   ```
   python scripts/set_provider_key.py --provider anthropic
   ```
   Or set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env`.

3. Check the model name is correct. Valid names:
   - Anthropic: `claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514`
   - OpenAI: `gpt-4o-mini`, `gpt-4o`, `gpt-4-turbo`, `gpt-3.5-turbo`

4. Check network access. Cloud calls require internet. Tailscale binding only affects inbound traffic, not outbound.

5. Check your provider dashboard for rate limits or billing issues. If your key is expired or over quota, the API will return errors that Ember surfaces as "failed to generate response."

---

## Scenario 5: Vault moved or machine migration

**Symptoms:** Ember starts but can't find memories, or retrieval returns nothing after moving to a new computer.

**Steps:**

1. Copy your vault folder to the new machine. The entire `PRIVATE_VAULT_PATH` directory.

2. Copy your `.env` file and update `PRIVATE_VAULT_PATH` to the new location:
   ```
   PRIVATE_VAULT_PATH=C:/EmberVault
   ```
   Use forward slashes even on Windows.

3. If index files reference old paths, delete them and let Ember rebuild:
   ```
   del C:\EmberVault\embeddings\*.json
   ```

4. Set your API key on the new machine:
   ```
   python scripts/set_api_key.py
   ```

5. Restart the API and run the retrieval eval to confirm:
   ```
   python tools/eval_retrieval.py
   ```

---

## Scenario 6: Lost API key

**Symptoms:** Can't authenticate to the Ember API from the UI or scripts.

**Steps:**

1. Try to retrieve it from Windows Credential Manager:
   - Start menu > search "Credential Manager"
   - Click "Windows Credentials"
   - Look for `ember-2` entry
   - Click the dropdown arrow > "Show" to reveal the key

2. If the key is gone, generate a new one:
   ```
   python scripts/set_api_key.py
   ```
   This creates a new key and stores it in Credential Manager. Copy it immediately.

3. Update the key in your UI settings or `.env` if needed.

---

## Supply Chain Security (March 2026)

If you installed npm packages on March 31, 2026 between 00:21 and 03:29 UTC, check your lockfiles for the compromised axios versions:

```bash
grep -r "1.14.1\|0.30.4\|plain-crypto-js" package-lock.json
```

Ember-2 does not use axios and was not affected. If you run other Node.js projects on the same machine, check those environments separately.

---

## General principles

- **The vault is append-only.** You can't corrupt it by restarting, crashing, or killing the process mid-write. The worst that happens is one incomplete JSON file, which Ember skips on read.
- **Embeddings and indexes are always rebuildable** from the canonical JSON records in `memory/`. Deleting them and restarting is always safe.
- **When in doubt:** check three things: vault path, Ollama, API key. Those three cover 90% of problems.
- **Back up before experimenting.** The vault is plain files. Copy the folder. It takes seconds and saves hours.
