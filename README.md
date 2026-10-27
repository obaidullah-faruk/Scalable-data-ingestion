# Scalable Data Ingestion

A local CSV ingestion project using React with Material UI, FastAPI, PostgreSQL, Floci S3, RabbitMQ, Celery, and Redis.

The current implementation creates a durable ingestion run, splits a CSV into 8 MiB parts in the browser, uploads up to three parts concurrently, and finalizes the multipart upload directly with Floci. FastAPI receives metadata and ETags only; it never receives CSV bytes.

![Scalable Data Ingestion screenshot](sample.png)

## Run locally

```sh
cd backend
docker compose up --build
```

Open `http://localhost:8000`. Nginx serves the page at `/` and the API at `/api`. Floci listens on `http://localhost:4566`.

To work on the frontend with live reload, keep Compose running and start the React dev server, pointed at the API:

```sh
cd frontend
npm install
REACT_APP_API_BASE_URL=http://localhost:8000 npm start
```

The dev server runs on `http://localhost:3000`, so set `REACT_ORIGIN=http://localhost:3000` in `backend/.env` and restart Compose. That origin is then the one FastAPI and the bucket's CORS policy allow.

## Architecture

```mermaid
flowchart LR
    Browser[React browser] -->|HTTP / SSE| Nginx[Nginx :8000]
    Nginx -->|/| Frontend[Static frontend]
    Nginx -->|/api| API1[FastAPI replica 1]
    Nginx -->|/api| API2[FastAPI replica 2]
    API1 --> Postgres[(PostgreSQL)]
    API2 --> Postgres
    API1 --> RabbitMQ{{RabbitMQ\nshared queue}}
    API2 --> RabbitMQ
    RabbitMQ --> Worker1[Celery worker 1]
    RabbitMQ --> Worker2[Celery worker 2]
    Worker1 --> Postgres
    Worker2 --> Postgres
    Worker1 --> Redis[(Redis)]
    Worker2 --> Redis
    API1 --> Floci[(Floci S3)]
    API2 --> Floci
    Worker1 --> Floci
    Worker2 --> Floci
    Browser -->|presigned multipart upload| Floci
```

Docker Compose runs two FastAPI replicas and the static frontend behind Nginx, and two Celery workers consuming from the same RabbitMQ broker and queue. Nginx publishes the app port (`APP_PORT`, default `8000`).

The browser validates the selected CSV and configured size limit, creates an ingestion run, requests presigned part URLs in bounded batches, records each returned `ETag`, and offers individual retries for failed parts. It then submits the ordered manifest directly to Floci through a signed completion request. FastAPI verifies the final key, size, and ETag with `HeadObject` before recording upload confirmation. Celery independently validates, loads, and summarizes the CSV from S3, writing restart-safe checkpoints and result rows to PostgreSQL. Upload state is keyed by run UUID, so the same filename can be uploaded independently.

## Configuration

The backend reads its settings from environment variables only. They are listed in `backend/.env.example`. Leave `S3_ENDPOINT_URL` and `S3_BROWSER_ENDPOINT_URL` unset to use AWS S3.

The frontend calls the API on the page's own host. The build accepts two optional settings:

```sh
REACT_APP_API_BASE_URL=http://localhost:8000   # API on another host
REACT_APP_MAX_UPLOAD_SIZE_BYTES=5368709120
```

The frontend maximum should match backend `MAX_UPLOAD_SIZE_BYTES`.
Workers checkpoint every `WORKER_CHECKPOINT_ROWS` records (default `1000`) and retry transient S3/database faults up to `WORKER_MAX_RETRIES` times (default `3`).

## Images

| Image | Build context | Runs as | Serves |
|---|---|---|---|
| backend | `backend/` | uid 10001 | api by default; worker, migrations, S3 setup and reconcile by overriding the command |
| frontend | `frontend/` | uid 101 | the static build on port 8080 |

