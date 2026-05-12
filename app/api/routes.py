from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, delete, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import CurrentUser, get_current_user
from app.db import get_session
from app.models import Location, Reservation, Room
from app.schemas.entities import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    LocationCreate,
    LocationRead,
    LocationUpdate,
    ReservationCreate,
    ReservationRead,
    ReservationUpdate,
    RoomCreate,
    RoomRead,
    RoomUpdate,
)
from app.services.reservation_locks import lock_room_reservations
from app.services.reservation_rules import (
    LocationNotFoundError,
    ReservationCapacityError,
    ReservationConflictError,
    RoomCapacityReductionError,
    RoomLocationMismatchError,
    RoomNotFoundError,
    ensure_no_conflict,
    ensure_reservation_within_room_capacity,
    ensure_room_belongs_to_location,
    ensure_room_capacity_can_fit_existing_reservations,
    room_has_reservations,
)

router = APIRouter(prefix="/api")
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LocationFilter = Annotated[str | None, Query()]


async def commit_or_conflict(session: AsyncSession, detail: str) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc


def ensure_reservation_owner(reservation: Reservation, current_user: CurrentUser) -> None:
    if reservation.created_by_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the reservation owner can modify this reservation.",
        )


async def ensure_unique_location_name(
    session: AsyncSession,
    name: str,
    location_id: str | None = None,
) -> None:
    filters = [func.lower(Location.name) == name.lower()]
    if location_id is not None:
        filters.append(Location.id != location_id)

    result = await session.execute(select(exists().where(and_(*filters))))
    if result.scalar_one():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location name already exists.",
        )


async def ensure_unique_room_name(
    session: AsyncSession,
    location_id: str,
    name: str,
    room_id: str | None = None,
) -> None:
    filters = [Room.location_id == location_id, func.lower(Room.name) == name.lower()]
    if room_id is not None:
        filters.append(Room.id != room_id)

    result = await session.execute(select(exists().where(and_(*filters))))
    if result.scalar_one():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Room name already exists in this location.",
        )


def reservation_to_read(reservation: Reservation) -> ReservationRead:
    return ReservationRead(
        id=reservation.id,
        location_id=reservation.location_id,
        room_id=reservation.room_id,
        start_at=reservation.start_at,
        end_at=reservation.end_at,
        responsible=reservation.responsible,
        coffee=reservation.coffee,
        attendees=reservation.attendees,
        description=reservation.description,
        created_by_user_id=reservation.created_by_user_id,
        created_by_email=reservation.created_by_email,
        created_at=reservation.created_at,
        updated_at=reservation.updated_at,
        location_name=reservation.location.name,
        room_name=reservation.room.name,
    )


@router.get("/locations", response_model=list[LocationRead])
async def list_locations(
    _: CurrentUserDep,
    session: SessionDep,
) -> list[Location]:
    result = await session.execute(select(Location).order_by(Location.name))
    return list(result.scalars().all())


@router.post("/locations", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
async def create_location(
    payload: LocationCreate,
    _: CurrentUserDep,
    session: SessionDep,
) -> Location:
    await ensure_unique_location_name(session, payload.name)
    location = Location(name=payload.name, address=payload.address)
    session.add(location)
    await commit_or_conflict(session, "Location name already exists.")
    await session.refresh(location)
    return location


@router.put("/locations/{location_id}", response_model=LocationRead)
async def update_location(
    location_id: str,
    payload: LocationUpdate,
    _: CurrentUserDep,
    session: SessionDep,
) -> Location:
    location = await session.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    await ensure_unique_location_name(session, payload.name, location_id=location_id)
    location.name = payload.name
    location.address = payload.address
    await commit_or_conflict(session, "Location name already exists.")
    await session.refresh(location)
    return location


@router.delete("/locations/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(
    location_id: str,
    _: CurrentUserDep,
    session: SessionDep,
) -> Response:
    location = await session.get(Location, location_id)
    if location is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    reservation = await session.scalar(select(Reservation.id).where(Reservation.location_id == location_id).limit(1))
    if reservation is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location has reservations and cannot be deleted.",
        )
    await session.delete(location)
    await commit_or_conflict(session, "Location could not be deleted because related data changed.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/rooms", response_model=list[RoomRead])
async def list_rooms(
    _: CurrentUserDep,
    session: SessionDep,
    location_id: LocationFilter = None,
) -> list[Room]:
    query = select(Room).order_by(Room.name)
    if location_id is not None:
        query = query.where(Room.location_id == location_id)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.post("/rooms", response_model=RoomRead, status_code=status.HTTP_201_CREATED)
