"""Модель даних: користувачі, місця зберігання, речі та історія руху."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, Text, DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


ROLES = ("viewer", "editor", "admin")


class User(Base):
    """Користувач Telegram, якому дозволено доступ."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Telegram user id
    name: Mapped[str] = mapped_column(String(200), default="")
    username: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Location(Base):
    """Місце зберігання. Може бути вкладеним: Склад › Стелаж › Полиця."""

    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    parent: Mapped["Location | None"] = relationship(remote_side="Location.id", back_populates="children")
    children: Mapped[list["Location"]] = relationship(back_populates="parent")
    items: Mapped[list["Item"]] = relationship(back_populates="location")


class Item(Base):
    """Річ на складі."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    sku: Mapped[str] = mapped_column(String(100), default="")  # артикул / інвентарний номер
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(20), default="шт")
    min_quantity: Mapped[float] = mapped_column(Float, default=0)  # поріг "закінчується"
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id", ondelete="SET NULL"), nullable=True)
    photo: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Нижній регістр назви/опису/артикулу: SQLite LOWER() не працює з кирилицею,
    # тому пошуковий текст готуємо в Python.
    search_text: Mapped[str] = mapped_column(Text, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    location: Mapped[Location | None] = relationship(back_populates="items")
    movements: Mapped[list["Movement"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", order_by="Movement.id.desc()"
    )

    def refresh_search_text(self) -> None:
        self.search_text = " ".join(filter(None, [self.name, self.sku, self.description])).lower()

    @property
    def is_low(self) -> bool:
        return self.min_quantity > 0 and self.quantity <= self.min_quantity


class Movement(Base):
    """Журнал змін: хто, коли і що змінив."""

    __tablename__ = "movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    delta: Mapped[float] = mapped_column(Float, default=0)
    quantity_after: Mapped[float] = mapped_column(Float, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_name: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    item: Mapped[Item] = relationship(back_populates="movements")


def make_session_factory(db_url: str) -> sessionmaker:
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
