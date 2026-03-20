#!/usr/bin/env python3
"""
freshness.py — Pinecone write-to-read latency monitor.

Usage:
  python freshness.py             # enable tracking, show live dashboard
  python freshness.py --off       # disable tracking and exit
  python freshness.py --stats     # print summary stats and exit (no live mode)
  python freshness.py --probe     # direct prober: upsert → poll → measure, no infra

Ctrl-C to stop monitoring (disables tracking automatically).
"""

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import numpy as np
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from dotenv import load_dotenv
from pinecone import Pinecone
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sklearn.preprocessing import normalize

load_dotenv()

AWS_REGION    = os.environ.get("AWS_REGION", "us-east-1")
AWS_PROFILE   = os.environ.get("AWS_PROFILE", "AdministratorAccess-084828598047")
DYNAMO_TABLE  = os.environ.get("DYNAMO_TABLE", "redrum-freshness")
SSM_FLAG_PATH = os.environ.get("SSM_FLAG_PATH", "/redrum/freshness_enabled")
REFRESH_SEC   = float(os.environ.get("DASHBOARD_REFRESH_SECONDS", "2"))
RECENT_ROWS   = int(os.environ.get("DASHBOARD_RECENT_ROWS", "15"))
INDEX_HOST    = os.environ.get("INDEX_HOST", "")
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY", "")
VECTOR_DIM    = int(os.environ.get("VECTOR_DIM", "1024"))
PROBE_INTERVAL  = float(os.environ.get("PROBE_INTERVAL_SECONDS", "5"))
PROBE_POLL      = float(os.environ.get("PROBE_POLL_SECONDS", "0.1"))
LAMBDA_FUNCTION = os.environ.get("LAMBDA_FUNCTION", "redrum-tracker")

session = boto3.Session(region_name=AWS_REGION, profile_name=AWS_PROFILE)
ssm     = session.client("ssm")
ddb     = session.resource("dynamodb")
table   = ddb.Table(DYNAMO_TABLE)


# ---------------------------------------------------------------------------
# SSM toggle
# ---------------------------------------------------------------------------

def set_freshness(enabled: bool):
    value = "true" if enabled else "false"
    ssm.put_parameter(Name=SSM_FLAG_PATH, Value=value, Type="String", Overwrite=True)
    state = "ENABLED" if enabled else "DISABLED"
    print(f"  freshness tracking {state} ({SSM_FLAG_PATH} = {value})")


# ---------------------------------------------------------------------------
# DynamoDB queries
# ---------------------------------------------------------------------------

def fetch_recent(limit: int = RECENT_ROWS) -> list[dict]:
    """Scan for the most recently written records."""
    resp = table.scan(
        FilterExpression=Attr("written_at").exists(),
        Limit=500,
    )
    items = resp.get("Items", [])
    items.sort(key=lambda x: float(x.get("written_at", 0)), reverse=True)
    return items[:limit]


def fetch_all_seen() -> list[int]:
    """Return all latency_ms values for seen records."""
    latencies = []
    kwargs: dict = {
        "FilterExpression": Attr("status").eq("seen"),
        "ProjectionExpression": "latency_ms",
    }
    while True:
        resp = table.scan(**kwargs)
        for item in resp.get("Items", []):
            if "latency_ms" in item:
                latencies.append(int(item["latency_ms"]))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return latencies


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def pct(values: list[int], p: float) -> str:
    if not values:
        return "—"
    return f"{statistics.quantiles(values, n=100)[int(p) - 1] / 1000:.2f}s"


def fmt_ts(epoch: Decimal | float | None) -> str:
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%H:%M:%S")


def status_text(status: str) -> Text:
    if status == "seen":
        return Text("✓ seen", style="green")
    if status == "timeout":
        return Text("✗ timeout", style="red")
    return Text("… pending", style="yellow")


