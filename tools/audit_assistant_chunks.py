import sqlite3
import json
import textwrap
import sys
import os

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

db_path = os.path.join(
    os.environ.get("PRIVATE_VAULT_PATH", ""),
    "embeddings",
    "ingested.db",
)

if not os.path.exists(db_path):
    # fallback: load from config
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from src.core.config import get_private_vault_path
    db_path = os.path.join(str(get_private_vault_path()), "embeddings", "ingested.db")

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Total count
cur.execute("""
    SELECT COUNT(*)
    FROM vectors
    WHERE json_extract(metadata, '$.content_kind') = 'assistant_content'
      AND (quality IS NULL OR quality = 'ok')
""")
total = cur.fetchone()[0]
print(f"Total assistant_content rows (quality=ok): {total}\n")
print("=" * 70)

# Length distribution
cur.execute("""
    SELECT
        SUM(CASE WHEN length(text) < 100 THEN 1 ELSE 0 END) AS under_100,
        SUM(CASE WHEN length(text) BETWEEN 100 AND 300 THEN 1 ELSE 0 END) AS "100-300",
        SUM(CASE WHEN length(text) BETWEEN 301 AND 800 THEN 1 ELSE 0 END) AS "301-800",
        SUM(CASE WHEN length(text) > 800 THEN 1 ELSE 0 END) AS over_800
    FROM vectors
    WHERE json_extract(metadata, '$.content_kind') = 'assistant_content'
      AND (quality IS NULL OR quality = 'ok')
""")
row = cur.fetchone()
print("Length distribution:")
print(f"  < 100 chars : {row[0]}")
print(f"  100–300     : {row[1]}")
print(f"  301–800     : {row[2]}")
print(f"  > 800 chars : {row[3]}")
print()

# 10 random samples
cur.execute("""
    SELECT id, text
    FROM vectors
    WHERE json_extract(metadata, '$.content_kind') = 'assistant_content'
      AND (quality IS NULL OR quality = 'ok')
    ORDER BY RANDOM()
    LIMIT 10
""")
rows = cur.fetchall()
print("10 random samples:")
print("=" * 70)
for i, (rid, text) in enumerate(rows, 1):
    fname = rid.replace("\\", "/").split("/")[-1]
    snippet = textwrap.shorten(text.strip(), width=350, placeholder=" [...]")
    print(f"[{i}] {fname}  (len={len(text)})")
    print(snippet)
    print()

conn.close()
