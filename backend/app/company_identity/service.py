"""Transactional recording and application of company identity reviews."""

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from threading import Lock
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import ValidationError
from sqlalchemy import ColumnElement, String, cast, func, select, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import (
    DataError,
    IntegrityError,
    OperationalError,
    SQLAlchemyError,
    StatementError,
)
from sqlalchemy.orm import Session

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityReviewDraft,
    IdentityReviewAction,
    IdentityReviewApplySummary,
    IdentityReviewDecisionInput,
    IdentityReviewItem,
    IdentityReviewRecordSummary,
    IdentityReviewStatus,
    PublicEvidenceReference,
)
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.core.normalization import normalize_name
from app.models import Company, CompanyAlias, CrawlRun, FilingType, RegulatoryFiling

_IDENTITY_LOCK_PREFIX = b"company_search:company_identity:v1\0"
_FILING_IDENTITY_LOCK_PREFIX = b"company_search:regulatory_filing:v1\0"
_MAX_COMPANY_WEBSITE_LENGTH = 1_000
_MAX_COMPANY_CITY_LENGTH = 50
_LOCAL_LOCKS_GUARD = Lock()
_LOCAL_LOCKS: WeakKeyDictionary[object, dict[int, Lock]] = WeakKeyDictionary()


class _IdentityReviewError(ValueError):
    code = "identity_review_error"
    public_message = "identity review error"

    def __init__(self) -> None:
        super().__init__(self.public_message)


class IdentityReviewConflict(_IdentityReviewError):
    """The request is invalid or conflicts with immutable review history."""

    code = "identity_review_conflict"
    public_message = "identity review conflict"


class IdentityOwnerChanged(_IdentityReviewError):
    """The currently locked identity owner differs from the reviewed state."""

    code = "identity_owner_changed"
    public_message = "identity owner changed"


class IdentitySearchUnavailable(_IdentityReviewError):
    """Current identity ownership could not be checked safely."""

    code = "identity_search_unavailable"
    public_message = "identity search unavailable"


def record_identity_review(
    session: Session,
    *,
    crawl_run_id: UUID,
    draft: CompanyIdentityReviewDraft,
) -> IdentityReviewRecordSummary:
    """Persist one immutable public review snapshot in an owned transaction."""

    _require_clean_session(session)
    validated = _validate_draft(draft)
    expected = _review_storage_values(validated)
    try:
        with session.begin() as transaction, _serialized_identity_keys(
            session,
            (b"review\0" + validated.stable_identity_hash.encode("ascii"),),
        ):
            if session.get(CrawlRun, crawl_run_id) is None:
                raise IdentityReviewConflict
            existing = session.scalar(
                select(CompanyIdentityReviewItem)
                .where(
                    CompanyIdentityReviewItem.stable_identity_hash
                    == validated.stable_identity_hash
                )
                .with_for_update()
            )
            if existing is not None:
                if _stored_review_values(existing) != expected:
                    raise IdentityReviewConflict
                summary = _record_summary(existing, created=False)
            else:
                item = CompanyIdentityReviewItem(
                    stable_identity_hash=validated.stable_identity_hash,
                    first_crawl_run_id=crawl_run_id,
                    status=IdentityReviewStatus.PENDING,
                    candidate_name=validated.identity.canonical_name,
                    normalized_name=validated.identity.normalized_name,
                    aliases=list(validated.identity.aliases),
                    official_website=validated.identity.official_website,
                    recruitment_identity=validated.identity.recruitment_identity,
                    legal_identifiers=list(validated.identity.legal_identifiers),
                    city=validated.identity.city,
                    public_evidence_refs=_evidence_snapshot(
                        validated.identity.evidence
                    ),
                    candidate_matches=_match_snapshot(validated.candidate_matches),
                    review_reasons=[
                        reason.value for reason in validated.review_reasons
                    ],
                    created_at=validated.observed_at,
                    resolved_at=None,
                )
                session.add(item)
                session.flush()
                summary = _record_summary(item, created=True)
            transaction.commit()
            return summary
    except _IdentityReviewError:
        raise
    except OperationalError:
        raise IdentitySearchUnavailable from None
    except (DataError, IntegrityError, StatementError):
        raise IdentityReviewConflict from None
    except SQLAlchemyError:
        raise IdentitySearchUnavailable from None