The api reports its health on two endpoints:

- `GET /health/live`: the process is up. It never checks dependencies.
- `GET /health/ready`: PostgreSQL answers. Returns `503` when it does not.

`GET /metrics` serves Prometheus metrics: request counts by route, method and status, and request latency. An SSE stream counts as one request; the time it stays open is not counted as latency.

Every backend process logs one JSON object per line, at the level set by `LOG_LEVEL`. Lines about an ingestion run carry `run_id`, and lines about one of its tasks also carry `task_id` and `task_type`, so one upload can be followed from the api to the workers.

## Useful commands

```sh
# Backend tests, in a virtual environment with the dev requirements
cd backend
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest -q

# Floci bucket, CORS, and multipart-completion smoke test (with Compose running)
set -a; source .env.example; set +a
.venv/bin/python -m app.scripts.smoke_test_s3

# Republish durable queued tasks left without a Celery ID (for example after an API stop)
docker compose run --rm --no-deps api python -m app.scripts.reconcile_queued_tasks

# Frontend lint, tests and production build
cd ../frontend
npm run lint
CI=true npm test -- --runInBand
npm run build
```

## CI and releases

Every pull request runs `.github/workflows/ci.yml`:

| Job | Checks |
|---|---|
| backend | ruff, pytest, and `alembic upgrade head` plus `alembic check` against PostgreSQL |
| frontend | ESLint, tests and a production build |
| hadolint, actionlint | Dockerfiles and workflows |
| image | Builds each image for arm64 and fails on fixable HIGH or CRITICAL vulnerabilities |
| ci-passed | Passes only when every job above passed. Branch rules require this one check. |

A vulnerability that doesn't affect the app can be accepted in `.trivyignore`, with the reason next to it.

Delivery goes through the [gitops repo](https://github.com/obaidullah-faruk/Scalable-Data-Ingestion-gitops). CI never gets cluster credentials; ArgoCD pulls from Git.

1. **Merge to `main`:** `.github/workflows/release.yml` builds both images for arm64, pushes them to Docker Hub tagged with the commit SHA, and sets their tag and digest in the gitops repo's dev overlay. ArgoCD deploys dev.
2. **Tag `vX.Y.Z` on a commit of `main`:** `.github/workflows/promote.yml` opens a pull request that sets the same digests in the prod overlay. Nothing is rebuilt. Prod changes once the owner approves and merges it.
3. **Roll back:** revert the overlay commit in the gitops repo.

The workflows write to the gitops repo as a GitHub App installed only there. They need:

| Name | Kind | Value |
|---|---|---|
| `DOCKERHUB_TOKEN` | secret | Docker Hub access token with Read & Write access |
| `GITOPS_APP_CLIENT_ID` | variable | the App's Client ID |
| `GITOPS_APP_PRIVATE_KEY` | secret | a private key of the App |

Third-party actions are pinned to a commit SHA, with the version in a comment.

Database migrations run automatically before the API and worker start. After changing SQLAlchemy models, generate and apply a migration from `backend/`:

```sh
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## Upload endpoints

- `POST /api/v1/ingestion-runs` — validate metadata and create a unique multipart upload.
- `POST /api/v1/ingestion-runs/{run_id}/part-urls` — issue a bounded set of presigned part URLs.
- `POST /api/v1/ingestion-runs/{run_id}/completion-request` — validate the ordered ETag manifest and sign the S3 completion request.
- `POST /api/v1/ingestion-runs/{run_id}/confirm-upload` — verify the finalized object, create the three durable processing tasks, and queue them in Celery.
- `GET /api/v1/ingestion-runs/{run_id}` — retrieve the durable processing snapshot and result summaries.
- `GET /api/v1/ingestion-runs/{run_id}/events` — receive the snapshot first, then live committed processing progress over SSE.
- `POST /api/v1/ingestion-runs/{run_id}/abort` — discard an unfinished multipart upload.
