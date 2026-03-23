"""
scripts/repoint_vault_paths.py

One-time migration: updates file_path values in all *_index.json files
and ingested.db after the vault was moved from the old OneDrive location
to C:\EmberVault.

Old: C:\\Users\\<username>\\OneDrive\\Desktop\\Ember-2\\private_vault
New: C:\\EmberVault

Safe to re-run — entries that already point to the new path are skipped.
"""

import json
import sqlite3
from pathlib import Path

OLD = r"C:\Users\<username>\OneDrive\Desktop\Ember-2\private_vault"  # replace with your old vault path
NEW = r"C:\EmberVault"
EMBEDDINGS = Path(r"C:\EmberVault\embeddings")


def fix_json_index(path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for entry in data:
        fp = entry.get("file_path", "")
        if OLD in fp:
            entry["file_path"] = fp.replace(OLD, NEW)
            count += 1
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return count


def fix_sqlite(path: Path) -> int:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    # file_path is stored inside the metadata JSON blob, not a top-level column
    cur.execute(
        "UPDATE vectors SET metadata = REPLACE(metadata, ?, ?) WHERE metadata LIKE ?",
        (OLD, NEW, "%" + OLD + "%"),
    )
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


if __name__ == "__main__":
    total = 0

    for json_file in sorted(EMBEDDINGS.glob("*_index.json")):
        n = fix_json_index(json_file)
        print(f"  {json_file.name}: {n} paths updated")
        total += n

    db = EMBEDDINGS / "ingested.db"
    if db.exists():
        n = fix_sqlite(db)
        print(f"  ingested.db:   {n} rows updated")
        total += n

    print(f"\nTotal: {total} paths updated")
