from datetime import datetime
from sqlalchemy import Integer, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class DocumentGroup(Base):
    __tablename__ = "document_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["DocumentGroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")


class DocumentGroupMember(Base):
    __tablename__ = "document_group_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_groups.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[int] = mapped_column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)

    group: Mapped["DocumentGroup"] = relationship(back_populates="members")
