"""
Worker Service — pulls diagnostic jobs from the Redis queue and processes them.
In Year 1 this does a simple rule-based check; later it will call the Claude agent.
"""

import os
import json
import time
import redis
import psycopg2

REDIS_URL = os.environ["REDIS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]

redis_client = redis.from_url(REDIS_URL)


def get_db():
    return psycopg2.connect(DATABASE_URL)


def process_job(job: dict) -> dict:
    """
    Placeholder diagnostic logic.
    Replace this with Claude agent calls in Phase 3.
    """
    logs = job.get("logs", "")
    service = job.get("service_name", "unknown")

    # Naive keyword-based diagnosis (will be replaced by AI agent)
    if "OOMKilled" in logs or "out of memory" in logs.lower():
        diagnosis = "Memory exhaustion"
        recommendation = "Increase container memory limit or investigate memory leak"
    elif "connection refused" in logs.lower():
        diagnosis = "Connectivity failure"
        recommendation = f"Check that dependent services for '{service}' are reachable"
    elif "timeout" in logs.lower():
        diagnosis = "Timeout"
        recommendation = "Check network latency and upstream service health"
    else:
        diagnosis = "Unknown"
        recommendation = "No known pattern matched. Manual inspection required."

    return {"diagnosis": diagnosis, "recommendation": recommendation, "service": service}


def run():
    print("Worker started. Waiting for jobs on Redis queue 'jobs'...")
    while True:
        # Blocking pop — waits up to 5s for a job, then loops
        item = redis_client.blpop("jobs", timeout=5)
        if item is None:
            continue

        _, raw = item
        job = json.loads(raw)
        job_id = job["job_id"]
        print(f"Processing job {job_id} for service '{job.get('service_name')}'")

        result = process_job(job)

        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE incidents SET status = %s, result = %s WHERE id = %s",
                ("complete", json.dumps(result), job_id)
            )
        conn.commit()
        conn.close()

        print(f"Job {job_id} complete: {result['diagnosis']}")


if __name__ == "__main__":
    # Wait briefly for DB to be ready (healthcheck in compose handles this,
    # but a small retry loop guards against edge cases at startup)
    for attempt in range(10):
        try:
            conn = get_db()
            conn.close()
            break
        except Exception:
            print(f"DB not ready yet (attempt {attempt + 1}/10), retrying...")
            time.sleep(3)

    run()
