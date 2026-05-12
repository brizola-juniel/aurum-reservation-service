from datetime import UTC, datetime, timedelta

import jwt
import pytest
from conftest import TEST_SECRET
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError


def build_auth_header(
    *,
    user_id: str = "11111111-1111-1111-1111-111111111111",
    email: str = "dev@aurum.test",
) -> dict[str, str]:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "iss": "aurum-auth-service",
            "aud": "aurum-reservation-system",
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=30),
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class FailingCommitSession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def commit(self) -> None:
        raise IntegrityError("INSERT", {}, Exception("constraint failed"))

    async def rollback(self) -> None:
        self.rolled_back = True


async def test_commit_or_conflict_rolls_back_and_returns_http_409(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    from app.api.routes import commit_or_conflict

    session = FailingCommitSession()

    with pytest.raises(HTTPException) as exc_info:
        await commit_or_conflict(session, "Related data changed.")

    assert session.rolled_back is True
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Related data changed."


async def test_health_includes_enterprise_security_headers(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )
    assert "camera=()" in response.headers["permissions-policy"]


async def create_location_and_room(
    client: AsyncClient,
    auth_header: dict[str, str],
    *,
    room_name: str = "Sala Aurum",
    capacity: int = 12,
) -> tuple[str, str]:
    location_response = await client.post(
        "/api/locations",
        json={"name": "Matriz Sao Paulo", "address": "Av. Paulista, 1000"},
        headers=auth_header,
    )
    assert location_response.status_code == 201
    location_id = location_response.json()["id"]

    room_response = await client.post(
        "/api/rooms",
        json={"location_id": location_id, "name": room_name, "capacity": capacity},
        headers=auth_header,
    )
    assert room_response.status_code == 201
    return location_id, room_response.json()["id"]


async def test_requires_valid_jwt(client: AsyncClient) -> None:
    response = await client.get("/api/reservations")

    assert response.status_code == 401


async def test_rejects_malformed_signed_jwt_claims(client: AsyncClient) -> None:
    bad_subject = await client.get(
        "/api/reservations",
        headers=build_auth_header(user_id="not-a-uuid"),
    )
    assert bad_subject.status_code == 401

    bad_email = await client.get(
        "/api/reservations",
        headers=build_auth_header(email="not-an-email"),
    )
    assert bad_email.status_code == 401


async def test_location_and_room_crud(client: AsyncClient, auth_header: dict[str, str]) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)

    rooms = await client.get(f"/api/rooms?location_id={location_id}", headers=auth_header)
    assert rooms.status_code == 200
    assert rooms.json()[0]["id"] == room_id

    update = await client.put(
        f"/api/rooms/{room_id}",
        json={"location_id": location_id, "name": "Sala Caturra", "capacity": 16},
        headers=auth_header,
    )
    assert update.status_code == 200
    assert update.json()["capacity"] == 16


async def test_reservation_crud_and_conflict_validation(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)
    start = datetime(2026, 5, 12, 13, 0, tzinfo=UTC)
    payload = {
        "location_id": location_id,
        "room_id": room_id,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat(),
        "responsible": "Maria Silva",
        "coffee": True,
        "attendees": 8,
        "description": "Planejamento trimestral",
    }

    created = await client.post("/api/reservations", json=payload, headers=auth_header)
    assert created.status_code == 201
    reservation_id = created.json()["id"]
    assert created.json()["created_by_email"] == "dev@aurum.test"

    conflict = await client.post(
        "/api/reservations",
        json={**payload, "responsible": "Joao Costa"},
        headers=auth_header,
    )
    assert conflict.status_code == 409
    assert "conflict" in conflict.json()["detail"].lower()

    updated = await client.put(
        f"/api/reservations/{reservation_id}",
        json={**payload, "end_at": (start + timedelta(hours=2)).isoformat(), "coffee": False},
        headers=auth_header,
    )
    assert updated.status_code == 200
    assert updated.json()["attendees"] is None

    listed = await client.get("/api/reservations", headers=auth_header)
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    deleted = await client.delete(f"/api/reservations/{reservation_id}", headers=auth_header)
    assert deleted.status_code == 204


async def test_bulk_delete_reservations(client: AsyncClient, auth_header: dict[str, str]) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)
    start = datetime(2026, 5, 13, 9, 0, tzinfo=UTC)
    ids: list[str] = []

    for index in range(2):
        response = await client.post(
            "/api/reservations",
            json={
                "location_id": location_id,
                "room_id": room_id,
                "start_at": (start + timedelta(hours=index * 2)).isoformat(),
                "end_at": (start + timedelta(hours=index * 2 + 1)).isoformat(),
                "responsible": f"Pessoa {index}",
                "coffee": False,
                "description": "Reserva em lote",
            },
            headers=auth_header,
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])

    deleted = await client.post("/api/reservations/bulk-delete", json={"ids": ids}, headers=auth_header)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": 2}