def export_identity_review_queue(session: Session) -> tuple[IdentityReviewItem, ...]:
    """Return pending review items in stable creation and UUID order."""

    _require_clean_session(session)
    try:
        with session.begin():
            rows = tuple(
                session.scalars(
                    select(CompanyIdentityReviewItem)
                    .where(
                        CompanyIdentityReviewItem.status == IdentityReviewStatus.PENDING
                    )
                    .order_by(
                        CompanyIdentityReviewItem.created_at,
                        CompanyIdentityReviewItem.id,
                    )
                )
            )
            return tuple(_export_item(row) for row in rows)
    except _IdentityReviewError:
        raise
    except (SQLAlchemyError, ValidationError, TypeError, ValueError):
        raise IdentitySearchUnavailable from None


def apply_identity_review_decisions(
    session: Session,
    decisions: Sequence[IdentityReviewDecisionInput],
) -> IdentityReviewApplySummary:
    """Apply reviewed identity decisions atomically and append their audit rows."""

    _require_clean_session(session)
    validated = tuple(_validate_decision(decision) for decision in decisions)
    if not validated:
        return IdentityReviewApplySummary(applied=0, replayed=0)

    try:
        with session.begin() as transaction:
            items = _lock_review_items(session, validated)
            existing_decisions = _decisions_by_item(session, items)
            replayed, pending = _partition_replays(
                validated,
                items=items,
                existing=existing_decisions,
            )
            if not pending:
                return IdentityReviewApplySummary(applied=0, replayed=replayed)

            drafts = {item_id: _draft_from_row(items[item_id]) for item_id, _ in pending}
            _validate_company_write_bounds(pending, drafts)
            with _serialized_lock_keys(
                session,
                _decision_lock_keys(tuple(drafts.values())),
            ):
                companies = _lock_involved_companies(session, pending, items, drafts)
                _lock_involved_aliases(session, pending, companies, drafts)

                applied = 0
                for item_id, command in pending:
                    item = items[item_id]
                    draft = drafts[item_id]
                    resulting_company_id = _apply_one_decision(
                        session,
                        item=item,
                        command=command,
                        draft=draft,
                        companies=companies,
                    )
                    audit = CompanyIdentityReviewDecision(
                        review_item_id=item.id,
                        action=command.action,
                        target_company_id=command.target_company_id,
                        resulting_company_id=resulting_company_id,
                        reason=command.reason,
                        decided_at=command.decided_at,
                        decision_hash=_decision_hash(command),
                    )
                    session.add(audit)
                    session.flush()
                    existing_decisions[item.id] = audit
                    applied += 1
                summary = IdentityReviewApplySummary(
                    applied=applied,
                    replayed=replayed,
                )
                transaction.commit()
                return summary
    except _IdentityReviewError:
        raise
    except (DataError, IntegrityError, StatementError):
        raise IdentityOwnerChanged from None
    except SQLAlchemyError:
        raise IdentitySearchUnavailable from None


def _require_clean_session(session: Session) -> None:
    if session.in_transaction():
        raise IdentityReviewConflict


def _validate_company_write_bounds(
    pending: list[tuple[UUID, IdentityReviewDecisionInput]],
    drafts: dict[UUID, CompanyIdentityReviewDraft],
) -> None:
    company_write_actions = {
        IdentityReviewAction.CREATE_NEW,
        IdentityReviewAction.RENAME_CANONICAL,
    }
    for item_id, command in pending:
        if command.action not in company_write_actions:
            continue
        identity = drafts[item_id].identity
        if (
            identity.official_website is not None
            and len(identity.official_website) > _MAX_COMPANY_WEBSITE_LENGTH
        ) or (
            identity.city is not None
            and len(identity.city) > _MAX_COMPANY_CITY_LENGTH
        ):
            raise IdentityReviewConflict


