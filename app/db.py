import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from alembic import command
from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def build_alembic_config() -> Config:
    service_root = Path(__file__).resolve().parents[1]
    config = Config(str(service_root / "alembic.ini"))
    config.set_main_option("script_location", str(service_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


async def run_migrations() -> None:
    await asyncio.to_thread(command.upgrade, build_alembic_config(), "head")


async def create_database() -> None:
    from app.models.entities import Location, Reservation, Room  # noqa: F401

    if settings.database_url.startswith("sqlite"):
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    else:
        await run_migrations()

    async with SessionLocal() as session:
        await seed_reference_data(session)


async def seed_reference_data(session: AsyncSession) -> None:
    from app.models.entities import Location, Room

    existing = await session.execute(select(Location.id).limit(1))
    if existing.scalar_one_or_none() is not None:
        return

    headquarters = Location(name="Matriz Sao Paulo", address="Av. Paulista, 1000")
    branch = Location(name="Filial Campinas", address="Rua das Reunioes, 250")
    session.add_all([headquarters, branch])
    await session.flush()
    session.add_all(
        [
            Room(location_id=headquarters.id, name="Sala Aurum", capacity=12),
            Room(location_id=headquarters.id, name="Sala Prata", capacity=8),
            Room(location_id=branch.id, name="Sala Executive", capacity=16),
        ]
    )
    await session.commit()
