from datetime import UTC, datetime, timedelta

from httpx import AsyncClient


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


async def create_location_and_room(client: AsyncClient, auth_header: dict[str, str]) -> tuple[str, str]:
    location_response = await client.post(
        "/api/locations",
        json={"name": "Matriz Sao Paulo", "address": "Av. Paulista, 1000"},
        headers=auth_header,
    )
    assert location_response.status_code == 201
    location_id = location_response.json()["id"]

    room_response = await client.post(
        "/api/rooms",
        json={"location_id": location_id, "name": "Sala Aurum", "capacity": 12},
        headers=auth_header,
    )
    assert room_response.status_code == 201
    return location_id, room_response.json()["id"]


async def test_requires_valid_jwt(client: AsyncClient) -> None:
    response = await client.get("/api/reservations")

    assert response.status_code == 401


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
