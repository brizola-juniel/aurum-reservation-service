# Aurum Reservation Service

Microsserviço Python responsável por locais, salas, reservas e validação de conflitos de horário.

Este diretório funciona em dois formatos:

- `reservation-service/` dentro do monorepo `reservation-system`.
- Repositório standalone exportado como `aurum-reservation-service`.

Todos os comandos oficiais são Docker-first. Não use Python, venv, Alembic ou PostgreSQL
instalados no host como caminho de validação.

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
- GitHub Actions Docker-only com `python:3.13-slim`.
- Dependabot para pip, GitHub Actions e Docker.

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

No repositório standalone, aplique migrations com o mesmo padrão Docker-first do runtime:

```bash
docker run --rm --env-file .env --network host aurum-reservation-service:local \
  alembic upgrade head
```

## Seed

Depois das migrations, ao iniciar com banco vazio, o serviço cria:

- Matriz Sao Paulo
- Filial Campinas
- Sala Aurum
- Sala Prata
- Sala Executive

## Rodar via Docker

Pela raiz do monorepo:

```bash
./scripts/dev-up.sh
```

No repositório standalone:

```bash
docker build --pull --no-cache -t aurum-reservation-service:local .
docker run --rm --env-file .env -p 8000:8000 aurum-reservation-service:local
```

## Testes

Gate completo pela raiz do monorepo:

```bash
./scripts/test-all.sh
```

Gate focado no serviço pela raiz do monorepo:

```bash
docker run --rm -v "$PWD/reservation-service:/src:ro" -w /workspace python:3.13-slim \
  sh -lc "cp -a /src/. /workspace && python -m pip install --upgrade pip && python -m pip install -e '.[dev]' && ruff check app tests && pytest tests --cov=app --cov-report=term-missing --cov-fail-under=68"
```

Gate focado no repositório standalone:

```bash
docker run --rm -v "$PWD:/src:ro" -w /workspace python:3.13-slim \
  sh -lc "cp -a /src/. /workspace && python -m pip install --upgrade pip && python -m pip install -e '.[dev]' && ruff check app tests && pytest tests --cov=app --cov-report=term-missing --cov-fail-under=68"
```

Build da imagem runtime no repositório standalone:

```bash
docker build --pull --no-cache -t aurum-reservation-service:local .
```

## CI e governança

O repositório standalone inclui:

- `.github/workflows/ci.yml`: lint, testes, coverage e build de imagem em Docker.
- `.github/dependabot.yml`: atualizações semanais para pip, Actions e Docker.
- `SECURITY.md`: política operacional e checklist de segurança.
- `TESTING.md`: comandos Docker-only para validação local e CI.

O job bloqueante do CI usa `ruff` e `pytest` com coverage mínimo de 68%, que corresponde
ao baseline atual do serviço. `mypy` roda como sinal não bloqueante porque há dívida de
tipagem existente fora deste escopo de endurecimento documental/CI.
