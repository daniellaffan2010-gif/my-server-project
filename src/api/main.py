"""
API Server — entry point for the Autonomous Infrastructure Intelligence Platform.
Accepts diagnostic requests, pushes jobs to Redis, reads results from PostgreSQL.
"""

import os
import json
import uuid
import redis
import psycopg2
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Infra Intelligence API", version="0.1.0")

REDIS_URL = os.environ["REDIS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]

redis_client = redis.from_url(REDIS_URL)


def get_db():
    return psycopg2.connect(DATABASE_URL)


@app.on_event("startup")
def create_tables():
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                input JSONB,
                result JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()
    conn.close()


class DiagnosticRequest(BaseModel):
    service_name: str
    logs: str
    metrics: dict = {}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/diagnose")
def submit_diagnosis(req: DiagnosticRequest):
    """Submit an infrastructure diagnostic job. Returns a job ID to poll."""
    job_id = str(uuid.uuid4())

    # Record in DB
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO incidents (id, status, input) VALUES (%s, %s, %s)",
            (job_id, "pending", json.dumps(req.dict()))
        )
    conn.commit()
    conn.close()

    # Push job onto Redis queue for the worker to pick up
    redis_client.rpush("jobs", json.dumps({"job_id": job_id, **req.dict()}))

    return {"job_id": job_id, "status": "pending"}


@app.get("/diagnose/{job_id}")
def get_diagnosis(job_id: str):
    """Poll for the result of a diagnostic job."""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, result FROM incidents WHERE id = %s", (job_id,)
        )
        row = cur.fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return {"job_id": job_id, "status": row[0], "result": row[1]}
