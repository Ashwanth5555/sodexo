"""Lightweight additive schema migration for SQLite.

create_all() creates missing tables but never touches a table that already
exists, so adding a column to a model leaves an older sodexo.db silently
missing it - and the failure shows up much later as "no such column" from a
query, not at startup.

This walks every mapped table and adds whatever columns the model declares
and the database lacks. Additive only: it never drops, renames or retypes,
because those need a table rebuild and real migration tooling. That is the
right trade for a POC whose data is wiped on restart anyway - the schema has
to survive, the rows do not.

Replaces a hand-maintained list of ALTER statements that had to be extended
by hand for every new column, and was already out of date.
"""
from __future__ import annotations

import logging

from sqlalchemy import Engine, inspect, text
from sqlalchemy.schema import CreateColumn

logger = logging.getLogger(__name__)


def _column_clause(engine: Engine, column) -> str | None:
    """Render `name TYPE [NOT NULL DEFAULT ...]` for ADD COLUMN.

    Returns None when the column cannot be added safely - SQLite refuses a
    NOT NULL column with no default on a table that already has rows, and a
    guessed default would be worse than leaving it to a real migration.
    """
    ddl = CreateColumn(column).compile(dialect=engine.dialect).string
    if not column.nullable and column.server_default is None and column.default is None:
        return None
    if not column.nullable and column.default is not None and not column.default.is_callable:
        # SQLAlchemy python-side defaults are not emitted into DDL, so spell
        # the literal out or SQLite rejects the NOT NULL.
        value = column.default.arg
        literal = f"'{value}'" if isinstance(value, str) else str(value)
        return f"{ddl} DEFAULT {literal}"
    if not column.nullable:
        # A callable default (e.g. datetime.utcnow) cannot become SQL DDL.
        return None
    return ddl


def add_missing_columns(engine: Engine, metadata) -> dict[str, list[str]]:
    """Add every model column the database is missing. Returns what it added."""
    if engine.dialect.name != "sqlite":
        # Postgres deployments should use real migrations; silently ALTERing
        # a shared database is not something to do behind the operator's back.
        return {}

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: dict[str, list[str]] = {}

    with engine.begin() as conn:
        for table_name, table in metadata.tables.items():
            if table_name not in existing_tables:
                continue  # create_all just made it, so it is already correct.
            present = {col["name"] for col in inspector.get_columns(table_name)}
            for column in table.columns:
                if column.name in present:
                    continue
                clause = _column_clause(engine, column)
                if clause is None:
                    logger.warning(
                        "Cannot auto-add %s.%s (NOT NULL without a usable default) - "
                        "recreate the database or migrate it by hand.",
                        table_name, column.name,
                    )
                    continue
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {clause}"))
                added.setdefault(table_name, []).append(column.name)

    if added:
        summary = "; ".join(f"{table}: {', '.join(cols)}" for table, cols in sorted(added.items()))
        logger.info("Schema migration added columns - %s", summary)
    return added