def build_display(items: list[dict], latencies: list[int]) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="stats", size=5),
        Layout(name="table"),
    )

    # --- stats panel ---
    count   = len(latencies)
    timeout = sum(1 for i in items if i.get("status") == "timeout")
    pending = sum(1 for i in items if i.get("status") == "pending")

    stats_text = (
        f"  Samples: [bold]{count}[/bold]    "
        f"p50: [cyan]{pct(latencies, 50)}[/cyan]    "
        f"p95: [cyan]{pct(latencies, 95)}[/cyan]    "
        f"p99: [cyan]{pct(latencies, 99)}[/cyan]    "
        f"timeouts: [{'red' if timeout else 'green'}]{timeout}[/{'red' if timeout else 'green'}]    "
        f"pending: [yellow]{pending}[/yellow]"
    )
    layout["stats"].update(
        Panel(stats_text, title="[bold]Pinecone Freshness Monitor[/bold]",
              subtitle=f"updated {datetime.now().strftime('%H:%M:%S')}")
    )

    # --- recent records table ---
    tbl = Table(show_header=True, header_style="bold", expand=True, show_lines=False)
    tbl.add_column("ID",         style="dim",  width=10)
    tbl.add_column("Written",    width=10)
    tbl.add_column("Seen",       width=10)
    tbl.add_column("Latency",    width=10, justify="right")
    tbl.add_column("Status",     width=12)

    for item in items:
        vid        = item.get("id", "")[:8] + "…"
        written_at = fmt_ts(item.get("written_at"))
        seen_at    = fmt_ts(item.get("seen_at"))
        latency    = f"{int(item['latency_ms']) / 1000:.2f}s" if "latency_ms" in item else "—"
        status     = status_text(item.get("status", "pending"))
        tbl.add_row(vid, written_at, seen_at, latency, status)

    layout["table"].update(Panel(tbl, title="Recent Records"))
    return layout


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def live_dashboard():
    console = Console()
    console.print("\n  Starting freshness tracking…")
    set_freshness(True)
    console.print("  Waiting for data (writer wakes up every 1–10 min)…\n")

    try:
        with Live(console=console, refresh_per_second=1 / REFRESH_SEC, screen=True) as live:
            while True:
                items     = fetch_recent()
                latencies = fetch_all_seen()
                live.update(build_display(items, latencies))
                time.sleep(REFRESH_SEC)
    except KeyboardInterrupt:
        pass
    finally:
        console.print("\n  Stopping freshness tracking…")
        set_freshness(False)
        console.print("  Done.\n")


def print_stats():
    latencies = fetch_all_seen()
    if not latencies:
        print("No seen records in DynamoDB yet.")
        return
    print(f"\nFreshness stats ({len(latencies)} samples)")
    print(f"  p50 : {pct(latencies, 50)}")
    print(f"  p75 : {pct(latencies, 75)}")
    print(f"  p95 : {pct(latencies, 95)}")
    print(f"  p99 : {pct(latencies, 99)}")
    print(f"  min : {min(latencies) / 1000:.2f}s")
    print(f"  max : {max(latencies) / 1000:.2f}s\n")


# ---------------------------------------------------------------------------
# Probe mode — direct upsert → poll, no DynamoDB/Lambda in the path
# ---------------------------------------------------------------------------

