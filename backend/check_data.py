"""Quick data inventory for dashboard visualization."""

import sqlite3


def main() -> None:
    conn = sqlite3.connect("d:/tools_dev/company_search/backend/company_search.db")
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print("Tables:")
    for row in c.fetchall():
        print(f"  {row[0]}")

    print()
    for table in [
        "companies",
        "job_postings",
        "job_posting_entries",
        "company_filings",
        "company_field_evidence",
        "crawl_runs",
        "collection_requests",
    ]:
        try:
            c.execute(f"SELECT COUNT(*) FROM {table}")
            print(f"{table}: {c.fetchone()[0]}")
        except Exception as e:
            print(f"{table}: {e}")

    print("\nCompanies with city:")
    c.execute("SELECT COUNT(*) FROM companies WHERE city IS NOT NULL")
    print(f"  {c.fetchone()[0]}")

    print("\nCompanies by city:")
    c.execute(
        "SELECT city, COUNT(*) FROM companies WHERE city IS NOT NULL GROUP BY city ORDER BY COUNT(*) DESC LIMIT 15"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nCompanies by funding_stage:")
    c.execute(
        "SELECT funding_stage, COUNT(*) FROM companies GROUP BY funding_stage ORDER BY COUNT(*) DESC"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nCompanies by scale:")
    c.execute(
        "SELECT scale, COUNT(*) FROM companies GROUP BY scale ORDER BY COUNT(*) DESC"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nCompanies by industry:")
    c.execute(
        "SELECT industry, COUNT(*) FROM companies WHERE industry IS NOT NULL GROUP BY industry ORDER BY COUNT(*) DESC LIMIT 15"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nCrawl runs by status:")
    c.execute(
        "SELECT status, COUNT(*) FROM crawl_runs GROUP BY status ORDER BY COUNT(*) DESC"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nSample companies with full data:")
    c.execute(
        "SELECT canonical_name, city, funding_stage, scale, industry FROM companies WHERE city IS NOT NULL LIMIT 10"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\n--- Job Postings ---")
    c.execute("SELECT COUNT(*) FROM job_postings")
    print(f"Total jobs: {c.fetchone()[0]}")

    c.execute("SELECT COUNT(*) FROM job_postings WHERE city IS NOT NULL AND city != ''")
    print(f"Jobs with city: {c.fetchone()[0]}")

    c.execute("SELECT COUNT(*) FROM job_postings WHERE salary_min_monthly IS NOT NULL")
    print(f"Jobs with salary: {c.fetchone()[0]}")

    print("\nJobs by city:")
    c.execute(
        "SELECT city, COUNT(*) FROM job_postings WHERE city IS NOT NULL AND city != '' GROUP BY city ORDER BY COUNT(*) DESC LIMIT 15"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nJobs by type:")
    c.execute("SELECT job_type, COUNT(*) FROM job_postings GROUP BY job_type")
    for row in c.fetchall():
        print(f"  {row}")

    print("\nSample jobs:")
    c.execute(
        "SELECT title, city, job_type, salary_min_monthly, salary_max_monthly, salary_months FROM job_postings LIMIT 10"
    )
    for row in c.fetchall():
        print(f"  {row}")

    print("\nCompanies with description:")
    c.execute("SELECT COUNT(*) FROM companies WHERE description IS NOT NULL")
    print(f"  {c.fetchone()[0]}")

    print("\nCompanies with website:")
    c.execute("SELECT COUNT(*) FROM companies WHERE website IS NOT NULL")
    print(f"  {c.fetchone()[0]}")

    conn.close()


if __name__ == "__main__":
    main()
