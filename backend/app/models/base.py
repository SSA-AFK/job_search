from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CHAR, DateTime
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


class GUID(TypeDecorator[UUID]):
    """Store UUIDs natively on PostgreSQL and as canonical strings elsewhere."""

    impl = CHAR(36)
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgreSQLUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: UUID | str | None, dialect: Any) -> UUID | str | None:
        if value is None:
            return None
        parsed = value if isinstance(value, UUID) else UUID(value)
        return parsed if dialect.name == "postgresql" else str(parsed)

    def process_result_value(self, value: UUID | str | None, _dialect: Any) -> UUID | None:
        if value is None:
            return None
        return value if isinstance(value, UUID) else UUID(value)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
