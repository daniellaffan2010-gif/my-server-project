#!/usr/bin/env python3
"""
Failure Injection Script — Deliberately break infrastructure for testing diagnostics.

Usage:
  python failure_injection.py crash_api       # Kill API container
  python failure_injection.py crash_worker    # Kill worker container
  python failure_injection.py crash_db        # Kill database container
  python failure_injection.py crash_cache     # Kill Redis cache
  python failure_injection.py slow_api        # Make API slow (10s latency)
  python failure_injection.py high_cpu        # Spike CPU usage
  python failure_injection.py high_memory     # Consume lots of memory
  python failure_injection.py full_disk       # Fill disk space
  python failure_injection.py db_timeout      # Timeout DB connections
  python failure_injection.py network_delay   # Add network latency
  python failure_injection.py recover         # Recover all services
"""

import subprocess
import sys
import time
import os
import signal

def run_cmd(cmd, capture=False):
    """Run shell command"""
    try:
        if capture:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True).stdout
        else:
            subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e}")
        sys.exit(1)

def crash_api():
    """Kill the API container"""
    print("💥 Crashing API server...")
    run_cmd("docker compose kill infra_api")
    print("✓ API server killed")

def crash_worker():
    """Kill the worker container"""
    print("💥 Crashing worker service...")
    run_cmd("docker compose kill infra_worker")
    print("✓ Worker service killed")

def crash_db():
    """Kill the database container"""
    print("💥 Crashing database...")
    run_cmd("docker compose kill infra_db")
    print("✓ Database killed")

def crash_cache():
    """Kill the Redis cache"""
    print("💥 Crashing Redis cache...")
    run_cmd("docker compose kill infra_cache")
    print("✓ Cache killed")

def slow_api():
    """Inject 10 second latency into API responses"""
    print("🐢 Making API slow (adding 10s latency)...")
    # Add network delay on API container
    run_cmd("docker exec infra_api bash -c 'tc qdisc add dev eth0 root netem delay 10000ms 2>/dev/null || tc qdisc replace dev eth0 root netem delay 10000ms'")
    print("✓ API will now respond slowly")

def high_cpu():
    """Spike CPU usage in worker container"""
    print("🔥 Spiking CPU usage...")
    # Run CPU-intensive task in background
    run_cmd("docker exec -d infra_worker python3 -c 'import hashlib; [hashlib.sha256(str(i).encode()).hexdigest() for i in range(100000000)]'")
    print("✓ CPU spike initiated")

def high_memory():
    """Consume lots of memory in worker"""
    print("💾 Consuming memory...")
    run_cmd("docker exec -d infra_worker python3 -c 'x = [i for i in range(10000000)]; import time; time.sleep(60)'")
    print("✓ Memory pressure applied")

def full_disk():
    """Fill up disk space"""
    print("💿 Filling disk space...")
    run_cmd("docker exec infra_api dd if=/dev/zero of=/tmp/fill.txt bs=1M count=500 2>/dev/null || true")
    print("✓ Disk is now nearly full")

def db_timeout():
    """Simulate database connection timeouts"""
    print("⏱️  Blocking database connections...")
    # Drop connections to port 5432
    run_cmd("docker exec infra_db iptables -A INPUT -p tcp --dport 5432 -j DROP 2>/dev/null || true")
    print("✓ Database connections now timeout")

def network_delay():
    """Add network latency to database communication"""
    print("🌐 Adding network delay...")
    run_cmd("docker exec infra_api bash -c 'tc qdisc add dev eth0 root netem delay 5000ms 2>/dev/null || tc qdisc replace dev eth0 root netem delay 5000ms'")
    print("✓ Network latency added to API")

def recover():
    """Recover all services"""
    print("🔧 Recovering all services...")

    # Restart all containers
    run_cmd("docker compose restart infra_api infra_worker infra_db infra_cache")

    # Remove network delays
    try:
        run_cmd("docker exec infra_api tc qdisc del dev eth0 root 2>/dev/null || true")
    except:
        pass

    # Wait for services to come back
    print("⏳ Waiting for services to recover...")
    time.sleep(10)

    print("✓ All services recovered!")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    failure = sys.argv[1].lower()

    failures = {
        "crash_api": crash_api,
        "crash_worker": crash_worker,
        "crash_db": crash_db,
        "crash_cache": crash_cache,
        "slow_api": slow_api,
        "high_cpu": high_cpu,
        "high_memory": high_memory,
        "full_disk": full_disk,
        "db_timeout": db_timeout,
        "network_delay": network_delay,
        "recover": recover,
    }

    if failure not in failures:
        print(f"❌ Unknown failure: {failure}")
        print(f"Available failures: {', '.join(failures.keys())}")
        sys.exit(1)

    print(f"\n{'='*50}")
    print(f"FAILURE INJECTION: {failure.upper()}")
    print(f"{'='*50}\n")

    failures[failure]()

    print(f"\n{'='*50}")
    print("Failure injected! Check your dashboard for alerts.")
    print("Run 'python scripts/failure_injection.py recover' to restore.")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
