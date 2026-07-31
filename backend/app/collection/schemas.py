from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class CollectionRequestCreate(BaseModel):
    query: Annotated[str, Field(min_length=2, max_length=100)]

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
