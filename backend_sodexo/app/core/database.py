from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import PROJECT_ROOT, get_settings

settings = get_settings()


def _resolve_database_url(url: str) -> str:
    if url.startswith("sqlite:///./"):
        abs_path = (PROJECT_ROOT / url.removeprefix("sqlite:///./")).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{abs_path.as_posix()}"
    return url


_resolved_url = _resolve_database_url(settings.database_url)
_is_sqlite = _resolved_url.startswith("sqlite")
# timeout: how long a writer waits for the lock before raising "database is
# locked". Training fits several series in parallel threads, each committing
# its own model runs, so contention is expected and waiting is the right
# response to it.
_connect_args = {"check_same_thread": False, "timeout": 30} if _is_sqlite else {}

engine = create_engine(_resolved_url, connect_args=_connect_args, future=True)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection, _connection_record):
        """WAL, so parallel training does not serialise behind a single writer.

        The default rollback journal blocks readers for the duration of a
        write, which with concurrent series training means the status-polling
        endpoint stalls every time a model run is persisted. WAL lets readers
        carry on while a writer holds the lock. busy_timeout backs up the
        connect-level timeout for pooled connections.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            # NORMAL is the documented safe pairing with WAL: durable across
            # an application crash, risking only the last commits on a full OS
            # crash, in exchange for not fsyncing every single write.
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