async def test_cross_user_cannot_modify_or_delete_reservations(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    other_user_header = build_auth_header(
        user_id="22222222-2222-2222-2222-222222222222",
        email="other@aurum.test",
    )
    location_id, room_id = await create_location_and_room(client, auth_header)
    start = datetime(2026, 5, 13, 14, 0, tzinfo=UTC)
    payload = {
        "location_id": location_id,
        "room_id": room_id,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat(),
        "responsible": "Maria Silva",
        "coffee": False,
        "description": "Reserva protegida por dono",
    }
    created = await client.post("/api/reservations", json=payload, headers=auth_header)
    assert created.status_code == 201
    reservation_id = created.json()["id"]

    update = await client.put(
        f"/api/reservations/{reservation_id}",
        json={**payload, "responsible": "Outro Usuario"},
        headers=other_user_header,
    )
    assert update.status_code == 403

    bulk_delete = await client.post(
        "/api/reservations/bulk-delete",
        json={"ids": [reservation_id]},
        headers=other_user_header,
    )
    assert bulk_delete.status_code == 403

    delete = await client.delete(f"/api/reservations/{reservation_id}", headers=other_user_header)
    assert delete.status_code == 403

    listed = await client.get("/api/reservations", headers=auth_header)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [reservation_id]

    owner_delete = await client.delete(f"/api/reservations/{reservation_id}", headers=auth_header)
    assert owner_delete.status_code == 204


async def test_room_and_location_delete_are_blocked_when_reservations_exist(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)
    start = datetime(2026, 5, 14, 9, 0, tzinfo=UTC)

    reservation = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "responsible": "Pessoa Protegida",
            "coffee": False,
            "description": "Protege integridade referencial",
        },
        headers=auth_header,
    )
    assert reservation.status_code == 201

    room_delete = await client.delete(f"/api/rooms/{room_id}", headers=auth_header)
    assert room_delete.status_code == 409
    assert "reservations" in room_delete.json()["detail"].lower()

    location_delete = await client.delete(f"/api/locations/{location_id}", headers=auth_header)
    assert location_delete.status_code == 409
    assert "reservations" in location_delete.json()["detail"].lower()


async def test_required_strings_are_trimmed_before_min_length(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location = await client.post(
        "/api/locations",
        json={"name": "  A  ", "address": "  "},
        headers=auth_header,
    )
    assert location.status_code == 422

    location_id, room_id = await create_location_and_room(client, auth_header)
    start = datetime(2026, 5, 15, 9, 0, tzinfo=UTC)
    reservation = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "responsible": "  A  ",
            "coffee": False,
            "description": "  ",
        },
        headers=auth_header,
    )
    assert reservation.status_code == 422


async def test_duplicate_location_and_room_names_return_conflict(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, _ = await create_location_and_room(client, auth_header)

    duplicate_location = await client.post(
        "/api/locations",
        json={"name": "  Matriz Sao Paulo  ", "address": "Outro endereco"},
        headers=auth_header,
    )
    assert duplicate_location.status_code == 409

    duplicate_room = await client.post(
        "/api/rooms",
        json={"location_id": location_id, "name": "  Sala Aurum  ", "capacity": 20},
        headers=auth_header,
    )
    assert duplicate_room.status_code == 409


async def test_duplicate_location_and_room_names_are_case_insensitive(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, _ = await create_location_and_room(client, auth_header)

    duplicate_location = await client.post(
        "/api/locations",
        json={"name": "matriz sao paulo", "address": "Outro endereco"},
        headers=auth_header,
    )
    assert duplicate_location.status_code == 409

    duplicate_room = await client.post(
        "/api/rooms",
        json={"location_id": location_id, "name": "sala aurum", "capacity": 20},
        headers=auth_header,
    )
    assert duplicate_room.status_code == 409

    other_location = await client.post(
        "/api/locations",
        json={"name": "Filial Rio", "address": "Centro"},
        headers=auth_header,
    )
    assert other_location.status_code == 201
    same_room_name_other_location = await client.post(
        "/api/rooms",
        json={"location_id": other_location.json()["id"], "name": "sala aurum", "capacity": 20},
        headers=auth_header,
    )
    assert same_room_name_other_location.status_code == 201


async def test_location_and_room_updates_reject_case_insensitive_name_collisions(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, _ = await create_location_and_room(client, auth_header)
    other_location = await client.post(
        "/api/locations",
        json={"name": "Filial Rio", "address": "Centro"},
        headers=auth_header,
    )
    assert other_location.status_code == 201

    duplicate_location_update = await client.put(
        f"/api/locations/{other_location.json()['id']}",
        json={"name": "matriz sao paulo", "address": "Outro endereco"},
        headers=auth_header,
    )
    assert duplicate_location_update.status_code == 409

    other_room = await client.post(
        "/api/rooms",
        json={"location_id": other_location.json()["id"], "name": "Sala Caturra", "capacity": 20},
        headers=auth_header,
    )
    assert other_room.status_code == 201

    duplicate_room_update = await client.put(
        f"/api/rooms/{other_room.json()['id']}",
        json={"location_id": location_id, "name": "sala aurum", "capacity": 20},
        headers=auth_header,
    )
    assert duplicate_room_update.status_code == 409


async def test_reservation_rejects_naive_datetimes_and_normalizes_timezone_to_utc(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)

    naive = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": "2026-05-16T09:00:00",
            "end_at": "2026-05-16T10:00:00",
            "responsible": "Maria Silva",
            "coffee": False,
        },
        headers=auth_header,
    )
    assert naive.status_code == 422

    offset = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": "2026-05-16T09:00:00-03:00",
            "end_at": "2026-05-16T10:00:00-03:00",
            "responsible": "Maria Silva",
            "coffee": False,
        },
        headers=auth_header,
    )
    assert offset.status_code == 201
    normalized = datetime.fromisoformat(offset.json()["start_at"].replace("Z", "+00:00"))
    assert normalized == datetime(2026, 5, 16, 12, 0, tzinfo=UTC)


