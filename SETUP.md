# SETUP.md — Ember-2 Setup Guide

This guide walks you through setting up Ember-2 from scratch. It assumes you are comfortable running commands in a terminal but does not assume a software development background.

**Recommended for non-technical users:** Download the [Ember Setup installer](https://github.com/niansahc/ember-2-installer/releases) instead — it handles all of these steps automatically.

---

## What You Are Setting Up

- **Ember API + UI** — the core backend and web interface (FastAPI, runs locally on port 8000)
- **Ollama** — runs the local language model on your hardware
- **SearXNG** — a private web search engine running in Docker (used for web-aware queries)

One process, one port, one URL. The Ember UI is served directly by FastAPI from the `ui/` folder.

---

## Prerequisites

Install the following before continuing.

### 1. Python 3.11 or later
Download from [python.org](https://www.python.org/downloads/). During installation, check **"Add Python to PATH"**.

Verify:
```
python --version
```

### 2. Ollama
Download from [ollama.com](https://ollama.com/download). This runs the local language models.

Verify:
```
ollama --version
```

### 3. Docker Desktop
Download from [docker.com](https://www.docker.com/products/docker-desktop/). This runs SearXNG for web search.

Verify:
```
docker --version
```

### 4. Git (optional)
Download from [git-scm.com](https://git-scm.com/downloads). Only needed if you want to clone the repo directly. You can also download a ZIP from GitHub.

---

## Setup Steps

### Step 1 — Get the Code

Downloads the Ember-2 codebase to your machine.

**If you have git:**
```
git clone https://github.com/niansahc/ember-2.git
cd ember-2
```

**If you don't have git:**
1. Go to [github.com/niansahc/ember-2](https://github.com/niansahc/ember-2)
2. Click the green **Code** button → **Download ZIP**
3. Extract the ZIP somewhere on your computer (e.g. your Desktop)
   > GitHub names the extracted folder **`ember-2-main`** — that is the correct folder.
4. Open a terminal (Command Prompt or PowerShell on Windows)
5. Navigate into the extracted folder. GitHub creates a folder inside a folder, so you need to go two levels deep. If you extracted to your Desktop:
```
cd %USERPROFILE%\Desktop\ember-2-main\ember-2-main
```
6. Confirm you are in the right place — you should see `SETUP.md` listed:
```
dir
```

All commands from this point forward must be run from inside this folder. If you open a new terminal window later, navigate back here before running anything.

---

### Step 2 — Create a Virtual Environment

Creates an isolated Python environment so Ember's dependencies do not conflict with anything else on your system.

```
python -m venv .venv
```

Activate it:

- **Windows:** `.venv\Scripts\activate`
- **Mac/Linux:** `source .venv/bin/activate`

You should see `(.venv)` at the start of your terminal prompt. You will need to activate this environment every time you open a new terminal to work with Ember.

---

### Step 3 — Install Dependencies

Installs all Python packages Ember-2 requires.

```
pip install -r requirements.txt
```

> This will take a few minutes — sentence-transformers and torch are large.

---

### Step 4 — Pull Ollama Models

Downloads the language models Ember uses for conversation, reasoning, and vision. This step requires an internet connection and may take 10–30 minutes depending on your connection.

**Primary reasoning model:**
```
ollama pull qwen3:8b
```

**Vision model** (for analyzing images in chat):
```
ollama pull llama3.2-vision:11b
```

Verify both are available:
```
ollama list
```

---

### Step 5 — Create Your Memory Vault

Creates the directory where Ember will store all your memories, journal entries, reflections, and context.

**The default location is `C:\EmberVault`. If you are happy with that, just run:**
```
mkdir C:\EmberVault
```
And move on to Step 6 — no other changes needed.

**If you want to use a different drive (e.g. D:):**
```
mkdir D:\EmberVault
```
Note the path — you will need to update it in Step 6.

**Mac/Linux:**
```
mkdir -p /data/embervault
```

> Choose a location on a local drive, not a cloud-synced folder (not OneDrive, iCloud, or Dropbox). This keeps your private data off external servers.

---

### Step 6 — Configure Your Environment

Creates your personal configuration file from the provided template.

> **Using Tailscale for remote access?** Before filling in this file, you need your Tailscale IP. Install Tailscale now if you haven't already ([tailscale.com/download/windows](https://tailscale.com/download/windows)), sign in, and find your IP address in the system tray icon — it starts with `100.`. Then come back here and set `EMBER_HOST` below.

```
cp .env.example .env
```

Open `.env` in any text editor (Notepad, Notepad++, TextEdit on Mac).

**Set the vault path** to wherever you created it in Step 5. Use forward slashes (`/`) even on Windows:

```
# C: drive example:
PRIVATE_VAULT_PATH=C:/EmberVault

# D: drive example:
PRIVATE_VAULT_PATH=D:/EmberVault
```

**Set the API host:**

```
# Local access only (default):
EMBER_HOST=127.0.0.1

# Tailscale remote access — replace with your Tailscale IP:
EMBER_HOST=100.x.x.x
```

Also set the model names (leave these as-is unless you pulled different models):

```
EMBER_MODEL=qwen3:8b
EMBER_VISION_MODEL=llama3.2-vision:11b
```

> `.env` is gitignored. It will never be committed to the repository.

---

### Step 7 — Seed Your Identity Profile (Optional)

Tells Ember who you are so she can personalize responses from the start.

> **Note:** This step is optional. If you skip it, Ember will ask you the same questions interactively the first time you send a message — that onboarding conversation does the same job. Only use this script if you prefer to fill in your profile manually before starting. **If you run this step, the onboarding conversation will not trigger** (Ember will see existing profile records and go straight to normal conversation).

If you'd like to seed manually:

**Copy the template outside the repo:**

Windows:
```
copy scripts\seed_identity_template.py ..\seed_identity.py
```

Mac/Linux:
```
cp scripts/seed_identity_template.py ../seed_identity.py
```

**Edit the copy** — open `../seed_identity.py` in a text editor and replace the placeholder text in each record with your real information. Write in full sentences in first person. Each entry should be at least 40 characters.

**Run it:**
```
python ../seed_identity.py
```

You will see a list of records written. Safe to re-run — exact duplicates are skipped automatically.

---

### Step 8 — Set Your API Key

Generates a secure API key and stores it in Windows Credential Manager — not in any file. This key protects the Ember API from unauthorized access.

```
python scripts/set_api_key.py
```

The script will display your key once. **Copy it immediately** — you will need it when accessing the UI. If you lose it, you can retrieve it from Windows Credential Manager (search for "Credential Manager" in the Start menu → Windows Credentials → look for `ember-2`) or run the script again to rotate to a new key.

---

### Step 9 — Start SearXNG

Starts SearXNG (private web search) locally in Docker.

**Before running this step:**
1. Make sure Docker Desktop is open and running — look for the Docker whale icon in your system tray. If it is not there, open Docker Desktop and wait until it says "Engine running".
2. Make sure your terminal is still in the ember-2 folder.

**Start SearXNG:**
```
docker compose up -d
```

The first time you run this it will download the image — this may take a few minutes. After that it starts instantly.

**Verify it is running:**
```
docker ps
```

You should see `ember-searxng` with `Up` in the status column.

> If you see `docker compose: command not found`, try `docker-compose up -d` (with a hyphen) — older versions of Docker use the hyphen form.

---

### Step 10 — Populate the UI Folder

The Ember UI is a built web application served by FastAPI from the `ui/` folder. The installer does this automatically. If you are setting up manually, you have two options:

**Option A — Copy from a release (simplest):**
Download a pre-built UI from the [ember-2-ui releases](https://github.com/niansahc/ember-2-ui/releases) and extract the contents into an `ui/` folder at the root of the ember-2 repo.

**Option B — Build from source:**
```
git clone https://github.com/niansahc/ember-2-ui.git
cd ember-2-ui
npm install
npm run build
```
Then copy the contents of `ember-2-ui/dist/` into `ember-2/ui/`.

> The `ui/` folder is gitignored. It is not part of the ember-2 repository — it is populated at install time.

---

### Step 11 — Start Ember

Starts the Ember backend server and UI. Run this from the repo root with your virtual environment activated.

The API host is controlled by `EMBER_HOST` in your `.env` file. It defaults to `127.0.0.1` (local-only access). If you are using Tailscale to access Ember from another device, set `EMBER_HOST` to your Tailscale IP before starting.

**Windows (recommended):**
```
start_api.bat
```

**Or directly:**
```
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

> If using Tailscale, replace `127.0.0.1` in the direct command with your Tailscale IP, or just use `start_api.bat` which reads `EMBER_HOST` from `.env` automatically.

Open a browser and go to **http://localhost:8000**. You should see the Ember UI.

> If the `ui/` folder is not populated, you will see `{"message": "Ember-2 API is running"}` instead.

---

### Step 12 — Access Ember from Your Phone (Tailscale)

Skip this step if you are not using Tailscale.

1. Install Tailscale on your phone:
   - **iPhone:** Search "Tailscale" in the App Store
   - **Android:** Search "Tailscale" in Google Play
2. Sign in with the same account you used on your desktop
3. Tap **Connect**

**Enable MagicDNS and HTTPS (recommended):**

4. Log in at [login.tailscale.com](https://login.tailscale.com) → go to **DNS** → enable **MagicDNS**
5. Go to your machine in the Tailscale admin panel → enable **HTTPS**
6. On your desktop, run:
```
tailscale serve --bg http://localhost:8000
```
7. Open a browser on your phone and go to:
```
https://your-machine-name.your-tailnet.ts.net
```
You should see the Ember UI.

**Or use the IP directly (simpler, no HTTPS):**

Open a browser on your phone and go to:
```
http://100.x.x.x:8000
```
Replace `100.x.x.x` with your Tailscale IP.

> Both devices need to have Tailscale running and connected. Check the Tailscale app on your phone — it should list your desktop machine as a connected device.

---

## Verify Everything Is Working

Open http://localhost:8000 in a browser. If Ember responds with context and personality when you send a message, setup is complete.

If something is not working:

- **Blank page at localhost:8000** — make sure the `ui/` folder is populated (Step 10)
- **API won't start** — make sure your virtual environment is activated and `.env` has the correct vault path
- **No web results** — check that Docker is running and `docker ps` shows `ember-searxng`
- **Slow first response** — normal. The embedding model loads on first use and caches after that.

---

## Optional: HTTPS via Tailscale

Once Ember is running with Tailscale, you can access it via a proper HTTPS address with a real security certificate. This is covered in Step 12 above. The command is:

```
tailscale serve --bg http://localhost:8000
```

This makes Ember available at `https://your-machine-name.your-tailnet.ts.net` from any device on your Tailscale network.

---

## What to Do Next

Once Ember is running:

- Write journal entries: `POST /journal` or use the journal script at `scripts/journal.py`
- Run a daily reflection: `python -m src.reflection.run_daily_reflection`
- Ingest a ChatGPT export: `python scripts/import_chatgpt.py`
- Check the vault health: `python scripts/audit_memory.py`
- Explore retrieved context for a query: `GET /debug-context?message=your+question`

For a full reference of what Ember can do, see [docs/Ember2_TDD.md](docs/Ember2_TDD.md).

---

## Something Not Working?

Report bugs and issues at [github.com/niansahc/ember-2/issues](https://github.com/niansahc/ember-2/issues).
