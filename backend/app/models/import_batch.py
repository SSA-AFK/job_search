from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"
    __table_args__ = (UniqueConstraint("workbook_filename", "worksheet_name", name="uq_import_batch_workbook_sheet"),)

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    workbook_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(String(100), nullable=False)


class ImportItem(Base, TimestampMixin):
    __tablename__ = "import_items"
    __table_args__ = (UniqueConstraint("import_batch_id", "source_row", name="uq_import_item_batch_row"),)

    id: Mapped[UUID] = mapped_column(GUID(), primary_key=True, default=uuid4)
    import_batch_id: Mapped[UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_source_name: Mapped[str] = mapped_column(String(255), nullable=False)
