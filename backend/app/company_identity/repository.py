"""Bounded SQL repositories for company identity resolution."""

from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol
from typing import cast as type_cast
from uuid import UUID

from rapidfuzz.fuzz import ratio
from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    cast,
    func,
    literal,
    select,
    text,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.company_identity.contracts import (
    CompanyIdentityCandidateMatch,
    CompanyIdentityInput,
    CompanyIdentityNameOwner,
    IdentityReviewStatus,
)
from app.company_identity.models import (
    CompanyIdentityReviewDecision,
    CompanyIdentityReviewItem,
)
from app.models import Company, CompanyAlias, RegulatoryFiling

_MAX_CANDIDATES = 20
_POSTGRESQL_SIMILARITY_CAPABILITY_SQL = text(
    "SELECT "
    "EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'pg_trgm') "
    "AND EXISTS ("
    "SELECT 1 FROM pg_catalog.pg_operator AS operator "
    "JOIN pg_catalog.pg_type AS left_type ON left_type.oid = operator.oprleft "
    "JOIN pg_catalog.pg_type AS right_type ON right_type.oid = operator.oprright "
    "WHERE operator.oprname = '<->' "
    "AND left_type.typname = 'text' AND right_type.typname = 'text'"
    ")"
)


class CompanyIdentityRepository(Protocol):
    async def find_exact_name_owners(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]: ...

    async def find_evidence_owner_ids(
        self, identity: CompanyIdentityInput
    ) -> frozenset[UUID]: ...

    async def find_similar_names(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]: ...

    def similarity_search_available(self) -> bool: ...


