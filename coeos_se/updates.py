"""Settings auto-update — poll the canonical TMB Settings on GitHub, compare its
`updated` date to the running config, and OFFER the update (never apply
silently). Download comes straight from the public repo.

  COEOS_SETTINGS_URL     override the raw GitHub URL (default: main branch)
  COEOS_UPDATE_INTERVAL  seconds between checks (default 86400 = 24 h)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

from .config import config_txn, load_config

DEFAULT_URL = ("https://raw.githubusercontent.com/Odyssai-eu/coeos-SE/main/"
               "coeos_se/settings/TMB-Settings-SE.json")

STATE: dict = {"checked_at": None, "available": False, "local_updated": None,
               "remote_updated": None, "remote_name": None, "error": None}


def settings_url() -> str:
    return (os.environ.get("COEOS_SETTINGS_URL") or DEFAULT_URL).strip()


def interval_s() -> int:
    try:
        return max(300, int(os.environ.get("COEOS_UPDATE_INTERVAL", "86400")))
    except ValueError:
        return 86400


async def _fetch_remote() -> dict | None:
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        r = await client.get(settings_url())
        if r.status_code == 200:
            return r.json()
        return None


async def check() -> dict:
    """Compare the remote `updated` (ISO date) to the local one. An update is
    'available' when the remote date is strictly newer. Read-only."""
    local = (load_config().get("coeos") or {}).get("updated") or ""
    try:
        remote = await _fetch_remote()
        if remote is None:
            STATE.update(error="settings unreachable", checked_at=_now())
            return dict(STATE)
        r_upd = str(remote.get("updated") or "")
        STATE.update(checked_at=_now(), local_updated=local, remote_updated=r_upd,
                     remote_name=remote.get("name"),
                     available=bool(r_upd and r_upd > local), error=None)
    except Exception as e:
        STATE.update(error=str(e)[:200], checked_at=_now())
    return dict(STATE)


async def apply() -> dict:
    """Download the remote settings and replace the coeos config. Provider keys
    (under cfg['providers']) are untouched."""
    remote = await _fetch_remote()
    if remote is None:
        raise RuntimeError("remote settings unreachable")
    with config_txn() as cfg:
        cfg["coeos"] = remote
    STATE.update(available=False, local_updated=str(remote.get("updated") or ""))
    sys.stderr.write(f"[coeos-se] settings updated from GitHub → {remote.get('updated')}\n")
    return {"ok": True, "updated": remote.get("updated"), "name": remote.get("name")}


async def periodic_loop() -> None:
    """Background task: check now, then every interval. Failures are swallowed
    into STATE['error'] — the poll never crashes the app."""
    while True:
        try:
            await check()
        except Exception as e:
            STATE.update(error=str(e)[:200], checked_at=_now())
        await asyncio.sleep(interval_s())


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")
