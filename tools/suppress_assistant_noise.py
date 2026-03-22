import sqlite3
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

db = os.path.join(
    r"C:\Users\<username>\OneDrive\Desktop\Ember-2\private_vault",
    "embeddings",
    "ingested.db",
)
conn = sqlite3.connect(db)
cur = conn.cursor()

base = (
    "json_extract(metadata, '$.content_kind') = 'assistant_content' "
    "AND (quality IS NULL OR quality = 'ok')"
)

categories = [
    (
        "(1) JSON / citeturn",
        f"""UPDATE vectors SET quality = 'suppressed'
            WHERE {base}
            AND (lower(text) LIKE '{{%'
                 OR lower(text) LIKE '%{{"queries":%'
                 OR lower(text) LIKE '%citeturn%')""",
    ),
    (
        "(2) Tool narration",
        f"""UPDATE vectors SET quality = 'suppressed'
            WHERE {base}
            AND (lower(text) LIKE '%let me try%'
                 OR lower(text) LIKE "%i'll use a tool%"
                 OR lower(text) LIKE '%let me use%'
                 OR lower(text) LIKE "%let's proceed with%")""",
    ),
    (
        "(3) Warmth filler",
        f"""UPDATE vectors SET quality = 'suppressed'
            WHERE {base}
            AND (lower(text) LIKE '%rest easy%'
                 OR lower(text) LIKE '%talk soon%'
                 OR lower(text) LIKE "%you've done enough%"
                 OR lower(text) LIKE "%i'm always here%"
                 OR lower(text) LIKE '%take care of yourself%')""",
    ),
    (
        "(4) Under 100 chars",
        f"""UPDATE vectors SET quality = 'suppressed'
            WHERE {base}
            AND length(text) < 100""",
    ),
    (
        "(5) AI self-reference",
        f"""UPDATE vectors SET quality = 'suppressed'
            WHERE {base}
            AND (lower(text) LIKE '%as an ai%'
                 OR lower(text) LIKE '%as an artificial%'
                 OR lower(text) LIKE '%i get to experience%')""",
    ),
]

total = 0
for label, sql in categories:
    cur.execute(sql)
    n = cur.rowcount
    total += n
    print(f"{label}: {n} rows suppressed")

conn.commit()
conn.close()
print(f"\nTotal suppressed this run: {total}")