async def create_room(
    payload: RoomCreate,
    _: CurrentUserDep,
    session: SessionDep,
) -> Room:
    if await session.get(Location, payload.location_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    await ensure_unique_room_name(session, payload.location_id, payload.name)
    room = Room(
        location_id=payload.location_id,
        name=payload.name,
        capacity=payload.capacity,
    )
    session.add(room)
    await commit_or_conflict(session, "Room name already exists in this location.")
    await session.refresh(room)
    return room


@router.put("/rooms/{room_id}", response_model=RoomRead)
async def update_room(
    room_id: str,
    payload: RoomUpdate,
    _: CurrentUserDep,
    session: SessionDep,
) -> Room:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    if await session.get(Location, payload.location_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location not found")
    if payload.location_id != room.location_id and await room_has_reservations(session, room_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Room has reservations and cannot be moved to another location.",
        )
    try:
        await ensure_room_capacity_can_fit_existing_reservations(session, room_id, payload.capacity)
    except RoomCapacityReductionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await ensure_unique_room_name(session, payload.location_id, payload.name, room_id=room_id)
    room.location_id = payload.location_id
    room.name = payload.name
    room.capacity = payload.capacity
    await commit_or_conflict(session, "Room name already exists in this location.")
    await session.refresh(room)
    return room


@router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_room(
    room_id: str,
    _: CurrentUserDep,
    session: SessionDep,
) -> Response:
    room = await session.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    reservation = await session.scalar(select(Reservation.id).where(Reservation.room_id == room_id).limit(1))
    if reservation is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Room has reservations and cannot be deleted.",
        )
    await session.delete(room)
    await commit_or_conflict(session, "Room could not be deleted because related data changed.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/reservations", response_model=list[ReservationRead])
async def list_reservations(
    _: CurrentUserDep,
    session: SessionDep,
) -> list[ReservationRead]:
    result = await session.execute(
        select(Reservation)
        .options(selectinload(Reservation.location), selectinload(Reservation.room))
        .order_by(Reservation.start_at)
    )
    return [reservation_to_read(item) for item in result.scalars().all()]


@router.post("/reservations", response_model=ReservationRead, status_code=status.HTTP_201_CREATED)
async def create_reservation(
    payload: ReservationCreate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ReservationRead:
    try:
        room = await ensure_room_belongs_to_location(session, payload.location_id, payload.room_id)
        await lock_room_reservations(session, payload.room_id)
        ensure_reservation_within_room_capacity(payload, room)
        await ensure_no_conflict(session, payload)
    except LocationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoomLocationMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ReservationCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ReservationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    reservation = Reservation(
        location_id=payload.location_id,
        room_id=payload.room_id,
        start_at=payload.start_at,
        end_at=payload.end_at,
        responsible=payload.responsible,
        coffee=payload.coffee,
        attendees=payload.attendees,
        description=payload.description,
        created_by_user_id=current_user.id,
        created_by_email=current_user.email,
    )
    session.add(reservation)
    await commit_or_conflict(session, "Reservation could not be persisted because related data changed.")

    result = await session.execute(
        select(Reservation)
        .where(Reservation.id == reservation.id)
        .options(selectinload(Reservation.location), selectinload(Reservation.room))
    )
    return reservation_to_read(result.scalar_one())


@router.put("/reservations/{reservation_id}", response_model=ReservationRead)
async def update_reservation(
    reservation_id: str,
    payload: ReservationUpdate,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> ReservationRead:
    result = await session.execute(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .options(selectinload(Reservation.location), selectinload(Reservation.room))
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")
    ensure_reservation_owner(reservation, current_user)

    try:
        room = await ensure_room_belongs_to_location(session, payload.location_id, payload.room_id)
        await lock_room_reservations(session, payload.room_id)
        ensure_reservation_within_room_capacity(payload, room)
        await ensure_no_conflict(session, payload, reservation_id=reservation_id)
    except LocationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoomNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoomLocationMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ReservationCapacityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ReservationConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    reservation.location_id = payload.location_id
    reservation.room_id = payload.room_id
    reservation.start_at = payload.start_at
    reservation.end_at = payload.end_at
    reservation.responsible = payload.responsible
    reservation.coffee = payload.coffee
    reservation.attendees = payload.attendees
    reservation.description = payload.description
    await commit_or_conflict(session, "Reservation could not be persisted because related data changed.")

    refreshed = await session.execute(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .options(selectinload(Reservation.location), selectinload(Reservation.room))
    )
    return reservation_to_read(refreshed.scalar_one())


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reservation(
    reservation_id: str,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> Response:
    reservation = await session.get(Reservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")
    ensure_reservation_owner(reservation, current_user)
    await session.delete(reservation)
    await commit_or_conflict(session, "Reservation could not be deleted because related data changed.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reservations/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_reservations(
    payload: BulkDeleteRequest,
    current_user: CurrentUserDep,
    session: SessionDep,
) -> BulkDeleteResponse:
    foreign_reservation = await session.scalar(
        select(Reservation.id)
        .where(Reservation.id.in_(payload.ids), Reservation.created_by_user_id != current_user.id)
        .limit(1)
    )
    if foreign_reservation is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only reservation owners can delete selected reservations.",
        )

    result = await session.execute(delete(Reservation).where(Reservation.id.in_(payload.ids)))
    await commit_or_conflict(session, "Selected reservations could not be deleted because related data changed.")
    deleted_count = int(getattr(result, "rowcount", 0) or 0)
    return BulkDeleteResponse(deleted=deleted_count)
