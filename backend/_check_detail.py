import sqlite3
conn = sqlite3.connect("company_search.db", timeout=30)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
for (name,) in cur.fetchall():
    cur.execute(f'SELECT COUNT(*) FROM "{name}"')
    print(f"  {name}: {cur.fetchone()[0]}")
conn.close()