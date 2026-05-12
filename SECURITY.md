# Security Policy

## Supported Version

Security maintenance targets the latest published `main` release of `aurum-reservation-service`.

## Security Controls

- Domain routes require `Authorization: Bearer <JWT>`.
- JWT validation is local and checks signature, issuer, audience and expiration.
- The JWT secret must be supplied by the orchestrator through `JWT_SECRET`.
- Use the same production secret configured in `auth-service` as `Jwt__Secret`.
- Reservation conflict validation is protected by a PostgreSQL advisory transaction lock per room.
- CORS must allow only the trusted frontend/BFF origin.
- The API emits hardening headers for JSON responses and a docs-specific CSP for local OpenAPI.

## Reporting

Open a private security advisory or contact the repository owner before publishing exploit details.
Do not include real secrets, production tokens or personal data in public issues.

## Validation

Run the Docker-only quality gate before every release:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace python:3.13-slim \
  sh -lc "python -m pip install --upgrade pip --root-user-action=ignore && python -m pip install --root-user-action=ignore -e '.[dev]' && ruff check app tests && pytest tests --cov=app"
```
