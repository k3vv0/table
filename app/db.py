from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

# timeout=30: pysqlite's 5s default turns a long write in one request into "database is
# locked" in another instead of a short wait.
engine = create_engine(
    get_settings().database_url, echo=False, connect_args={"check_same_thread": False, "timeout": 30}
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """WAL lets readers carry on while a sync writes; synchronous=NORMAL is its safe pairing."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db() -> None:
    """Create any missing tables. A change to an existing table needs a
    scripts/migrate_*.py, which deploy.sh runs on every deploy (DESIGN.md §6)."""
    import app.models  # noqa: F401  (registers the models on SQLModel.metadata)

    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