def _identity_key_material(
    drafts: tuple[CompanyIdentityReviewDraft, ...],
) -> tuple[bytes, ...]:
    identities: set[bytes] = set()
    for draft in drafts:
        identity = draft.identity
        identities.update(
            _company_name_key_material(
                (identity.normalized_name, *identity.normalized_aliases)
            )
        )
        if identity.official_website is not None:
            identities.add(
                b"website\0" + identity.official_website.encode("utf-8")
            )
        if identity.recruitment_identity is not None:
            identities.add(
                b"recruitment\0" + identity.recruitment_identity.encode("utf-8")
            )
        identities.update(
            b"legal\0" + identifier.encode("utf-8")
            for identifier in identity.legal_identifiers
        )
    return tuple(identities)


def _company_name_key_material(names: Sequence[str]) -> tuple[bytes, ...]:
    normalized_names = sorted(
        {normalized for name in names if (normalized := normalize_name(name))}
    )
    return tuple(b"name\0" + name.encode("utf-8") for name in normalized_names)


def _identity_lock_keys(material: Sequence[bytes]) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                int.from_bytes(
                    sha256(_IDENTITY_LOCK_PREFIX + identity).digest()[:8],
                    byteorder="big",
                    signed=True,
                )
                for identity in material
            }
        )
    )


def _decision_lock_keys(
    drafts: tuple[CompanyIdentityReviewDraft, ...],
) -> tuple[int, ...]:
    identity_keys = _identity_lock_keys(_identity_key_material(drafts))
    legal_identifiers = {
        identifier
        for draft in drafts
        for identifier in draft.identity.legal_identifiers
    }
    filing_keys = tuple(
        int.from_bytes(
            sha256(
                _FILING_IDENTITY_LOCK_PREFIX
                + filing_type.value.encode("utf-8")
                + b"\0"
                + identifier.encode("utf-8")
            ).digest()[:8],
            byteorder="big",
            signed=True,
        )
        for filing_type, identifier in sorted(
            (
                (filing_type, identifier)
                for filing_type in FilingType
                for identifier in legal_identifiers
            ),
            key=lambda pair: (pair[0].value, pair[1]),
        )
    )
    return tuple(dict.fromkeys((*identity_keys, *filing_keys)))


@contextmanager
def _serialized_identity_keys(
    session: Session,
    material: Sequence[bytes],
) -> Iterator[None]:
    with _serialized_lock_keys(session, _identity_lock_keys(material)):
        yield


@contextmanager
def serialized_company_identity_names(
    session: Session,
    names: Sequence[str],
) -> Iterator[None]:
    """Serialize canonical and alias ownership using the review decision keys."""

    with _serialized_identity_keys(session, _company_name_key_material(names)):
        yield


@contextmanager
def _serialized_lock_keys(
    session: Session,
    lock_keys: Sequence[int],
) -> Iterator[None]:
    if session.get_bind().dialect.name == "postgresql":
        for lock_key in lock_keys:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
        yield
        return

    bind = session.get_bind()
    with _LOCAL_LOCKS_GUARD:
        locks_by_key = _LOCAL_LOCKS.setdefault(bind, {})
        locks = tuple(locks_by_key.setdefault(key, Lock()) for key in lock_keys)
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