def run_probe():
    console = Console()
    if not INDEX_HOST or not PINECONE_API_KEY:
        console.print("[red]INDEX_HOST and PINECONE_API_KEY must be set in .env for probe mode[/red]")
        sys.exit(1)

    pc    = Pinecone(api_key=PINECONE_API_KEY)
    index = pc.Index(host=INDEX_HOST)

    samples: list[int] = []

    console.print(f"\n  [bold]Probe mode[/bold] — upsert 1 vector every {PROBE_INTERVAL}s, poll every {int(PROBE_POLL*1000)}ms")
    console.print("  Ctrl-C to stop and print summary\n")

    try:
        n = 0
        while True:
            n += 1
            vid = str(uuid.uuid4())
            vec = normalize(np.random.randn(1, VECTOR_DIM).astype("float32"), norm="l2")[0].tolist()

            index.upsert(vectors=[{"id": vid, "values": vec}])
            t0 = time.time()  # clock starts after Pinecone ACKs the write

            deadline = t0 + 60
            seen = False
            while time.time() < deadline:
                try:
                    if vid in (index.fetch(ids=[vid]).vectors or {}):
                        latency_ms = int((time.time() - t0) * 1000)
                        samples.append(latency_ms)
                        seen = True
                        break
                except Exception:
                    pass
                time.sleep(PROBE_POLL)

            if seen:
                avg = statistics.mean(samples)
                p50 = statistics.median(samples)
                console.print(
                    f"  [{n:>4}] [green]{latency_ms:>6}ms[/green]   "
                    f"p50={p50/1000:.2f}s  avg={avg/1000:.2f}s  "
                    f"({'  '.join(f'{v/1000:.2f}s' for v in samples[-5:])})"
                )
            else:
                console.print(f"  [{n:>4}] [red]timeout (>60s)[/red]")

            time.sleep(PROBE_INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        if samples:
            console.print(f"\n  [bold]Probe summary[/bold] ({len(samples)} samples)")
            console.print(f"    p50 : {statistics.median(samples)/1000:.2f}s")
            if len(samples) >= 20:
                console.print(f"    p95 : {statistics.quantiles(samples, n=100)[94]/1000:.2f}s")
                console.print(f"    p99 : {statistics.quantiles(samples, n=100)[98]/1000:.2f}s")
            console.print(f"    min : {min(samples)/1000:.2f}s")
            console.print(f"    max : {max(samples)/1000:.2f}s\n")


# ---------------------------------------------------------------------------
# Lambda probe mode — invoke the tracker Lambda directly, stream its logs
# ---------------------------------------------------------------------------

def run_lambda_probe():
    console = Console()

    # need a longer read timeout than the default 60s — Lambda runs for 3 min
    lambda_client = session.client(
        "lambda",
        config=Config(read_timeout=210, connect_timeout=10),
    )

    console.print(f"\n  [bold]Lambda probe[/bold] — invoking [cyan]{LAMBDA_FUNCTION}[/cyan]")
    console.print("  3 min run · 1 vector every 2s · polling every 10ms\n")

    payload = json.dumps({
        "mode": "probe",
        "duration_seconds": 180,
        "upsert_interval_seconds": 2,
        "poll_interval_seconds": 0.01,
        "vector_timeout_seconds": 10,
    })

    try:
        resp = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION,
            InvocationType="RequestResponse",
            LogType="Tail",
            Payload=payload,
        )
    except Exception as e:
        console.print(f"[red]Invoke failed: {e}[/red]")
        return

    import base64
    raw = resp["Payload"].read()

    if resp.get("FunctionError"):
        console.print(f"[red]Lambda error:[/red] {raw.decode()}")
        if resp.get("LogResult"):
            console.print("\n[dim]Lambda logs:[/dim]")
            console.print(base64.b64decode(resp["LogResult"]).decode())
        return

    result = json.loads(raw)
    samples = result.get("samples", [])

    if not samples:
        console.print("[yellow]No samples returned.[/yellow]")
        return

    console.print(f"  [bold]Results[/bold] ({result['count']} samples, {result['timeouts']} timeouts)\n")

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="dim", width=6)
    tbl.add_column(style="cyan")

    tbl.add_row("p50",  f"{result['p50_ms'] / 1000:.3f}s")
    if "p95_ms" in result:
        tbl.add_row("p95",  f"{result['p95_ms'] / 1000:.3f}s")
        tbl.add_row("p99",  f"{result['p99_ms'] / 1000:.3f}s")
    tbl.add_row("min",  f"{result['min_ms'] / 1000:.3f}s")
    tbl.add_row("max",  f"{result['max_ms'] / 1000:.3f}s")

    console.print(tbl)

    # histogram — bucket into 1s bands
    max_ms  = result["max_ms"]
    buckets = max(1, (max_ms // 1000) + 1)
    counts  = [0] * int(buckets)
    for ms in samples:
        counts[min(int(ms // 1000), len(counts) - 1)] += 1

    console.print("\n  [dim]Distribution (1s buckets)[/dim]")
    bar_max = max(counts)
    for i, c in enumerate(counts):
        bar = "█" * int(c / bar_max * 40) if bar_max else ""
        console.print(f"  {i:>3}s  {bar} {c}")
    console.print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pinecone freshness monitor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--off",          action="store_true", help="Disable tracking and exit")
    group.add_argument("--stats",        action="store_true", help="Print summary stats and exit")
    group.add_argument("--probe",        action="store_true", help="Direct prober: upsert → poll locally")
    group.add_argument("--lambda-probe", action="store_true", help="Invoke tracker Lambda as prober, return stats")
    args = parser.parse_args()

    if args.off:
        set_freshness(False)
    elif args.stats:
        print_stats()
    elif args.probe:
        run_probe()
    elif args.lambda_probe:
        run_lambda_probe()
    else:
        live_dashboard()
