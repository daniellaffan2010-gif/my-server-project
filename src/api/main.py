"""
API Server — entry point for the Autonomous Infrastructure Intelligence Platform.
Accepts diagnostic requests, pushes jobs to Redis, reads results from PostgreSQL.
"""

import os
import json
import uuid
import redis
import psycopg2
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="Infra Intelligence API", version="0.1.0")
Instrumentator().instrument(app).expose(app)

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


PROMETHEUS_URL = "http://prometheus:9090"
LOKI_URL = "http://loki:3100"


async def prom_query(q: str) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": q})
            result = r.json()["data"]["result"]
            if not result:
                return None
            v = float(result[0]["value"][1])
            return None if (v != v or v == float('inf') or v == float('-inf')) else v
    except Exception:
        return None


async def loki_query(limit: int = 50) -> list:
    """Query recent logs from all containers"""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    "query": '{job=~"infra_.*"}',
                    "start": "1h",
                    "limit": limit
                }
            )
            result = r.json()
            if result.get("status") != "success":
                return []

            logs = []
            for stream in result.get("data", {}).get("result", []):
                container = stream.get("stream", {}).get("container", "unknown")
                for timestamp, message in stream.get("values", []):
                    logs.append({
                        "container": container,
                        "timestamp": timestamp,
                        "message": message
                    })

            logs.sort(key=lambda x: x["timestamp"], reverse=True)
            return logs[:limit]
    except Exception:
        return []


@app.get("/stats")
async def stats():
    req_rate     = await prom_query('round(sum(rate(http_requests_total[1m])), 0.001)')
    error_rate   = await prom_query('round(sum(rate(http_requests_total{status_code=~"5.."}[1m])) / sum(rate(http_requests_total[1m])) * 100, 0.01)')
    latency_p95  = await prom_query('round(histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le)), 0.001)')
    jobs_total   = await prom_query('sum(worker_jobs_processed_total)')
    job_duration = await prom_query('round(rate(worker_job_duration_seconds_sum[5m]) / rate(worker_job_duration_seconds_count[5m]), 0.001)')
    redis_mem    = await prom_query('redis_memory_used_bytes')
    redis_cmds   = await prom_query('round(rate(redis_commands_processed_total[1m]), 0.01)')
    pg_up        = await prom_query('pg_up')
    redis_up     = await prom_query('redis_up')
    return {
        "api_request_rate":    req_rate,
        "api_error_rate_pct":  error_rate,
        "api_latency_p95_s":   latency_p95,
        "worker_jobs_total":   jobs_total,
        "worker_job_duration_s": job_duration,
        "redis_memory_bytes":  redis_mem,
        "redis_commands_per_s": redis_cmds,
        "postgres_up":         pg_up,
        "redis_up":            redis_up,
    }


