# Scalable Data Ingestion

Importing CSV data through a React frontend, FastAPI API, Celery workers, PostgreSQL, RabbitMQ, Redis, and Floci S3.

## Setup

1. Optionally copy `backend/.env.example` to `backend/.env` and adjust local ports or credentials.
2. Start the local services:

   ```sh
   cd backend
   docker compose -f docker-compose.yml up --build
   ```

   The API and worker apply the database schema migration before starting. To apply it explicitly from the host:

   ```sh
   cd backend
   alembic upgrade head
   ```

   Alembic reads the database connection from `backend/.env`. When `DATABASE_URL` is not set, it builds the URL from `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, and `POSTGRES_DB`.

3. Verify the API:

   ```sh
   curl http://localhost:8000/health
   ```

## Database migrations

After changing the SQLAlchemy models, generate a proposed migration:

```sh
cd backend
alembic revision --autogenerate -m "add content hash"
```

Review the generated file under `backend/alembic/versions/`, then apply it:

```sh
alembic upgrade head
```

To preview the SQL without connecting to or changing the database:

```sh
alembic upgrade head --sql
```

If Alembic is not installed on the host, migrations can still be applied through the backend image after starting PostgreSQL:

```sh
cd backend
docker compose run --rm api alembic upgrade head
```

Generate revision files from the host environment so the new file is written into your local `backend/alembic/versions/` directory.

## Floci S3 bucket and CORS

Compose runs the one-shot `floci-init` service before the API and worker. It idempotently creates the `S3_UPLOAD_BUCKET` bucket and applies a CORS policy for `REACT_ORIGIN`. The policy permits browser multipart `PUT`, completion `POST`, and `HEAD` requests, and exposes the `ETag` response header.

After the Compose stack is running, verify the bucket, object round-trip, simulated browser preflight, and browser-readable `ETag`:

```sh
cd backend
docker compose run --rm --no-deps floci-init python -m app.scripts.smoke_test_s3
```

The Floci credentials are dummy local credentials used only by backend containers. They are never sent to React; later phases give React short-lived presigned requests instead.