class SqlAlchemyCompanyIdentityRepository:
    def __init__(self, session: Session, *, similarity_limit: int = _MAX_CANDIDATES) -> None:
        if not 1 <= similarity_limit <= _MAX_CANDIDATES:
            raise ValueError("similarity_limit must be between 1 and 20")
        self.session = session
        self.similarity_limit = similarity_limit
        self._dialect_name = session.bind.dialect.name if session.bind is not None else ""
        self._similarity_available: bool | None = (
            None if self._dialect_name == "postgresql" else False
        )

    async def find_exact_name_owners(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        return self.find_exact_name_owners_sync(names)

    def find_exact_name_owners_sync(
        self, names: frozenset[str]
    ) -> tuple[CompanyIdentityNameOwner, ...]:
        if not names:
            return ()
        canonical = select(
            Company.id.label("company_id"),
            Company.normalized_name.label("normalized_name"),
        ).where(Company.normalized_name.in_(names))
        aliases = select(
            CompanyAlias.company_id.label("company_id"),
            CompanyAlias.normalized_alias.label("normalized_name"),
        ).where(CompanyAlias.normalized_alias.in_(names))
        ownership = union_all(canonical, aliases).subquery()
        statement = (
            select(ownership.c.company_id, ownership.c.normalized_name)
            .distinct()
            .order_by(ownership.c.normalized_name, ownership.c.company_id)
        )
        return tuple(
            CompanyIdentityNameOwner(company_id=row.company_id, normalized_name=row.normalized_name)
            for row in self.session.execute(statement)
        )

    async def find_evidence_owner_ids(
        self, identity: CompanyIdentityInput
    ) -> frozenset[UUID]:
        owner_ids: set[UUID] = set()
        if identity.official_website is not None:
            owner_ids.update(
                self._bounded_owner_ids(
                    select(Company.id).where(
                        Company.normalized_website == identity.official_website
                    )
                )
            )
            owner_ids.update(
                self._resolved_history_owner_ids(
                    CompanyIdentityReviewItem.official_website == identity.official_website
                )
            )

        if identity.recruitment_identity is not None:
            owner_ids.update(
                self._resolved_history_owner_ids(
                    CompanyIdentityReviewItem.recruitment_identity
                    == identity.recruitment_identity
                )
            )

        if identity.legal_identifiers:
            owner_ids.update(
                self._bounded_owner_ids(
                    select(RegulatoryFiling.company_id).where(
                        RegulatoryFiling.normalized_filing_number.in_(
                            identity.legal_identifiers
                        )
                    )
                )
            )
            owner_ids.update(self._resolved_legal_history_owner_ids(identity.legal_identifiers))

        return frozenset(sorted(owner_ids, key=str)[: self.similarity_limit])

    async def find_similar_names(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        return self.find_similar_names_sync(names, limit=limit)

    def find_similar_names_sync(
        self, names: frozenset[str], *, limit: int
    ) -> tuple[CompanyIdentityCandidateMatch, ...]:
        final_limit = min(limit, self.similarity_limit, _MAX_CANDIDATES)
        if final_limit <= 0 or not names or not self.similarity_search_available():
            return ()

        recalled: list[CompanyIdentityCandidateMatch] = []
        for candidate_name in sorted(names):
            canonical_distance = Company.normalized_name.op("<->")(candidate_name)
            canonical_statement = (
                select(
                    Company.id.label("company_id"),
                    Company.canonical_name.label("canonical_name"),
                    Company.normalized_name.label("normalized_name"),
                    Company.normalized_name.label("matched_name"),
                    literal("fuzzy_canonical").label("match_kind"),
                )
                .order_by(
                    canonical_distance,
                    Company.normalized_name,
                    Company.id,
                )
                .limit(final_limit)
            )
            alias_distance = CompanyAlias.normalized_alias.op("<->")(candidate_name)
            alias_statement = (
                select(
                    Company.id.label("company_id"),
                    Company.canonical_name.label("canonical_name"),
                    Company.normalized_name.label("normalized_name"),
                    CompanyAlias.normalized_alias.label("matched_name"),
                    literal("fuzzy_alias").label("match_kind"),
                )
                .join(CompanyAlias, CompanyAlias.company_id == Company.id)
                .order_by(
                    alias_distance,
                    CompanyAlias.normalized_alias,
                    Company.id,
                    CompanyAlias.id,
                )
                .limit(final_limit)
            )
            recalled.extend(self._candidate_matches(candidate_name, canonical_statement))
            recalled.extend(self._candidate_matches(candidate_name, alias_statement))

        best_by_company: dict[UUID, CompanyIdentityCandidateMatch] = {}
        for candidate in sorted(recalled, key=_candidate_order):
            best_by_company.setdefault(candidate.company_id, candidate)
        return tuple(sorted(best_by_company.values(), key=_candidate_order)[:final_limit])

    def similarity_search_available(self) -> bool:
        if self._similarity_available is None:
            self._similarity_available = bool(
                self.session.scalar(_POSTGRESQL_SIMILARITY_CAPABILITY_SQL)
            )
        return self._similarity_available

    def _bounded_owner_ids(
        self,
        statement: Select[tuple[UUID]] | Select[tuple[UUID | None]],
    ) -> tuple[UUID, ...]:
        owner_column = next(iter(statement.selected_columns))
        bounded = statement.distinct().order_by(owner_column).limit(self.similarity_limit)
        return tuple(
            type_cast(UUID, row[0])
            for row in self.session.execute(bounded)
            if row[0] is not None
        )

    def _resolved_history_owner_ids(
        self, criterion: ColumnElement[bool]
    ) -> tuple[UUID, ...]:
        statement = (
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
        return self._bounded_owner_ids(statement)

    def _resolved_legal_history_owner_ids(
        self, identifiers: tuple[str, ...]
    ) -> tuple[UUID, ...]:
        if self._dialect_name == "postgresql":
            legal_values = func.jsonb_array_elements_text(
                cast(CompanyIdentityReviewItem.legal_identifiers, JSONB)
            ).table_valued("value")
        else:
            legal_values = func.json_each(
                CompanyIdentityReviewItem.legal_identifiers
            ).table_valued("value")
        statement = (
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
        return self._bounded_owner_ids(statement)

    def _candidate_matches(
        self,
        candidate_name: str,
        statement: Select[tuple[UUID, str, str, str, str]],
    ) -> Iterable[CompanyIdentityCandidateMatch]:
        for row in self.session.execute(statement):
            yield CompanyIdentityCandidateMatch(
                company_id=row.company_id,
                canonical_name=row.canonical_name,
                normalized_name=row.normalized_name,
                match_kind=row.match_kind,
                score=Decimal(str(ratio(candidate_name, row.matched_name))),
            )


def _candidate_order(
    candidate: CompanyIdentityCandidateMatch,
) -> tuple[Decimal, str, str, str]:
    return (
        -candidate.score,
        candidate.normalized_name,
        str(candidate.company_id),
        candidate.match_kind,
    )