def _validate_draft(draft: CompanyIdentityReviewDraft) -> CompanyIdentityReviewDraft:
    try:
        return CompanyIdentityReviewDraft.model_validate(draft.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise IdentityReviewConflict from None


def _validate_decision(
    decision: IdentityReviewDecisionInput,
) -> IdentityReviewDecisionInput:
    try:
        return IdentityReviewDecisionInput.model_validate(
            decision.model_dump(mode="python")
        )
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise IdentityReviewConflict from None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _evidence_snapshot(
    evidence: tuple[PublicEvidenceReference, ...],
) -> list[dict[str, object]]:
    return [
        {
            "provider": reference.provider,
            "url": reference.url,
            "evidence_id": reference.evidence_id,
            "confidence": _decimal_text(reference.confidence),
        }
        for reference in evidence
    ]


def _match_snapshot(
    matches: tuple[CompanyIdentityCandidateMatch, ...],
) -> list[dict[str, object]]:
    return [
        {
            "company_id": str(match.company_id),
            "canonical_name": match.canonical_name,
            "normalized_name": match.normalized_name,
            "match_kind": match.match_kind,
            "score": _decimal_text(match.score),
            "conflict_reasons": [reason.value for reason in match.conflict_reasons],
        }
        for match in matches
    ]


def _review_storage_values(draft: CompanyIdentityReviewDraft) -> tuple[object, ...]:
    identity = draft.identity
    return (
        identity.canonical_name,
        identity.normalized_name,
        list(identity.aliases),
        identity.official_website,
        identity.recruitment_identity,
        list(identity.legal_identifiers),
        identity.city,
        _evidence_snapshot(identity.evidence),
        _match_snapshot(draft.candidate_matches),
        [reason.value for reason in draft.review_reasons],
        draft.observed_at,
    )


def _stored_review_values(item: CompanyIdentityReviewItem) -> tuple[object, ...]:
    return (
        item.candidate_name,
        item.normalized_name,
        item.aliases,
        item.official_website,
        item.recruitment_identity,
        item.legal_identifiers,
        item.city,
        item.public_evidence_refs,
        item.candidate_matches,
        item.review_reasons,
        item.created_at,
    )


def _record_summary(
    item: CompanyIdentityReviewItem,
    *,
    created: bool,
) -> IdentityReviewRecordSummary:
    return IdentityReviewRecordSummary(
        review_item_id=item.id,
        stable_identity_hash=item.stable_identity_hash,
        status=item.status,
        first_crawl_run_id=item.first_crawl_run_id,
        created=created,
    )


def _draft_from_row(item: CompanyIdentityReviewItem) -> CompanyIdentityReviewDraft:
    try:
        return CompanyIdentityReviewDraft.model_validate(
            {
                "identity": {
                    "canonical_name": item.candidate_name,
                    "aliases": tuple(item.aliases),
                    "official_website": item.official_website,
                    "recruitment_identity": item.recruitment_identity,
                    "legal_identifiers": tuple(item.legal_identifiers),
                    "city": item.city,
                    "evidence": tuple(item.public_evidence_refs),
                },
                "candidate_matches": tuple(item.candidate_matches),
                "review_reasons": tuple(item.review_reasons),
                "observed_at": item.created_at,
            }
        )
    except (TypeError, ValueError, ValidationError):
        raise IdentityReviewConflict from None


def _export_item(item: CompanyIdentityReviewItem) -> IdentityReviewItem:
    return IdentityReviewItem(
        review_item_id=item.id,
        stable_identity_hash=item.stable_identity_hash,
        first_crawl_run_id=item.first_crawl_run_id,
        status=item.status,
        draft=_draft_from_row(item),
        created_at=item.created_at,
        resolved_at=item.resolved_at,
    )


def _lock_review_items(
    session: Session,
    decisions: tuple[IdentityReviewDecisionInput, ...],
) -> dict[UUID, CompanyIdentityReviewItem]:
    item_ids = sorted({decision.review_item_id for decision in decisions}, key=str)
    rows = tuple(
        session.scalars(
            select(CompanyIdentityReviewItem)
            .where(CompanyIdentityReviewItem.id.in_(item_ids))
            .order_by(CompanyIdentityReviewItem.id)
            .with_for_update()
        )
    )
    if len(rows) != len(item_ids):
        raise IdentityReviewConflict
    return {row.id: row for row in rows}


def _decisions_by_item(
    session: Session,
    items: dict[UUID, CompanyIdentityReviewItem],
) -> dict[UUID, CompanyIdentityReviewDecision]:
    rows = tuple(
        session.scalars(
            select(CompanyIdentityReviewDecision)
            .where(CompanyIdentityReviewDecision.review_item_id.in_(items))
            .order_by(CompanyIdentityReviewDecision.review_item_id)
        )
    )
    return {row.review_item_id: row for row in rows}


def _partition_replays(
    decisions: tuple[IdentityReviewDecisionInput, ...],
    *,
    items: dict[UUID, CompanyIdentityReviewItem],
    existing: dict[UUID, CompanyIdentityReviewDecision],
) -> tuple[int, list[tuple[UUID, IdentityReviewDecisionInput]]]:
    replayed = 0
    pending: list[tuple[UUID, IdentityReviewDecisionInput]] = []
    seen: dict[UUID, IdentityReviewDecisionInput] = {}
    for command in decisions:
        previous_input = seen.get(command.review_item_id)
        if previous_input is not None:
            if previous_input != command:
                raise IdentityReviewConflict
            replayed += 1
            continue
        seen[command.review_item_id] = command
        audit = existing.get(command.review_item_id)
        if audit is not None:
            if not _is_exact_decision_replay(audit, command):
                raise IdentityReviewConflict
            replayed += 1
            continue
        if items[command.review_item_id].status is not IdentityReviewStatus.PENDING:
            raise IdentityReviewConflict
        pending.append((command.review_item_id, command))
    return replayed, pending


def _decision_hash(command: IdentityReviewDecisionInput) -> str:
    payload = {
        "action": command.action.value,
        "decided_at": _utc_z(command.decided_at),
        "reason": command.reason,
        "review_item_id": str(command.review_item_id),
        "target_company_id": (
            None if command.target_company_id is None else str(command.target_company_id)
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return sha256(encoded).hexdigest()


def _utc_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_exact_decision_replay(
    persisted: CompanyIdentityReviewDecision,
    command: IdentityReviewDecisionInput,
) -> bool:
    return (
        persisted.decision_hash == _decision_hash(command)
        and persisted.action is command.action
        and persisted.target_company_id == command.target_company_id
        and persisted.reason == command.reason
        and persisted.decided_at == command.decided_at
    )


def _lock_involved_companies(
    session: Session,
    pending: list[tuple[UUID, IdentityReviewDecisionInput]],
    items: dict[UUID, CompanyIdentityReviewItem],
    drafts: dict[UUID, CompanyIdentityReviewDraft],
) -> dict[UUID, Company]:
    target_ids = {
        command.target_company_id
        for _, command in pending
        if command.target_company_id is not None
    }
    preliminary_ids = set(target_ids)
    for item_id, command in pending:
        allowed = (
            frozenset()
            if command.action is IdentityReviewAction.CREATE_NEW
            else frozenset(target_ids & {command.target_company_id})
        )
        current_owners = _current_owner_ids(session, drafts[item_id].identity)
        preliminary_ids.update(current_owners - allowed)
        if command.action is IdentityReviewAction.CREATE_NEW and not current_owners:
            preliminary_ids.add(UUID(hex=items[item_id].stable_identity_hash[:32]))
    rows = tuple(
        session.scalars(
            select(Company)
            .where(Company.id.in_(preliminary_ids))
            .order_by(Company.id)
            .with_for_update()
        )
    ) if preliminary_ids else ()
    companies = {row.id: row for row in rows}
    if any(target_id not in companies for target_id in target_ids):
        raise IdentityOwnerChanged
    _ = items
    return companies


def _lock_involved_aliases(
    session: Session,
    pending: list[tuple[UUID, IdentityReviewDecisionInput]],
    companies: dict[UUID, Company],
    drafts: dict[UUID, CompanyIdentityReviewDraft],
) -> None:
    names: set[str] = set()
    for item_id, command in pending:
        identity = drafts[item_id].identity
        names.update((identity.normalized_name, *identity.normalized_aliases))
        if command.target_company_id is not None:
            names.add(companies[command.target_company_id].normalized_name)
    if names:
        tuple(
            session.scalars(
                select(CompanyAlias)
                .where(CompanyAlias.normalized_alias.in_(names))
                .order_by(CompanyAlias.normalized_alias, CompanyAlias.id)
                .with_for_update()
            )
        )


def _apply_one_decision(
    session: Session,
    *,
    item: CompanyIdentityReviewItem,
    command: IdentityReviewDecisionInput,
    draft: CompanyIdentityReviewDraft,
    companies: dict[UUID, Company],
) -> UUID | None:
    if command.action is IdentityReviewAction.REJECT:
        item.status = IdentityReviewStatus.REJECTED
        item.resolved_at = command.decided_at
        return None

    target = (
        None
        if command.target_company_id is None
        else companies.get(command.target_company_id)
    )
    allowed_owners = frozenset() if target is None else frozenset({target.id})
    if _current_owner_ids(session, draft.identity) - allowed_owners:
        raise IdentityOwnerChanged

    if command.action is IdentityReviewAction.CREATE_NEW:
        company_id = UUID(hex=item.stable_identity_hash[:32])
        if session.get(Company, company_id) is not None:
            raise IdentityOwnerChanged
        target = Company(
            id=company_id,
            canonical_name=draft.identity.canonical_name,
            normalized_name=draft.identity.normalized_name,
            website=draft.identity.official_website,
            city=draft.identity.city,
            funding_stage="unknown",
            scale="unknown",
        )
        session.add(target)
        session.flush()
        companies[target.id] = target
        _ensure_aliases(session, target, draft.identity.aliases)
    elif command.action is IdentityReviewAction.LINK_AS_ALIAS:
        assert target is not None
        _ensure_aliases(
            session,
            target,
            (draft.identity.canonical_name, *draft.identity.aliases),
        )
    elif command.action is IdentityReviewAction.RENAME_CANONICAL:
        assert target is not None
        _rename_company(session, target, draft.identity)
    else:
        raise IdentityReviewConflict

    item.status = IdentityReviewStatus.RESOLVED
    item.resolved_at = command.decided_at
    session.flush()
    return target.id


def _rename_company(
    session: Session,
    company: Company,
    identity: CompanyIdentityInput,
) -> None:
    old_name = company.canonical_name
    old_normalized = company.normalized_name
    if old_normalized != identity.normalized_name:
        old_alias = session.scalar(
            select(CompanyAlias).where(
                CompanyAlias.normalized_alias == old_normalized
            )
        )
        if old_alias is None:
            session.add(
                CompanyAlias(
                    company_id=company.id,
                    alias=old_name,
                    normalized_alias=old_normalized,
                )
            )
            session.flush()
        elif old_alias.company_id != company.id:
            raise IdentityOwnerChanged

        new_name_alias = session.scalar(
            select(CompanyAlias).where(
                CompanyAlias.normalized_alias == identity.normalized_name
            )
        )
        if new_name_alias is not None:
            if new_name_alias.company_id != company.id:
                raise IdentityOwnerChanged
            session.delete(new_name_alias)
            session.flush()

    company.canonical_name = identity.canonical_name
    company.normalized_name = identity.normalized_name
    company.website = identity.official_website
    company.city = identity.city
    session.flush()
    _ensure_aliases(session, company, identity.aliases)


def _ensure_aliases(
    session: Session,
    company: Company,
    aliases: Sequence[str],
) -> None:
    display_by_normalized: dict[str, str] = {}
    for alias in aliases:
        normalized = normalize_name(alias)
        if normalized and normalized != company.normalized_name:
            display_by_normalized.setdefault(normalized, alias)
    for normalized in sorted(display_by_normalized):
        existing = session.scalar(
            select(CompanyAlias).where(CompanyAlias.normalized_alias == normalized)
        )
        if existing is None:
            session.add(
                CompanyAlias(
                    company_id=company.id,
                    alias=display_by_normalized[normalized],
                    normalized_alias=normalized,
                )
            )
        elif existing.company_id != company.id:
            raise IdentityOwnerChanged
    session.flush()


def _current_owner_ids(session: Session, identity: CompanyIdentityInput) -> set[UUID]:
    try:
        owner_ids = _name_owner_ids(
            session,
            frozenset((identity.normalized_name, *identity.normalized_aliases)),
        )
        if identity.official_website is not None:
            owner_ids.update(
                session.scalars(
                    select(Company.id).where(
                        Company.normalized_website == identity.official_website
                    )
                )
            )
            owner_ids.update(
                _history_owner_ids(
                    session,
                    CompanyIdentityReviewItem.official_website
                    == identity.official_website,
                )
            )
        if identity.recruitment_identity is not None:
            owner_ids.update(
                _history_owner_ids(
                    session,
                    CompanyIdentityReviewItem.recruitment_identity
                    == identity.recruitment_identity,
                )
            )
        if identity.legal_identifiers:
            owner_ids.update(
                session.scalars(
                    select(RegulatoryFiling.company_id).where(
                        RegulatoryFiling.normalized_filing_number.in_(
                            identity.legal_identifiers
                        )
                    )
                )
            )
            owner_ids.update(_legal_history_owner_ids(session, identity.legal_identifiers))
        return owner_ids
    except SQLAlchemyError:
        raise IdentitySearchUnavailable from None


def _name_owner_ids(session: Session, names: frozenset[str]) -> set[UUID]:
    if not names:
        return set()
    canonical = set(
        session.scalars(select(Company.id).where(Company.normalized_name.in_(names)))
    )
    aliases = set(
        session.scalars(
            select(CompanyAlias.company_id).where(
                CompanyAlias.normalized_alias.in_(names)
            )
        )
    )
    return canonical | aliases


def _history_owner_ids(
    session: Session,
    criterion: ColumnElement[bool],
) -> set[UUID]:
    return {
        company_id
        for company_id in session.scalars(
            select(CompanyIdentityReviewDecision.resulting_company_id)
            .join(
                CompanyIdentityReviewItem,
                CompanyIdentityReviewDecision.review_item_id
                == CompanyIdentityReviewItem.id,
            )
            .where(
                CompanyIdentityReviewItem.status == IdentityReviewStatus.RESOLVED,
                CompanyIdentityReviewItem.resolved_at.is_not(None),
                CompanyIdentityReviewDecision.resulting_company_id.is_not(None),
                criterion,
            )
        )
        if company_id is not None
    }


def _legal_history_owner_ids(
    session: Session,
    identifiers: tuple[str, ...],
) -> set[UUID]:
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    if dialect_name == "postgresql":
        legal_values = func.jsonb_array_elements_text(
            cast(CompanyIdentityReviewItem.legal_identifiers, JSONB)
        ).table_valued("value")
    else:
        legal_values = func.json_each(
            CompanyIdentityReviewItem.legal_identifiers
        ).table_valued("value")
    return {
        company_id
        for company_id in session.scalars(
            select(CompanyIdentityReviewDecision.resulting_company_id)
            .join(
                CompanyIdentityReviewItem,
                CompanyIdentityReviewDecision.review_item_id
                == CompanyIdentityReviewItem.id,
            )
            .join(legal_values, true())
            .where(
                CompanyIdentityReviewItem.status == IdentityReviewStatus.RESOLVED,
                CompanyIdentityReviewItem.resolved_at.is_not(None),
                CompanyIdentityReviewDecision.resulting_company_id.is_not(None),
                cast(legal_values.c.value, String).in_(identifiers),
            )
        )
        if company_id is not None
    }
