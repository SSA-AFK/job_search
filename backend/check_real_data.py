"""Check actual data from recently collected companies."""

import sqlite3


def main() -> None:
    conn = sqlite3.connect("d:/tools_dev/company_search/backend/company_search.db")
    c = conn.cursor()

    # Check companies with last_collected_at (recently collected)
    c.execute("SELECT COUNT(*) FROM companies WHERE last_collected_at IS NOT NULL")
    print(f"Companies with last_collected_at: {c.fetchone()[0]}")

    c.execute(
        "SELECT canonical_name, city, industry, funding_stage, scale, "
        "website IS NOT NULL as has_web, description IS NOT NULL as has_desc, "
        "last_collected_at FROM companies WHERE last_collected_at IS NOT NULL LIMIT 15"
    )
    print("\nRecently collected companies:")
    for row in c.fetchall():
        print(f"  {row}")

    # Check companies with any structured field filled
    c.execute(
        "SELECT COUNT(*) FROM companies WHERE city IS NOT NULL "
        "OR industry IS NOT NULL OR funding_stage != 'unknown' OR scale != 'unknown'"
    )
    print(f"\nCompanies with ANY structured field: {c.fetchone()[0]}")

    # Job postings with company name
    c.execute(
        "SELECT COUNT(DISTINCT jp.company_id) FROM job_postings jp"
    )
    print(f"\nDistinct companies with jobs: {c.fetchone()[0]}")

    # Job sources (multiple data sources?)
    print("\n--- Job Sources table ---")
    c.execute("SELECT COUNT(*) FROM job_sources")
    print(f"Total job_sources: {c.fetchone()[0]}")
    c.execute("SELECT provider, COUNT(*) FROM job_sources GROUP BY provider")
    for row in c.fetchall():
        print(f"  {row}")

    # job_entries
    print("\n--- Job Entries table ---")
    c.execute("SELECT COUNT(*) FROM job_entries")
    print(f"Total job_entries: {c.fetchone()[0]}")
    c.execute("PRAGMA table_info(job_entries)")
    print("job_entries columns:")
    for row in c.fetchall():
        print(f"  {row}")

    # Source documents by provider
    print("\n--- Source Documents by provider ---")
    c.execute("SELECT provider, COUNT(*) FROM source_documents GROUP BY provider")
    for row in c.fetchall():
        print(f"  {row}")

    # Check successful crawl runs and their companies
    print("\n--- Successful crawl runs ---")
    c.execute(
        "SELECT cr.normalized_query, cr.status, cr.jobs_found, cr.documents_found, "
        "cr.company_id FROM crawl_runs cr WHERE cr.status = 'succeeded' LIMIT 10"
    )
    cols = [d[0] for d in c.description]
    print(f"Columns: {cols}")
    for row in c.fetchall():
        print(f"  {row}")

    # Check crawl_runs schema
    c.execute("PRAGMA table_info(crawl_runs)")
    print("\ncrawl_runs columns:")
    for row in c.fetchall():
        print(f"  {row}")

    conn.close()


if __name__ == "__main__":
    main()
