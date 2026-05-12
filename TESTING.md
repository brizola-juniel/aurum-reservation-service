# Testing

This repository is Docker-first. The host only needs Docker.

## Standalone Repository

From the `aurum-reservation-service` repository root:

```bash
docker run --rm -v "$PWD:/workspace" -w /workspace python:3.13-slim \
  sh -lc "python -m pip install --upgrade pip --root-user-action=ignore && python -m pip install --root-user-action=ignore -e '.[dev]' && ruff check app tests && pytest tests --cov=app"
```

## Monorepo Workspace

From `/home/juniel/Documentos/reservation-system`:

```bash
./scripts/test-all.sh
./scripts/audit-sbom.sh
```

## CI Gate

GitHub Actions runs `.github/workflows/ci.yml` on `push` to `main` and pull requests.
The required status check is named `reservation-quality`.
