from sqlalchemy import create_engine
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
_connect_args = {"check_same_thread": False} if _resolved_url.startswith("sqlite") else {}

engine = create_engine(_resolved_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
