"""Database engine. Switching to Postgres is a DATABASE_URL change."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from ben.config import Settings
from ben.storage import models  # noqa: F401  (registers tables)

_engines: dict[str, Engine] = {}


def get_engine(settings: Settings) -> Engine:
    url = settings.resolved_database_url
    if url not in _engines:
        kwargs: dict = {}
        if url.startswith("sqlite"):
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, pool_pre_ping=True, **kwargs)
        SQLModel.metadata.create_all(engine)
        _engines[url] = engine
    return _engines[url]


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    with Session(get_engine(settings), expire_on_commit=False) as session:
        yield session