@app.get("/logs")
async def get_logs():
    """Fetch recent logs from Loki"""
    logs = await loki_query(30)
    return {"logs": logs}


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Infra Dashboard</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1117; color: #e0e0e0; padding: 24px; }
    h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 20px; color: #fff; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
    .card { background: #1a1d2e; border-radius: 12px; padding: 20px; border: 1px solid #2a2d3e; }
    .card .label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
    .card .value { font-size: 2rem; font-weight: 700; color: #fff; }
    .card .unit { font-size: 0.85rem; color: #666; margin-left: 4px; }
    .card.green .value { color: #4ade80; }
    .card.red .value { color: #f87171; }
    .card.blue .value { color: #60a5fa; }
    .card.yellow .value { color: #fbbf24; }
    .card.purple .value { color: #c084fc; }
    .status { display: inline-block; padding: 2px 10px; border-radius: 99px; font-size: 0.85rem; font-weight: 600; }
    .up { background: #14532d; color: #4ade80; }
    .down { background: #450a0a; color: #f87171; }
    .na { background: #1f2937; color: #6b7280; }
    footer { margin-top: 20px; font-size: 0.75rem; color: #444; }
    #updated { color: #555; }
  </style>
</head>
<body>
  <h1>Infra Intelligence Platform</h1>
  <div class="grid" id="grid">Loading...</div>
  <h2 style="margin-top: 40px; margin-bottom: 16px; font-size: 1.1rem;">Recent Logs</h2>
  <div id="logs" style="background: #1a1d2e; border-radius: 12px; padding: 16px; border: 1px solid #2a2d3e; font-size: 0.85rem; line-height: 1.6; max-height: 400px; overflow-y: auto; font-family: 'Monaco', 'Menlo', monospace;">Loading logs...</div>
  <footer>Auto-refreshes every 5s &nbsp;|&nbsp; Last updated: <span id="updated">—</span></footer>
  <script>
    function fmt(v, decimals=2) { return v == null ? "N/A" : Number(v).toFixed(decimals); }
    function fmtBytes(b) { if (b == null) return "N/A"; if (b > 1048576) return (b/1048576).toFixed(1) + " MB"; return (b/1024).toFixed(1) + " KB"; }
    function statusBadge(v) {
      if (v == null) return '<span class="status na">N/A</span>';
      return v >= 1 ? '<span class="status up">UP</span>' : '<span class="status down">DOWN</span>';
    }
    async function refresh() {
      try {
        const r = await fetch("/stats");
        const d = await r.json();
        document.getElementById("grid").innerHTML = `
          <div class="card blue">
            <div class="label">API Request Rate</div>
            <div class="value">${fmt(d.api_request_rate, 3)}<span class="unit">req/s</span></div>
          </div>
          <div class="card ${d.api_error_rate_pct > 1 ? 'red' : 'green'}">
            <div class="label">API Error Rate</div>
            <div class="value">${fmt(d.api_error_rate_pct, 2)}<span class="unit">%</span></div>
          </div>
          <div class="card yellow">
            <div class="label">API Latency p95</div>
            <div class="value">${fmt(d.api_latency_p95_s, 3)}<span class="unit">s</span></div>
          </div>
          <div class="card green">
            <div class="label">Worker Jobs Total</div>
            <div class="value">${d.worker_jobs_total != null ? Math.round(d.worker_jobs_total) : "N/A"}</div>
          </div>
          <div class="card blue">
            <div class="label">Worker Avg Job Duration</div>
            <div class="value">${fmt(d.worker_job_duration_s, 3)}<span class="unit">s</span></div>
          </div>
          <div class="card purple">
            <div class="label">Redis Memory</div>
            <div class="value">${fmtBytes(d.redis_memory_bytes)}</div>
          </div>
          <div class="card yellow">
            <div class="label">Redis Commands/s</div>
            <div class="value">${fmt(d.redis_commands_per_s, 1)}<span class="unit">cmd/s</span></div>
          </div>
          <div class="card">
            <div class="label">PostgreSQL</div>
            <div class="value">${statusBadge(d.postgres_up)}</div>
          </div>
          <div class="card">
            <div class="label">Redis</div>
            <div class="value">${statusBadge(d.redis_up)}</div>
          </div>
        `;
        document.getElementById("updated").textContent = new Date().toLocaleTimeString();

        const lr = await fetch("/logs");
        const ld = await lr.json();
        const logsHtml = ld.logs.length === 0
          ? '<span style="color: #666;">No logs yet...</span>'
          : ld.logs.map(log => {
            const ts = new Date(parseInt(log.timestamp) / 1000000).toLocaleTimeString();
            const container = log.container.replace('infra_', '');
            const colors = { api: '#60a5fa', worker: '#4ade80', db: '#f87171', cache: '#fbbf24' };
            const color = colors[container] || '#888';
            return `<div><span style="color: ${color}; font-weight: 600;">[${container}]</span> <span style="color: #999;">${ts}</span> ${log.message}</div>`;
          }).join('');
        document.getElementById("logs").innerHTML = logsHtml;
      } catch(e) { console.error(e); }
    }
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""


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
