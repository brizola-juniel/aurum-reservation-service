from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reservation, Room
from app.schemas.entities import ReservationBase


class ReservationConflictError(Exception):
    pass


class RoomLocationMismatchError(Exception):
    pass


async def ensure_room_belongs_to_location(
    session: AsyncSession,
    location_id: str,
    room_id: str,
) -> None:
    result = await session.execute(
        select(exists().where(and_(Room.id == room_id, Room.location_id == location_id)))
    )
    if not result.scalar_one():
        raise RoomLocationMismatchError("Room does not belong to the selected location.")


async def ensure_no_conflict(
    session: AsyncSession,
    payload: ReservationBase,
    reservation_id: str | None = None,
) -> None:
    filters = [
        Reservation.location_id == payload.location_id,
        Reservation.room_id == payload.room_id,
        Reservation.start_at < payload.end_at,
        Reservation.end_at > payload.start_at,
    ]
    if reservation_id is not None:
        filters.append(Reservation.id != reservation_id)

    result = await session.execute(select(exists().where(and_(*filters))))
    if result.scalar_one():
        raise ReservationConflictError(
            "Schedule conflict: this room already has a reservation in the selected period."
        )
