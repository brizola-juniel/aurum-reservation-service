from hashlib import blake2b

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def room_advisory_lock_key(room_id: str) -> int:
    digest = blake2b(room_id.encode("utf-8"), digest_size=8, person=b"aurumrs").digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)
    if value >= 2**63:
        value -= 2**64
    return value


async def lock_room_reservations(session: AsyncSession, room_id: str) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": room_advisory_lock_key(room_id)},
    )
