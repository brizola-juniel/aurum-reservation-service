# Reservation Service

Microsserviço Python responsável por locais, salas, reservas e validação de conflitos de horário.

## Responsabilidade

- Persistir locais, salas e reservas em banco relacional próprio.
- Proteger todas as rotas de domínio com JWT.
- Validar localmente o JWT emitido pelo `auth-service`.
- Validar conflito de horário na mesma sala e local.
- Expor CRUD REST de locais, salas e reservas.
- Expor exclusão em lote de reservas.

## Stack

- Python 3.13.
- FastAPI 0.136.
- Pydantic v2.
- SQLAlchemy 2 async.
- Alembic 1.17 para migrations versionadas.
- PostgreSQL via asyncpg.
- pytest 9, pytest-asyncio, ruff e coverage.

## Variáveis

```env
DATABASE_URL=postgresql+asyncpg://reservations:reservations@localhost:5434/reservationsdb
JWT_SECRET=change-this-shared-secret-with-at-least-32-bytes
JWT_ISSUER=aurum-auth-service
JWT_AUDIENCE=aurum-reservation-system
CORS_ALLOWED_ORIGINS=http://localhost:3000
```

`JWT_SECRET` precisa ser o mesmo valor de `Jwt__Secret` no C#.

## Endpoints

- `GET /health`
- `GET /api/locations`
- `POST /api/locations`
- `PUT /api/locations/{id}`
- `DELETE /api/locations/{id}`
- `GET /api/rooms`
- `POST /api/rooms`
- `PUT /api/rooms/{id}`
- `DELETE /api/rooms/{id}`
- `GET /api/reservations`
- `POST /api/reservations`
- `PUT /api/reservations/{id}`
- `DELETE /api/reservations/{id}`
- `POST /api/reservations/bulk-delete`

## Regra de conflito

Uma reserva conflita quando existe outra reserva na mesma sala e local com:

```text
existing.start_at < requested.end_at
existing.end_at > requested.start_at
```

Nesse caso a API responde `409 Conflict` e não grava a reserva.

Em PostgreSQL, criação e edição de reserva também adquirem `pg_advisory_xact_lock`
por sala antes da validação, mantendo a regra protegida contra concorrência dentro
da transação.

## Migrations

- Migration inicial: `alembic/versions/202605120001_initial_reservation_schema.py`.
- Histórico em runtime: tabela PostgreSQL `alembic_version`.
- No PostgreSQL, o schema é aplicado por `../scripts/reservation-migration-apply.sh`.
- O startup executa `alembic upgrade head` como proteção idempotente.
- Nos testes SQLite em container, o schema efêmero usa `Base.metadata.create_all`.

Comandos oficiais pela raiz do monorepo:

```bash
./scripts/reservation-migration-add.sh "add reservation audit columns"
./scripts/reservation-migration-apply.sh
./scripts/migrations-status.sh
```

Esses comandos usam Docker/Compose e Python 3.13 em container; não exigem venv nem Alembic
instalado no host.

## Seed

Depois das migrations, ao iniciar com banco vazio, o serviço cria:

- Matriz Sao Paulo
- Filial Campinas
- Sala Aurum
- Sala Prata
- Sala Executive

## Rodar via Docker

```bash
./scripts/dev-up.sh
```

Este projeto é Docker-first. Não é necessário instalar Python, venv ou PostgreSQL no host.

## Testes

Na raiz:

```bash
./scripts/test-all.sh
```

Somente este serviço:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace/reservation-service python:3.13-slim \
  sh -lc "python -m pip install --upgrade pip && python -m pip install -e '.[dev]' && ruff check app tests && pytest tests --cov=app"
```
