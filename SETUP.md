# SETUP.md — Ember-2 Setup Guide

This guide walks you through setting up Ember-2 from scratch. It assumes you are comfortable running commands in a terminal but does not assume a software development background.

---

## What You Are Setting Up

- **Ember API** — the core backend (FastAPI, runs locally)
- **Ollama** — runs the local language model on your hardware
- **SearXNG** — a private web search engine running in Docker (used for web-aware queries)
- **Open WebUI** — a chat interface that connects to Ember

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

---

## Setup Steps

### Step 1 — Clone the Repository

Downloads the Ember-2 codebase to your machine.

```
git clone https://github.com/your-username/ember-2.git
cd ember-2
```

> **Note:** The URL above is a placeholder. Update it with the actual repository URL, or if you received Ember-2 directly, extract the folder and open a terminal inside it instead.

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
ollama pull qwen2.5:14b
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

**Windows:**
```
mkdir C:\EmberVault
```

**Mac/Linux:**
```
mkdir -p /data/embervault
```

> Choose a location on a local drive, not a cloud-synced folder (not OneDrive, iCloud, or Dropbox). This keeps your private data off external servers.

---

### Step 6 — Configure Your Environment

Creates your personal configuration file from the provided template.

```
cp .env.example .env
```

Open `.env` in any text editor (Notepad, TextEdit, VS Code) and set:

```
PRIVATE_VAULT_PATH=C:/EmberVault
EMBER_MODEL=qwen2.5:14b
EMBER_VISION_MODEL=llama3.2-vision:11b
```

Adjust the vault path to wherever you created it in Step 5.

> `.env` is gitignored. It will never be committed to the repository.

---

### Step 7 — Seed Your Identity Profile

Tells Ember who you are so she can personalize responses from the start. This step copies the template to your vault, lets you fill in your information, then writes it to memory.

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

### Step 8 — Start SearXNG (Web Search)

Starts the private web search engine Ember uses for web-aware queries. Runs locally in Docker and is not accessible from outside your machine.

```
docker compose up -d
```

Verify it is running:
```
docker ps
```

You should see `ember-searxng` in the list.

---

### Step 9 — Set Your API Key

Generates a secure API key and stores it in Windows Credential Manager — not in any file. This key protects the Ember API from unauthorized access.

```
python scripts/set_api_key.py
```

The script will display your key once. **Copy it immediately** — you will need it in Step 11. If you lose it, you can retrieve it from Windows Credential Manager (search for "Credential Manager" in the Start menu → Windows Credentials → look for `ember-2`) or run the script again to rotate to a new key.

---

### Step 10 — Start the Ember API

Starts the Ember backend server. Run this from the repo root with your virtual environment activated.

**Windows (recommended):**
```
start_api.bat
```

**Or directly:**
```
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

> If you plan to access Ember from another device (phone, tablet) via Tailscale, see the **Mobile Access** section at the bottom of this guide before starting — the host binding needs to match your Tailscale IP.

Verify the API is running by opening a browser and navigating to:
```
http://127.0.0.1:8000/
```

You should see: `{"message": "Ember-2 API is running"}`

---

### Step 11 — Connect Open WebUI

Open WebUI is the chat interface you will use to talk to Ember. Install and run it via Docker:

```
docker run -d -p 3000:8080 --add-host=host.docker.internal:host-gateway \
  -e WEBUI_AUTH=true \
  -v open-webui:/app/backend/data \
  --name open-webui --restart always \
  ghcr.io/open-webui/open-webui:main
```

Then open a browser and go to `http://localhost:3000`. Create an account (local only — no external sign-up).

**Configure the Ember connection:**

1. Go to **Settings → Connections**
2. Set the **OpenAI API Base URL** to: `http://host.docker.internal:8000/v1`
3. Set the **API Key** to the key you copied in Step 9
4. Click **Save**

You should see Ember models appear in the model selector. Select one and start a conversation.

---

### Step 12 — Configure Open WebUI Settings

Disables Open WebUI's built-in retrieval and query rewriting features so they do not interfere with Ember's own retrieval pipeline.

**Settings → Documents**

- **Bypass Embedding and Retrieval** → turn **On**
  Prevents Open WebUI from embedding and searching documents itself — Ember handles all retrieval from the memory vault.

**Settings → Interface**

- **Retrieval Query Generation** → turn **Off**
  Stops Open WebUI from rewriting your messages into retrieval queries before they reach Ember, which would corrupt intent classification and context assembly.

- **Web Search Query Generation** → turn **Off**
  Stops Open WebUI from injecting its own web search queries — Ember uses SearXNG directly and manages web search internally.

---

## Verify Everything Is Working

Send a message in Open WebUI. If Ember responds with context and personality, setup is complete.

If something is not working:

- **No models in Open WebUI** — check that the API URL and key are correct in Settings → Connections
- **API won't start** — make sure your virtual environment is activated and `.env` has the correct vault path
- **No web results** — check that Docker is running and `docker ps` shows `ember-searxng`
- **Slow first response** — normal. The embedding model loads on first use and caches after that.

---

## Optional: Mobile Access via Tailscale

Tailscale lets you access Ember securely from your phone or any other device, with encrypted WireGuard tunneling and no port forwarding required.

### On Your Desktop

1. Download and install Tailscale from [tailscale.com/download](https://tailscale.com/download)
2. Sign in and connect — note your Tailscale IP address (shown in the Tailscale app, e.g. `100.x.x.x`)
3. Open `start_api.bat` in a text editor and change `--host 127.0.0.1` to `--host 100.x.x.x` (your Tailscale IP)
4. Restart the API

### On Your Phone

1. Install Tailscale from the App Store or Google Play
2. Sign in with the same account as your desktop
3. Enable Tailscale on your phone
4. Open a browser and go to `http://100.x.x.x:8000/` — you should see the Ember health check

### Optional: HTTPS via Tailscale Serve

Tailscale can provide HTTPS with a real TLS certificate for your Ember instance:

1. In the Tailscale admin console, enable **HTTPS Certificates** for your machine
2. Run: `tailscale serve --https=443 http://127.0.0.1:8000`
3. Access Ember at: `https://your-machine-name.tail-hash.ts.net`

Configure Open WebUI on your phone using this HTTPS URL instead of the IP.

---

## What to Do Next

Once Ember is running:

- Write journal entries: `POST /journal` or use the journal script at `scripts/journal.py`
- Run a daily reflection: `python -m src.reflection.run_daily_reflection`
- Ingest a ChatGPT export: `python scripts/import_chatgpt.py`
- Check the vault health: `python scripts/audit_memory.py`
- Explore retrieved context for a query: `GET /debug-context?message=your+question`

For a full reference of what Ember can do, see [docs/Ember2_TDD.md](docs/Ember2_TDD.md).
