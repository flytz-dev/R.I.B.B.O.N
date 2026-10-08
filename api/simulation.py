"""TEMPORARY multi-user simulation shown in the interface for the thesis presentation.

Simulated users sign in and run audits at the same time through the real HTTP API,
each with their own synthetic documents and session cookie. The run reports timing
and checks that no user can see another user's audits.

To remove: delete this module, its router registration in api/main.py, the
"Simulação multiusuário" tab and the view marked TEMPORÁRIO in static/index.html,
the code under the "Multi-user simulation" heading in static/app.js, and
tests/test_simulation.py.
A dedicated load-testing tool should replace it for real capacity testing."""

import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api import auth
from core import samples, storage

router = APIRouter(prefix="/api/v1/simulations", tags=["Temporary demo"])

SIMULATIONS: dict[str, dict] = {}
_RUNNING = threading.Lock()
ALLOWED_DPI = (200, 300, 500)
BARRIER_TIMEOUT_SECONDS = 120


class SimulationRequest(BaseModel):
    users: int = Field(3, ge=2, le=8)
    pages: int = Field(3, ge=1, le=6)
    dpi: int = 200


class SimulationStarted(BaseModel):
    simulation_id: str


def _http_client_factory(base_url):
    return lambda: httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, read=300.0))


def _server_url(request: Request) -> str:
    """Address this server by the socket that received the request.

    The Host header is often "localhost", which Windows resolves to IPv6 first; when
    the server listens on IPv4 only, every new connection then waits about 2 seconds."""
    server = request.scope.get("server")
    if not server:
        return str(request.base_url)
    host, port = server
    if ":" in host:
        host = f"[{host}]"
    return f"{request.url.scheme}://{host}:{port}/"


@router.post("", response_model=SimulationStarted)
def start_simulation(body: SimulationRequest, request: Request, username: str = Depends(auth.current_user)):
    """Start simulated users that sign in and audit synthetic documents concurrently."""
    if body.dpi not in ALLOWED_DPI:
        raise HTTPException(status_code=400, detail=f"dpi must be one of {ALLOWED_DPI}.")
    if not _RUNNING.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A simulation is already running.")

    state = new_state(username, body.users, body.pages, body.dpi)
    simulation_id = state["simulation_id"]
    SIMULATIONS[simulation_id] = state
    threading.Thread(
        target=_run_and_release, args=(state, _http_client_factory(_server_url(request))), daemon=True
    ).start()
    return SimulationStarted(simulation_id=simulation_id)


@router.get("/{simulation_id}")
def get_simulation(simulation_id: str, username: str = Depends(auth.current_user)):
    state = SIMULATIONS.get(simulation_id)
    if not state:
        raise HTTPException(status_code=404, detail="Simulation not found.")
    return state


def new_state(started_by, users, pages, dpi):
    simulation_id = uuid.uuid4().hex[:8]
    return {
        "simulation_id": simulation_id,
        "started_by": started_by,
        "status": "running",
        "dpi": dpi,
        "pages_per_user": pages,
        "started_at": time.time(),
        "wall_seconds": None,
        "error": None,
        "summary": None,
        "users": [
            {
                "username": f"{auth.SIMULATED_PREFIX}{simulation_id}-{slot + 1}",
                "status": "waiting", "job_id": None, "total": None, "processed": 0,
                "matched": 0, "mismatched": 0, "login_ms": None, "first_page_seconds": None,
                "seconds": None, "isolated": None, "isolation_detail": "", "error": None,
            }
            for slot in range(users)
        ],
    }


