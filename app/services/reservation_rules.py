from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Location, Reservation, Room
from app.schemas.entities import ReservationInputBase


class LocationNotFoundError(Exception):
    pass


class RoomNotFoundError(Exception):
    pass


class ReservationConflictError(Exception):
    pass


class ReservationCapacityError(Exception):
    pass


class RoomCapacityReductionError(Exception):
    pass


class RoomLocationMismatchError(Exception):
    pass


async def ensure_room_belongs_to_location(
    session: AsyncSession,
    location_id: str,
    room_id: str,
) -> Room:
    location = await session.get(Location, location_id)
    if location is None:
        raise LocationNotFoundError("Location not found")

    room = await session.get(Room, room_id)
    if room is None:
        raise RoomNotFoundError("Room not found")

    if room.location_id != location_id:
        raise RoomLocationMismatchError("Room does not belong to the selected location.")
    return room


def ensure_reservation_within_room_capacity(payload: ReservationInputBase, room: Room) -> None:
    if payload.attendees is not None and payload.attendees > room.capacity:
        raise ReservationCapacityError("Reservation attendees exceed the room capacity.")


async def ensure_room_capacity_can_fit_existing_reservations(
    session: AsyncSession,
    room_id: str,
    capacity: int,
) -> None:
    max_attendees = await session.scalar(
        select(func.max(Reservation.attendees)).where(Reservation.room_id == room_id)
    )
    if max_attendees is not None and capacity < max_attendees:
        raise RoomCapacityReductionError(
            "Room capacity cannot be reduced below existing reservation attendees."
        )


async def room_has_reservations(session: AsyncSession, room_id: str) -> bool:
    result = await session.execute(select(exists().where(Reservation.room_id == room_id)))
    return bool(result.scalar_one())


async def ensure_no_conflict(
    session: AsyncSession,
    payload: ReservationInputBase,
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
