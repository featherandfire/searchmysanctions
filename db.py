"""
Persistence layer for user-generated data (notes, address search history).

Uses SQLAlchemy Core (no ORM) over Postgres in production and SQLite in tests.
Arrays are stored as JSON for cross-dialect portability.

Configuration: set DATABASE_URL in the environment. Defaults to a local
SQLite file so the app runs out of the box and tests are self-contained.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, MetaData,
    String, Table, create_engine, delete, insert, select, update,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(os.path.dirname(__file__), "app_data.db"),
)

# pool_pre_ping handles dropped connections cleanly (relevant for managed
# Postgres providers that recycle connections aggressively)
_engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)

_metadata = MetaData()

notes = Table(
    "notes", _metadata,
    Column("id",          Integer,  primary_key=True, autoincrement=True),
    Column("title",       String,   nullable=False, default=""),
    Column("body",        String,   nullable=False, default=""),
    Column("tags",        JSON,     nullable=False, default=list),
    Column("created_at",  DateTime(timezone=True), nullable=False),
    Column("updated_at",  DateTime(timezone=True), nullable=True),
)

address_history = Table(
    "address_history", _metadata,
    Column("address",        String,  primary_key=True),
    Column("sanctioned",     Boolean, nullable=False, default=False),
    Column("sanction_lists", JSON,    nullable=False, default=list),
    Column("label",          String,  nullable=False, default=""),
    Column("referred_from",  String,  nullable=False, default=""),
    Column("mode",           String,  nullable=False, default="balance"),
    Column("searched_at",    DateTime(timezone=True), nullable=False),
)


def init():
    """Create tables if they don't exist. Idempotent — safe to call every boot."""
    _metadata.create_all(_engine)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt_dt(dt: datetime) -> str:
    """Match the historical JSON format used by the frontend."""
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ── Notes ────────────────────────────────────────────────────────────────────

def list_notes() -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(select(notes).order_by(notes.c.id.desc())).mappings().all()
    return [_serialize_note(r) for r in rows]


def create_note(title: str, body: str, tags: list[str]) -> dict:
    with _engine.begin() as conn:
        result = conn.execute(
            insert(notes).values(
                title=title, body=body, tags=tags, created_at=_now(),
            ).returning(notes.c.id)
        )
        new_id = result.scalar_one()
        row = conn.execute(
            select(notes).where(notes.c.id == new_id)
        ).mappings().one()
    return _serialize_note(row)


def delete_note(note_id: int) -> None:
    with _engine.begin() as conn:
        conn.execute(delete(notes).where(notes.c.id == note_id))


def update_note(note_id: int, fields: dict) -> None:
    """`fields` may contain any subset of {title, body, tags}. Always sets updated_at."""
    values = {k: v for k, v in fields.items() if k in ("title", "body", "tags")}
    if not values:
        return
    values["updated_at"] = _now()
    with _engine.begin() as conn:
        conn.execute(update(notes).where(notes.c.id == note_id).values(**values))


def _serialize_note(row) -> dict:
    return {
        "id":         row["id"],
        "title":      row["title"],
        "body":       row["body"],
        "tags":       row["tags"] or [],
        "created_at": _fmt_dt(row["created_at"]),
        **({"updated_at": _fmt_dt(row["updated_at"])} if row["updated_at"] else {}),
    }


# ── Address history ─────────────────────────────────────────────────────────

def list_address_history() -> list[dict]:
    with _engine.connect() as conn:
        rows = conn.execute(
            select(address_history).order_by(address_history.c.searched_at.desc())
        ).mappings().all()
    return [_serialize_history(r) for r in rows]


def upsert_address_history(
    address: str, sanctioned: bool, sanction_lists: list[str],
    label: str, referred_from: str, mode: str,
) -> dict:
    """
    Insert or replace the row for `address`, refreshing searched_at so the
    entry surfaces at the top of the history list.
    """
    with _engine.begin() as conn:
        conn.execute(delete(address_history).where(address_history.c.address == address))
        conn.execute(insert(address_history).values(
            address=address,
            sanctioned=sanctioned,
            sanction_lists=sanction_lists,
            label=label,
            referred_from=referred_from,
            mode=mode,
            searched_at=_now(),
        ))
        row = conn.execute(
            select(address_history).where(address_history.c.address == address)
        ).mappings().one()
    return _serialize_history(row)


def delete_address(address: str) -> None:
    with _engine.begin() as conn:
        conn.execute(delete(address_history).where(address_history.c.address == address))


def clear_address_history() -> None:
    with _engine.begin() as conn:
        conn.execute(delete(address_history))


def _serialize_history(row) -> dict:
    return {
        "address":        row["address"],
        "sanctioned":     bool(row["sanctioned"]),
        "sanction_lists": row["sanction_lists"] or [],
        "label":          row["label"],
        "referred_from":  row["referred_from"],
        "mode":           row["mode"],
        "searched_at":    _fmt_dt(row["searched_at"]),
    }