def run_simulation(state, client_factory):
    """Run all simulated users, summarize, and remove their accounts and audits."""
    passwords = {}
    temp_directory = tempfile.mkdtemp(prefix="ribbon_simulation_")
    started = time.perf_counter()
    try:
        for user in state["users"]:
            passwords[user["username"]] = secrets.token_urlsafe(16)
            auth.create_account(user["username"], passwords[user["username"]], simulated=True)

        barrier = threading.Barrier(len(state["users"]))
        threads = [
            threading.Thread(
                target=_simulate_user,
                args=(state, slot, client_factory, passwords[user["username"]], temp_directory, barrier),
                daemon=True,
            )
            for slot, user in enumerate(state["users"])
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        outcome = "completed"
    except Exception as e:
        outcome = "error"
        state["error"] = f"{type(e).__name__}: {e}"
    finally:
        state["wall_seconds"] = round(time.perf_counter() - started, 2)
        _summarize(state)
        try:
            storage.delete_users(passwords)
        except sqlite3.Error as e:
            print(f"[simulation] could not remove simulated users: {e}")
        shutil.rmtree(temp_directory, ignore_errors=True)
    # Publish the final status last: the interface stops polling when it changes.
    state["status"] = outcome


def _run_and_release(state, client_factory):
    try:
        run_simulation(state, client_factory)
    finally:
        _RUNNING.release()


def _summarize(state):
    users = state["users"]
    pages = sum(user["processed"] for user in users)
    state["summary"] = {
        "users": len(users),
        "pages": pages,
        "pages_per_minute": round(pages / state["wall_seconds"] * 60, 1) if state["wall_seconds"] else None,
        "failed_users": sum(user["error"] is not None for user in users),
        "isolation_passed": all(user["isolated"] for user in users),
    }


def _simulate_user(state, slot, client_factory, password, temp_directory, barrier):
    user = state["users"][slot]
    try:
        directory = os.path.join(temp_directory, user["username"])
        dataset = samples.generate_dataset(
            directory, slips=state["pages_per_user"], seed=secrets.randbelow(1 << 16), remove_slip=False
        )

        with client_factory() as client:
            user["status"] = "signing in"
            started = time.perf_counter()
            response = client.post("/api/v1/auth/login", json={"username": user["username"], "password": password})
            response.raise_for_status()
            user["login_ms"] = round((time.perf_counter() - started) * 1000)

            user["status"] = "uploading"
            with open(dataset["payment_slips"], "rb") as slips, open(dataset["reference"], "rb") as reference:
                response = client.post(
                    f"/api/v1/audits?dpi={state['dpi']}",
                    files={
                        "payment_slips": ("payment_slips.pdf", slips, "application/pdf"),
                        "reference": ("reference.pdf", reference, "application/pdf"),
                    },
                )
            response.raise_for_status()
            user["job_id"] = response.json()["job_id"]

            # Wait until every user has an audit, then try to open a neighbor's audit.
            try:
                barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
                _check_isolation(state, slot, client)
            except threading.BrokenBarrierError:
                user["isolation_detail"] = "Skipped: another simulated user failed to start."

            user["status"] = "processing"
            _follow_events(user, client, started)

            # Each user's history must list their own audit and nobody else's.
            history = {item["job_id"] for item in client.get("/api/v1/audits").json()}
            others = {other["job_id"] for other in state["users"] if other is not user and other["job_id"]}
            if user["job_id"] not in history or history & others:
                user["isolated"] = False
                user["isolation_detail"] = "History shows another user's audit or misses its own."
            elif user["isolated"] is None:
                user["isolated"] = True

            client.post("/api/v1/auth/logout")
        user["status"] = "completed" if user["error"] is None else "error"
    except Exception as e:
        barrier.abort()
        user["status"] = "error"
        user["error"] = f"{type(e).__name__}: {e}"
        user["isolated"] = user["isolated"] or False


def _check_isolation(state, slot, client):
    user = state["users"][slot]
    neighbor = state["users"][(slot + 1) % len(state["users"])]
    if not neighbor["job_id"]:
        return
    blocked = all(
        client.get(path).status_code == 404
        for path in (f"/api/v1/audits/{neighbor['job_id']}", f"/api/v1/audits/{neighbor['job_id']}/pages")
    )
    user["isolated"] = blocked
    user["isolation_detail"] = (
        f"Could not open {neighbor['username']}'s audit (404), as expected." if blocked
        else f"Opened {neighbor['username']}'s audit: isolation failure."
    )


def _follow_events(user, client, started):
    with client.stream("GET", f"/api/v1/audits/{user['job_id']}/events") as stream:
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "start":
                user["total"] = event["total_pages"]
            elif event["type"] == "page":
                if user["first_page_seconds"] is None:
                    user["first_page_seconds"] = round(time.perf_counter() - started, 2)
                user["processed"] += 1
                status = event["row"]["Overall Status"]
                user["matched"] += status == "OK"
                user["mismatched"] += status == "ERROR"
            elif event["type"] == "error":
                user["error"] = event["message"]
            elif event["type"] == "end":
                break
    user["seconds"] = round(time.perf_counter() - started, 2)
