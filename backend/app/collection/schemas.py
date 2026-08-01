from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CollectionStatus


class CollectionRequestCreate(BaseModel):
    query: Annotated[str, Field(min_length=2, max_length=100)]

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CollectionRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    normalized_query: str
    status: CollectionStatus
    company_id: UUID | None
    error_code: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