async def test_reservation_create_and_update_enforce_room_capacity(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header, capacity=4)
    start = datetime(2026, 5, 17, 9, 0, tzinfo=UTC)
    payload = {
        "location_id": location_id,
        "room_id": room_id,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat(),
        "responsible": "Maria Silva",
        "coffee": True,
        "attendees": 5,
    }

    too_large = await client.post("/api/reservations", json=payload, headers=auth_header)
    assert too_large.status_code == 422
    assert "capacity" in too_large.json()["detail"].lower()

    created = await client.post(
        "/api/reservations",
        json={**payload, "attendees": 4},
        headers=auth_header,
    )
    assert created.status_code == 201

    update_too_large = await client.put(
        f"/api/reservations/{created.json()['id']}",
        json={**payload, "attendees": 5},
        headers=auth_header,
    )
    assert update_too_large.status_code == 422
    assert "capacity" in update_too_large.json()["detail"].lower()


async def test_room_capacity_cannot_be_reduced_below_existing_reservations(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header, capacity=12)
    start = datetime(2026, 5, 18, 9, 0, tzinfo=UTC)

    reservation = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "responsible": "Maria Silva",
            "coffee": True,
            "attendees": 8,
        },
        headers=auth_header,
    )
    assert reservation.status_code == 201

    reduced = await client.put(
        f"/api/rooms/{room_id}",
        json={"location_id": location_id, "name": "Sala Aurum", "capacity": 7},
        headers=auth_header,
    )
    assert reduced.status_code == 409
    assert "capacity" in reduced.json()["detail"].lower()


async def test_room_with_reservations_cannot_move_locations(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)
    other_location = await client.post(
        "/api/locations",
        json={"name": "Filial Campinas", "address": "Rua das Reunioes, 250"},
        headers=auth_header,
    )
    assert other_location.status_code == 201
    start = datetime(2026, 5, 19, 9, 0, tzinfo=UTC)

    reservation = await client.post(
        "/api/reservations",
        json={
            "location_id": location_id,
            "room_id": room_id,
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat(),
            "responsible": "Maria Silva",
            "coffee": False,
        },
        headers=auth_header,
    )
    assert reservation.status_code == 201

    moved = await client.put(
        f"/api/rooms/{room_id}",
        json={"location_id": other_location.json()["id"], "name": "Sala Aurum", "capacity": 12},
        headers=auth_header,
    )
    assert moved.status_code == 409
    assert "moved" in moved.json()["detail"].lower()


async def test_reservation_distinguishes_missing_ids_from_location_room_mismatch(
    client: AsyncClient,
    auth_header: dict[str, str],
) -> None:
    location_id, room_id = await create_location_and_room(client, auth_header)
    other_location = await client.post(
        "/api/locations",
        json={"name": "Filial Campinas", "address": "Rua das Reunioes, 250"},
        headers=auth_header,
    )
    assert other_location.status_code == 201
    start = datetime(2026, 5, 20, 9, 0, tzinfo=UTC)
    payload = {
        "location_id": location_id,
        "room_id": room_id,
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(hours=1)).isoformat(),
        "responsible": "Maria Silva",
        "coffee": False,
    }

    missing_location = await client.post(
        "/api/reservations",
        json={**payload, "location_id": "missing-location"},
        headers=auth_header,
    )
    assert missing_location.status_code == 404
    assert missing_location.json()["detail"] == "Location not found"

    missing_room = await client.post(
        "/api/reservations",
        json={**payload, "room_id": "missing-room"},
        headers=auth_header,
    )
    assert missing_room.status_code == 404
    assert missing_room.json()["detail"] == "Room not found"

    mismatch = await client.post(
        "/api/reservations",
        json={**payload, "location_id": other_location.json()["id"]},
        headers=auth_header,
    )
    assert mismatch.status_code == 422
    assert "does not belong" in mismatch.json()["detail"]
