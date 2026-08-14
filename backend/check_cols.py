import sqlite3
c = sqlite3.connect('company_search.db')
for r in c.execute("PRAGMA table_info(companies)").fetchall():
    print(r)
