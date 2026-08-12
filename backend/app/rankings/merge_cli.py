"""Merge the calibrated AI pilot into the job database and add JobHunt companies."""

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

JOBHUNT_COMPANIES = (
    "DeepSeek（深度求索）", "MiniMax", "京东", "华为", "哔哩哔哩", "大疆", "小米",
    "小红书", "得物", "快手", "携程", "智谱AI", "月之暗面", "百度", "米哈游",
    "网易", "美团", "腾讯", "蚂蚁集团", "钉钉",
)

_COPY_TABLES = (
    "source_documents", "ranking_pilots", "companies", "company_sources",
    "company_profile_fields", "ranking_pilot_members", "ranking_collection_runs",
    "company_ranking_signals", "company_ranking_snapshots",
    "company_ranking_snapshot_evidence",
)


def merge(target_path: Path, source_path: Path) -> dict[str, object]:
    backup = target_path.with_name(f"{target_path.stem}.before-ranking-merge{target_path.suffix}")
    shutil.copy2(target_path, backup)
    db = sqlite3.connect(target_path)
    db.execute("pragma foreign_keys=on")
    db.execute("attach database ? as pilot_source", (str(source_path),))
    try:
        db.execute("begin immediate")
        _copy_source_documents(db)
        _copy_companies(db)
        _copy_ranking_data(db)
        pilot_id = db.execute(
            "select id from ranking_pilots where industry='ai' order by created_at desc limit 1"
        ).fetchone()[0]
        added = _add_jobhunt_members(db, pilot_id)
        db.execute(
            "update ranking_pilots set sample_size=(select count(*) from ranking_pilot_members where pilot_id=?) where id=?",
            (pilot_id, pilot_id),
        )
        db.commit()
        ranked = db.execute(
            "select count(*) from company_ranking_snapshots where pilot_id=? and rule_version='ai-long-term-v2' and is_eligible=1",
            (pilot_id,),
        ).fetchone()[0]
        members = db.execute(
            "select count(*) from ranking_pilot_members where pilot_id=?", (pilot_id,)
        ).fetchone()[0]
        return {
            "backup": str(backup), "pilot_id": pilot_id, "members": members,
            "ranked": ranked, "observation": members - ranked, "jobhunt_members_added": added,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _columns(db: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in db.execute(f'pragma {schema}.table_info("{table}")')]


def _copy_source_documents(db: sqlite3.Connection) -> None:
    columns = [name for name in _columns(db, "main", "source_documents") if name in _columns(db, "pilot_source", "source_documents")]
    names = ",".join(f'"{name}"' for name in columns)
    db.execute(f"insert or ignore into source_documents ({names}) select {names} from pilot_source.source_documents")


def _copy_companies(db: sqlite3.Connection) -> None:
    columns = [name for name in _columns(db, "main", "companies") if name in _columns(db, "pilot_source", "companies")]
    names = ",".join(f'"{name}"' for name in columns)
    db.execute(f"insert or ignore into companies ({names}) select {names} from pilot_source.companies")
    db.execute("drop table if exists temp.company_map")
    db.execute("create temp table company_map(source_id text primary key, target_id text not null)")
    db.execute(
        "insert into company_map select s.id,t.id from pilot_source.companies s join companies t on t.normalized_name=s.normalized_name"
    )


def _copy_ranking_data(db: sqlite3.Connection) -> None:
    db.execute("insert or ignore into ranking_pilots select * from pilot_source.ranking_pilots")
    db.execute("insert or ignore into company_sources (company_id,source_document_id,covered_fields,field_verification,confidence) select m.target_id,cs.source_document_id,cs.covered_fields,cs.field_verification,cs.confidence from pilot_source.company_sources cs join company_map m on m.source_id=cs.company_id")
    db.execute("insert or ignore into company_profile_fields (company_id,field_key,value,source_document_id,verification_status,collected_at) select m.target_id,p.field_key,p.value,p.source_document_id,p.verification_status,p.collected_at from pilot_source.company_profile_fields p join company_map m on m.source_id=p.company_id")
    db.execute("insert or ignore into ranking_pilot_members (id,pilot_id,company_id,source_row,source_identity_hash,stratum,selection_reason,company_size,established_at,insured_employee_count,employee_report_year) select r.id,r.pilot_id,m.target_id,r.source_row,r.source_identity_hash,r.stratum,r.selection_reason,r.company_size,r.established_at,r.insured_employee_count,r.employee_report_year from pilot_source.ranking_pilot_members r join company_map m on m.source_id=r.company_id")
    db.execute("insert or ignore into ranking_collection_runs (id,pilot_id,company_id,category,run_key,status,logical_call_count,tool_call_count,error_code,response_sha256,started_at,finished_at) select r.id,r.pilot_id,m.target_id,r.category,r.run_key,r.status,r.logical_call_count,r.tool_call_count,r.error_code,r.response_sha256,r.started_at,r.finished_at from pilot_source.ranking_collection_runs r join company_map m on m.source_id=r.company_id")
    db.execute("insert or ignore into company_ranking_signals (id,company_id,source_document_id,category,signal_key,value,event_date,source_fingerprint,response_sha256,confidence,verification_status,fetched_at,expires_at) select r.id,m.target_id,r.source_document_id,r.category,r.signal_key,r.value,r.event_date,r.source_fingerprint,r.response_sha256,r.confidence,r.verification_status,r.fetched_at,r.expires_at from pilot_source.company_ranking_signals r join company_map m on m.source_id=r.company_id")
    db.execute("insert or ignore into company_ranking_snapshots (id,pilot_id,company_id,industry,rule_version,total_score,component_scores,raw_component_scores,stage_percentiles,evidence_coverage,company_stage,missing_fields,eligibility_reasons,is_eligible,calculated_at) select r.id,r.pilot_id,m.target_id,r.industry,r.rule_version,r.total_score,r.component_scores,r.raw_component_scores,r.stage_percentiles,r.evidence_coverage,r.company_stage,r.missing_fields,r.eligibility_reasons,r.is_eligible,r.calculated_at from pilot_source.company_ranking_snapshots r join company_map m on m.source_id=r.company_id")
    db.execute("insert or ignore into company_ranking_snapshot_evidence (snapshot_id,source_document_id) select e.snapshot_id,e.source_document_id from pilot_source.company_ranking_snapshot_evidence e join company_ranking_snapshots s on s.id=e.snapshot_id")


def _add_jobhunt_members(db: sqlite3.Connection, pilot_id: str) -> int:
    now = datetime.now(UTC).isoformat()
    added = 0
    start_row = db.execute("select coalesce(max(source_row),2)+1 from ranking_pilot_members where pilot_id=?", (pilot_id,)).fetchone()[0]
    for offset, name in enumerate(JOBHUNT_COMPANIES):
        row = db.execute("select id from companies where canonical_name=?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"required JobHunt company is missing: {name}")
        company_id = row[0]
        exists = db.execute("select 1 from ranking_pilot_members where pilot_id=? and company_id=?", (pilot_id, company_id)).fetchone()
        if exists:
            continue
        identity_hash = hashlib.sha256(f"jobhunt:{name}".encode()).hexdigest()
        db.execute(
            "insert into ranking_pilot_members (id,pilot_id,company_id,source_row,source_identity_hash,stratum,selection_reason) values (lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-a'||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))),?,?,?,?,?,?)",
            (pilot_id, company_id, start_row + offset, identity_hash, "jobhunt_addition|pending_stage", "approved_jobhunt_company"),
        )
        db.execute(
            "insert into company_ranking_snapshots (id,pilot_id,company_id,industry,rule_version,total_score,component_scores,raw_component_scores,stage_percentiles,evidence_coverage,company_stage,missing_fields,eligibility_reasons,is_eligible,calculated_at) values (lower(hex(randomblob(4)))||'-'||lower(hex(randomblob(2)))||'-4'||substr(lower(hex(randomblob(2))),2)||'-a'||substr(lower(hex(randomblob(2))),2)||'-'||lower(hex(randomblob(6))),?,?, 'ai','ai-long-term-v2',0,?,?,?,?,?,?,?,?,?)",
            (pilot_id, company_id, json.dumps({"ai_core":0,"market_validation":0,"growth_momentum":0,"industry_influence":0,"reliability":0}), "{}", "{}", json.dumps({"ai_core":False,"market_validation":False,"growth_momentum":False,"industry_influence":False,"reliability":False}), "growth", json.dumps(["ai.core_level","ai.market_proofs","ai.growth_events","ai.technology_signals"]), json.dumps(["missing_automatic_ai_relevance_evidence","insufficient_component_coverage"]), 0, now),
        )
        added += 1
    return added


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(merge(args.target, args.source), ensure_ascii=False))


if __name__ == "__main__":
    main()
