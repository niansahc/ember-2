# Backup and Export Guide

Your data lives on your machine. Nobody else has a copy. That means backups are on you.

This guide covers what to back up, how to do it, and how to get your data out of Ember if you ever need to.

---

## What to back up

The only thing that truly matters is your vault. Everything else is rebuildable.

### Must back up

- **`private_vault/memory/`** — all your canonical records. Conversations, journal entries, reflections, state, profile, projects. These are plain JSON files. If you lose these, you lose everything Ember knows.
- **`.env`** — your vault path, model settings, host configuration. Not the API key (that's in Credential Manager), but you need this file to tell Ember where to find everything.
- **`config/constitution.yaml`** — only if you've customized it. The default ships with the repo.

### Good to back up

- **`private_vault/embeddings/`** — vector indexes. These are rebuildable from your memory files, but rebuilding takes time (minutes for a large vault). Faster to just include them in the backup.
- **`private_vault/imports/`** — original source files you uploaded or imported (ChatGPT exports, PDFs, documents). The chunked versions live in memory/, but if you want the originals, back these up too.

### Do NOT need to back up

- **The `ember-2` code repo** — just clone it again from GitHub.
- **`private_vault/embeddings/`** if storage is tight — Ember rebuilds them on first use after they're missing. First startup will be slow.
- **`logs/`** — audit logs and safety review logs. Useful for debugging but not essential data.
- **`ui/`** — the built frontend. Rebuilt from ember-2-ui on install.

---

## Backup methods

### 1. Simple copy

The vault is plain files. Copy the folder.

**Windows:**
```
robocopy C:\EmberVault D:\Backups\EmberVault /MIR
```

**Mac/Linux:**
```
rsync -av /data/embervault/ /mnt/backup/embervault/
```

Run this on a schedule (weekly, or after major conversations). The vault is append-only, so a mirror copy is safe and fast after the first run.

### 2. External drive

Copy your vault folder to a USB drive or external hard drive periodically. Label it. Keep it somewhere safe. Low tech, high reliability.

### 3. Encrypted cloud storage

If you want offsite backup without exposing your data:

1. Install [Cryptomator](https://cryptomator.org/) (free, open source)
2. Create an encrypted vault on your cloud storage (OneDrive, Google Drive, Dropbox)
3. Copy your Ember vault into the Cryptomator vault
4. Files are encrypted before they leave your machine

**Never sync your raw vault folder directly to a cloud provider.** Your memories, journal entries, and conversations would be readable by anyone with access to that cloud account.

---

## Rebuilding indexes after a restore

If you restore a vault backup to a new machine or location and the embeddings are missing:

```bash
# Delete any stale index files (they may reference old paths)
# Then restart the API — indexes rebuild on first use
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

The first startup after index deletion will be slow. Ember needs to re-embed all your memory records. For a vault with ~16K records, expect a few minutes.

A dedicated `tools/rebuild_indexes.py` script is planned but not yet implemented. See TDD section 10.4 for context on the storage evolution plan.

---

## Exporting your data

Your data is already in an open format. The vault is plain JSON files organized by type.

### See what you have

```bash
python scripts/audit_memory.py
```

This gives you an inventory of all record types, counts, and health status.

### Export a specific type

Just copy the folder:

```bash
# Export all journal entries
cp -r private_vault/memory/journal/ ~/Desktop/ember-journal-export/

# Export all conversations
cp -r private_vault/memory/conversation/ ~/Desktop/ember-conversations/
```

### Read your data without Ember

Every record is a standalone JSON file with these fields:

```json
{
  "id": "2026-03-28T15-30-00-123456",
  "timestamp": "2026-03-28T15:30:00.123456",
  "type": "conversation",
  "text": "The actual content of this record",
  "source": "chat",
  "tags": ["conversation"],
  "metadata": { "role": "user", "session_id": "sess_abc123" }
}
```

No proprietary format. No binary encoding. No vendor lock-in. Your data is yours and always readable without Ember running.

---

## Frequency recommendations

- **Daily conversations:** back up weekly
- **After major life events or heavy journaling:** back up immediately
- **Before any system migration:** back up the full vault + .env
- **Before running cleanup or suppression scripts:** back up first (they're append-only but better safe)
