from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
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
    ReservationConflictError,
    RoomLocationMismatchError,
    ensure_no_conflict,
    ensure_room_belongs_to_location,
)

router = APIRouter(prefix="/api")
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
LocationFilter = Annotated[str | None, Query()]


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
    location = Location(name=payload.name.strip(), address=payload.address)
    session.add(location)
    await session.commit()
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
    location.name = payload.name.strip()
    location.address = payload.address
    await session.commit()
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
    await session.commit()
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
    room = Room(
        location_id=payload.location_id,
        name=payload.name.strip(),
        capacity=payload.capacity,
    )
    session.add(room)
    await session.commit()
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
    room.location_id = payload.location_id
    room.name = payload.name.strip()
    room.capacity = payload.capacity
    await session.commit()
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
    await session.commit()
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
        await lock_room_reservations(session, payload.room_id)
        await ensure_room_belongs_to_location(session, payload.location_id, payload.room_id)
        await ensure_no_conflict(session, payload)
    except RoomLocationMismatchError as exc:
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
        responsible=payload.responsible.strip(),
        coffee=payload.coffee,
        attendees=payload.attendees,
        description=payload.description,
        created_by_user_id=current_user.id,
        created_by_email=current_user.email,
    )
    session.add(reservation)
    await session.commit()

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
    _: CurrentUserDep,
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

    try:
        await lock_room_reservations(session, payload.room_id)
        await ensure_room_belongs_to_location(session, payload.location_id, payload.room_id)
        await ensure_no_conflict(session, payload, reservation_id=reservation_id)
    except RoomLocationMismatchError as exc:
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
    reservation.responsible = payload.responsible.strip()
    reservation.coffee = payload.coffee
    reservation.attendees = payload.attendees
    reservation.description = payload.description
    await session.commit()

    refreshed = await session.execute(
        select(Reservation)
        .where(Reservation.id == reservation_id)
        .options(selectinload(Reservation.location), selectinload(Reservation.room))
    )
    return reservation_to_read(refreshed.scalar_one())


@router.delete("/reservations/{reservation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reservation(
    reservation_id: str,
    _: CurrentUserDep,
    session: SessionDep,
) -> Response:
    reservation = await session.get(Reservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reservation not found")
    await session.delete(reservation)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reservations/bulk-delete", response_model=BulkDeleteResponse)
async def bulk_delete_reservations(
    payload: BulkDeleteRequest,
    _: CurrentUserDep,
    session: SessionDep,
) -> BulkDeleteResponse:
    result = await session.execute(delete(Reservation).where(Reservation.id.in_(payload.ids)))
    await session.commit()
    return BulkDeleteResponse(deleted=result.rowcount or 0)
