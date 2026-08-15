# Scalable Data Ingestion

Local development project for importing CSV data through a React frontend, FastAPI API, Celery workers, PostgreSQL, RabbitMQ, Redis, and Floci S3.

## Setup

1. Optionally copy `backend/.env.example` to `backend/.env` and adjust local ports or credentials.
2. Start the local services:

   ```sh
   cd backend
   docker compose -f compose.yml up --build
   ```

3. Verify the API:

   ```sh
   curl http://localhost:8000/health
   ```
