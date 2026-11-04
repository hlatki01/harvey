"""Harvey Dashboard — local web UI to set up, control, and monitor Harvey."""

import asyncio
import ipaddress
import json
import logging
import os
import re
from urllib.parse import urlparse
import signal
import stat
import subprocess
import time
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, PlainTextResponse, Response,
)

logger = logging.getLogger("harvey.dashboard")

from harvey import dashboard_auth as auth  # noqa: E402
from harvey.paths import PROJECT_ROOT  # noqa: E402
from harvey.textio import read_user_text  # noqa: E402
DB_PATH = PROJECT_ROOT / "data" / "harvey.db"
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "harvey.yaml"
PID_FILE = PROJECT_ROOT / "data" / "harvey.pid"
# In Docker the loop is its own container; this process must not spawn another.
IN_CONTAINER = bool(os.environ.get("HARVEY_CONTAINER"))
ALIVE_STALE_SECONDS = 90  # four missed beacons
CONTAINER_NOTE = "Roadie Crew runs as its own container: use 'docker compose restart harvey'."
LOG_FILE = PROJECT_ROOT / "data" / "harvey.log"
TOKEN_FILE = PROJECT_ROOT / "data" / "dashboard-token"


def _resolve_config_file() -> Path:
    """harvey.local.yaml wins when present — same resolution as config.py's
    loader, so the dashboard doesn't judge setup complete against the
    tracked harvey.yaml template while the real trained config sits in the
    gitignored local override."""
    from harvey.config import _find_config_file, ConfigFileNotFoundError
    try:
        return Path(_find_config_file())
    except ConfigFileNotFoundError:
        return CONFIG_FILE

# No /docs, /redoc or /openapi.json: the API is for this UI only, and every
# route except the static shell needs the token (see dashboard_auth).
app = FastAPI(title="Roadie Crew Dashboard", docs_url=None, redoc_url=None, openapi_url=None)
token_store = auth.TokenStore(TOKEN_FILE)
_public_cache: tuple[float, str] = (0.0, "")


def _public_origin() -> str:
    """dashboard.public_url from the config, re-read at most every 30 s."""
    global _public_cache
    import time
    now = time.monotonic()
    if now - _public_cache[0] > 30:
        try:
            from harvey.config import load_config
            origin = load_config().dashboard.public_url
        except Exception:
            origin = ""
        _public_cache = (now, origin)
    return _public_cache[1]


async def _session_user(cookie: str):
    from harvey.users import hash_token
    return await _state().get_session_user(hash_token(cookie))


app.add_middleware(auth.DashboardGuard, store=token_store,
                   public_origin=_public_origin, resolve_session=_session_user)

# Harvey process tracking
_harvey_process: subprocess.Popen | None = None
_harvey_started_at: datetime | None = None
_env_lock = asyncio.Lock()


# ── Helpers ──


async def query_db(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as list of dicts.

    Never raises: a missing DB file, missing table, or malformed schema
    returns [] so no dashboard route can 500 on an empty install.
    """
    if not DB_PATH.exists():
        return []
    try:
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("query_db failed (%s): %s", sql.split(None, 4)[:4], e)
        return []


def _mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 and last 4 chars."""
    if not key or len(key) < 10:
        return "****" if key else ""
    return key[:4] + "****" + key[-4:]


def _read_env_file() -> dict[str, str]:
    """Read .env file and return as dict."""
    env_vars = {}
    if ENV_FILE.exists():
        for line in read_user_text(ENV_FILE).splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip()
    return env_vars


def _env_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    key = stripped.partition("=")[0].strip()
    if key.startswith("export "):
        key = key[len("export "):].strip()
    return key


def _write_env_file(updates: dict[str, str]):
    """Set keys in .env, keeping every other line (comments, order, other
    keys) as it was.

    Written to a private temporary file in the same directory and swapped
    in atomically, so a crash never leaves a half-written .env and the
    credentials are never readable by other users, even for a moment: the
    result is owner-only (0600 on POSIX) whether .env existed or not. A
    symlinked .env is refused rather than replaced or followed.
    """
    if ENV_FILE.is_symlink():
        raise OSError(f"{ENV_FILE} is a symbolic link; edit it by hand.")
    if ENV_FILE.exists() and not ENV_FILE.is_file():
        raise OSError(f"{ENV_FILE} is not a regular file.")
    lines = (read_user_text(ENV_FILE).splitlines()
             if ENV_FILE.exists() else [])

    out, written = [], set()
    for line in lines:
        key = _env_key(line)
        if key in updates:
            if key in written:
                continue  # a later duplicate would override the new value
            out.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in written]

    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(ENV_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            if os.name != "nt":
                os.fchmod(f.fileno(), stat.S_IRUSR | stat.S_IWUSR)
            f.write("\n".join(out) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ENV_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    load_dotenv(str(ENV_FILE), override=True)


def _check_harvey_pid() -> int | None:
    """Check if there's a running Harvey process from a PID file."""
    global _harvey_process, _harvey_started_at
    if _harvey_process and _harvey_process.poll() is None:
        return _harvey_process.pid
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, 0)  # Check if process exists
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    return None


# ── Setup Status ──


@app.get("/api/setup-status")
async def get_setup_status():
    """Check what's configured and what still needs setup."""
    checks = []

    # 1. Venv
    checks.append({
        "id": "venv", "label": "Python virtual environment",
        "done": IN_CONTAINER or (PROJECT_ROOT / ".venv").is_dir(),  # the image is the environment
        "required": True,
        "help": "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e .",
    })

    # 2. Env file
    env_vars = _read_env_file()
    # in a container the compose env_file injects the keys; there is no .env on disk
    env_exists = IN_CONTAINER or (ENV_FILE.exists() and bool(env_vars))
    checks.append({
        "id": "env_file", "label": "Environment file (.env)",
        "done": env_exists,
        "required": True,
        "help": "Go to the Settings tab to enter your API keys.",
    })

    # 3. Email provider configured (matches channels.email.provider)
    provider = _current_provider()

    def _has(*keys):
        return all((env_vars.get(k, "") or os.getenv(k, "")).strip() for k in keys)

    if provider == "gmail":
        provider_done = _has("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET") and \
            (PROJECT_ROOT / "data" / "gmail_token.json").is_file()
        provider_help = ("Set GMAIL_CLIENT_ID/SECRET in Settings, then run "
                         "'harvey gmail auth' in your terminal.")
    elif provider == "microsoft":
        provider_done = _has("M365_CLIENT_ID") and \
            (PROJECT_ROOT / "data" / "m365_token.json").is_file()
        provider_help = ("Set the Microsoft 365 client and tenant IDs in Settings, then run "
                         "'harvey microsoft auth' in your terminal.")
    elif provider == "smtp":
        provider_done = _has("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD")
        provider_help = "Enter your SMTP host, username, and password in Settings."
    else:
        instantly_key = env_vars.get("INSTANTLY_API_KEY", "") or os.getenv("INSTANTLY_API_KEY", "")
        provider_done = bool(instantly_key) and instantly_key != "your_instantly_api_key_here"
        provider_help = "Enter your Instantly API key in Settings."
    checks.append({
        "id": "email_provider", "label": f"Email provider configured ({provider})",
        "done": provider_done,
        "required": True,
        "help": provider_help,
    })

    # 4. Email verification (needed for addresses to be sendable, not 'guess')
    verifier_set = _has("REOON_API_KEY") or _has("ZEROBOUNCE_API_KEY") or _has("HUNTER_API_KEY")
    try:
        from harvey.config import load_config
        whatsapp_on = load_config().channels.whatsapp.enabled
    except Exception:
        whatsapp_on = False
    checks.append({
        "id": "verifier", "label": "Email verification key",
        "done": verifier_set,
        # Only cold email needs verified addresses; with WhatsApp on, email is optional.
        "required": not whatsapp_on,
        "help": "Add a Reoon (free 600/mo), ZeroBounce, or Hunter key in Settings — "
                "without one, found emails stay 'guess' and are never sent.",
    })

    # 5. Config valid
    config_valid = False
    config_path = _resolve_config_file()
    if config_path.exists():
        try:
            cfg = yaml.safe_load(read_user_text(config_path))
            company = cfg.get("persona", {}).get("company", "")
            product = cfg.get("product", {}).get("name", "")
            config_valid = company not in ("Your Company", "") and product not in ("Your Product", "")
        except Exception:
            pass
    checks.append({
        "id": "config", "label": f"Roadie Crew configured ({config_path.name})",
        "done": config_valid,
        "required": True,
        "help": "Train Roadie Crew on your product. Use the trainer or set up manually through Claude.",
    })

    # 6. Product trained
    product_trained = (PROJECT_ROOT / "skills" / "product_knowledge.md").exists()
    checks.append({
        "id": "product_trained", "label": "Product knowledge trained",
        "done": product_trained,
        "required": True,
        "help": "Run: harvey train https://yourwebsite.com (or set up through Claude).",
    })

    # 7. LinkedIn (optional)
    linkedin_email = env_vars.get("LINKEDIN_EMAIL", "") or os.getenv("LINKEDIN_EMAIL", "")
    linkedin_pass = env_vars.get("LINKEDIN_PASSWORD", "") or os.getenv("LINKEDIN_PASSWORD", "")
    checks.append({
        "id": "linkedin", "label": "LinkedIn credentials",
        "done": bool(linkedin_email) and bool(linkedin_pass),
        "required": False,
        "help": "Optional. Enter your LinkedIn credentials in Settings to enable LinkedIn prospecting.",
    })

    # 8. Cloudflare (optional)
    cf_id = env_vars.get("CLOUDFLARE_ACCOUNT_ID", "") or os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
    cf_token = env_vars.get("CLOUDFLARE_API_TOKEN", "") or os.getenv("CLOUDFLARE_API_TOKEN", "")
    checks.append({
        "id": "cloudflare", "label": "Cloudflare deep crawling",
        "done": bool(cf_id) and bool(cf_token),
        "required": False,
        "help": "Optional. For JavaScript-rendered website crawling during training.",
    })

    required_checks = [c for c in checks if c["required"]]
    completed_required = sum(1 for c in required_checks if c["done"])

    return {
        "checks": checks,
        "completed": completed_required,
        "total_required": len(required_checks),
        "percent": int(completed_required / len(required_checks) * 100) if required_checks else 0,
    }


# ── Settings ──


def _current_provider() -> str:
    """Read channels.email.provider from the active config (best-effort)."""
    try:
        cfg = yaml.safe_load(read_user_text(_resolve_config_file())) or {}
        return ((cfg.get("channels") or {}).get("email") or {}).get("provider", "instantly")
    except Exception:
        return "instantly"


@app.get("/api/settings")
async def get_settings():
    """Get current settings — presence flags only for secrets, never raw values."""
    env_vars = _read_env_file()
    all_keys = [
        "INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
        "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
        "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
        "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
        "M365_CLIENT_ID", "M365_TENANT_ID",
        "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY",
        "EVOLUTION_API_URL", "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
        "INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD", "INSTAGRAM_SESSIONID", "SERPER_API_KEY",
    ]
    for key in all_keys:
        if key not in env_vars:
            env_vars[key] = os.getenv(key, "")
    # The Instagram login lives in the database (integrations/instagram.py).
    from harvey.integrations.instagram import saved_login
    saved = saved_login(DB_PATH)
    if saved:
        for key in INSTAGRAM_LOGIN_FIELDS:
            env_vars[key] = saved.get(key.lower(), "")

    def is_set(k):
        return bool((env_vars.get(k) or "").strip())

    # Gmail is authorized once harvey gmail auth has stored a token file.
    gmail_token = (PROJECT_ROOT / "data" / "gmail_token.json").is_file()

    return {
        "provider": _current_provider(),
        # Non-secret values echo back so fields repopulate; secrets are
        # presence-only so keys never leave the box.
        "instantly_api_key_set": is_set("INSTANTLY_API_KEY"),
        "linkedin_email": env_vars.get("LINKEDIN_EMAIL", ""),
        "linkedin_password_set": is_set("LINKEDIN_PASSWORD"),
        "cloudflare_account_id": env_vars.get("CLOUDFLARE_ACCOUNT_ID", ""),
        "cloudflare_api_token_set": is_set("CLOUDFLARE_API_TOKEN"),
        "gmail_client_id": env_vars.get("GMAIL_CLIENT_ID", ""),
        "gmail_client_secret_set": is_set("GMAIL_CLIENT_SECRET"),
        "gmail_authorized": gmail_token,
        "smtp_host": env_vars.get("SMTP_HOST", ""),
        "smtp_port": env_vars.get("SMTP_PORT", ""),
        "smtp_username": env_vars.get("SMTP_USERNAME", ""),
        "smtp_password_set": is_set("SMTP_PASSWORD"),
        "imap_host": env_vars.get("IMAP_HOST", ""),
        "imap_port": env_vars.get("IMAP_PORT", ""),
        "imap_username": env_vars.get("IMAP_USERNAME", ""),
        "imap_password_set": is_set("IMAP_PASSWORD"),
        # Microsoft 365: ids aren't secret (no client secret: Harvey signs
        # in as a public client with PKCE); the token file means signed in.
        "m365_client_id": env_vars.get("M365_CLIENT_ID", ""),
        "m365_tenant_id": env_vars.get("M365_TENANT_ID", ""),
        "m365_authorized": (PROJECT_ROOT / "data" / "m365_token.json").is_file(),
        "reoon_api_key_set": is_set("REOON_API_KEY"),
        "zerobounce_api_key_set": is_set("ZEROBOUNCE_API_KEY"),
        "hunter_api_key_set": is_set("HUNTER_API_KEY"),
        "evolution_api_url": env_vars.get("EVOLUTION_API_URL", ""),
        "evolution_instance": env_vars.get("EVOLUTION_INSTANCE", ""),
        "evolution_api_key_set": is_set("EVOLUTION_API_KEY"),
        "instagram_username": env_vars.get("INSTAGRAM_USERNAME", ""),
        "instagram_password_set": is_set("INSTAGRAM_PASSWORD"),
        "instagram_sessionid_set": is_set("INSTAGRAM_SESSIONID"),
        "serper_api_key_set": is_set("SERPER_API_KEY"),
    }


INSTAGRAM_LOGIN_FIELDS = ("INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD", "INSTAGRAM_SESSIONID")


async def _save_instagram_login(updates: dict):
    """Into the settings table, not .env (prod's .env is read-only). A changed
    login drops the saved session, which belongs to the old one."""
    from harvey.integrations.instagram import saved_login
    state = _state()
    await state.init_db()
    before = saved_login(DB_PATH)
    if not before:   # first save from the dashboard: start from what the env had
        env = _read_env_file()
        before = {k.lower(): v for k in INSTAGRAM_LOGIN_FIELDS if (v := env.get(k) or os.getenv(k, ""))}
    after = dict(before)
    for key, value in updates.items():
        if value:
            after[key.lower()] = value
        else:
            after.pop(key.lower(), None)
    for key in INSTAGRAM_LOGIN_FIELDS:
        await state.set_setting(key.lower(), after.get(key.lower(), ""))
    if after != before:
        (Path(DB_PATH).parent / "instagram_session.json").unlink(missing_ok=True)


@app.post("/api/settings/env")
async def save_env_settings(request: Request):
    """Save environment variables to .env file (the Instagram login to the database)."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"success": False, "message": "Invalid request body."}, status_code=400)
    async with _env_lock:
        updates = {}
        for key in ["INSTANTLY_API_KEY", "LINKEDIN_EMAIL", "LINKEDIN_PASSWORD",
                     "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
                     "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                     "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
                     "IMAP_HOST", "IMAP_PORT", "IMAP_USERNAME", "IMAP_PASSWORD",
                     "M365_CLIENT_ID", "M365_TENANT_ID",
                     "REOON_API_KEY", "ZEROBOUNCE_API_KEY", "HUNTER_API_KEY",
                     "EVOLUTION_API_URL", "EVOLUTION_API_KEY", "EVOLUTION_INSTANCE",
                     "INSTAGRAM_USERNAME", "INSTAGRAM_PASSWORD", "INSTAGRAM_SESSIONID",
                     "SERPER_API_KEY"]:
            if key in data and data[key] is not None:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                    return JSONResponse({"success": False, "message": f"{key} must be text."},
                                        status_code=400)
                value = str(value)
                # A port Harvey can't parse would stop it starting at all.
                if key.endswith("_PORT") and value.strip() and not value.strip().isdigit():
                    return JSONResponse({"success": False, "message": f"{key} must be a number."},
                                        status_code=400)
                if len(value) > 4096 or "\x00" in value:
                    return JSONResponse({"success": False, "message": f"{key} is invalid."},
                                        status_code=400)
                # Strip newlines so a crafted value can't inject extra .env entries
                updates[key] = value.replace("\n", " ").replace("\r", " ").strip()
        instagram = {k: updates.pop(k) for k in INSTAGRAM_LOGIN_FIELDS if k in updates}
        if instagram:
            await _save_instagram_login(instagram)
        if updates:
            try:
                _write_env_file(updates)
            except Exception as e:
                logger.warning("Failed to write .env: %s", e)
                return {"success": False, "message": "Could not write .env file."}
    return {"success": True}


# ── Geography: which cities discovery searches ──

_COORD_RE = re.compile(r"-?\d{1,3}(\.\d+)?,-?\d{1,3}(\.\d+)?(,\d{1,4})?")


def _replace_config_key(lines: list[str], path: list[str], block: list[str]) -> list[str]:
    """Swap the key at `path` (["icp", "geography"], or deeper) and its indented
    body for `block`, leaving every other line, comments included, as it was.
    Appended to its parent when the key isn't there yet. Two-space indents."""
    lo, hi = 0, len(lines)
    for depth, name in enumerate(path):
        pad = " " * (2 * depth)
        at = next((i for i in range(lo, hi)
                   if re.match(rf"{pad}{re.escape(name)}:(\s|$)", lines[i])), None)
        last = depth == len(path) - 1
        if at is None:
            if not last:
                raise ValueError(f"no {'.'.join(path[:depth + 1])}: section in the config file")
            while hi > lo and not lines[hi - 1].strip():
                hi -= 1
            return lines[:hi] + block + lines[hi:]
        # The body is everything indented deeper than the key (blank lines inside it too).
        stop = at + 1
        while stop < hi and (not lines[stop].strip() or lines[stop].startswith(pad + " ")):
            stop += 1
        while stop > at + 1 and not lines[stop - 1].strip():
            stop -= 1
        if last:
            return lines[:at] + block + lines[stop:]
        lo, hi = at + 1, stop
    return lines


@app.get("/api/settings/geography")
async def get_geography():
    """The cities in the config, the ones Harvey added itself, and what each
    city's searches yielded last time (found / new)."""
    from harvey.config import load_config
    config = load_config(str(_resolve_config_file()))
    icp = config.icp
    coords = icp.geo_coordinates or {}
    state = _state()
    await state.init_db()
    per_city: dict[str, dict] = {}
    for (_, loc), y in (await state.query_yields()).items():
        c = per_city.setdefault(loc, {"searched": 0, "found": 0, "new": 0, "last_run_at": ""})
        c["searched"] += 1
        c["found"] += y["last_found"]
        c["new"] += y["last_new"]
        c["last_run_at"] = max(c["last_run_at"], y["last_run_at"])
    terms = len(icp.industries) or 1
    for c in per_city.values():
        c["searches"] = terms
    configured = {c.lower() for c in icp.geography}
    added = [{"name": c["name"], "coordinate": c["coordinate"], "reason": c["reason"], "added_at": c["added_at"]}
             for c in await state.discovery_cities() if c["name"].lower() not in configured]
    return {"cities": [{"name": c, "coordinate": coords.get(c, "")} for c in icp.geography],
            "added": added, "yields": per_city,
            "expand": bool(config.discovery.expand_cities),
            "per_week": config.discovery.city_additions_per_week}


@app.delete("/api/settings/geography/added/{name}")
async def remove_added_city(name: str):
    """Take out a city Harvey added: it is never searched or proposed again."""
    if not await _state().remove_discovery_city(name):
        return _refuse("Roadie Crew didn't add that city, or it was already taken out.", 404)
    return {"success": True}


@app.post("/api/settings/geography")
async def save_geography(request: Request):
    """Rewrite icp.geography and icp.geo_coordinates. The result is loaded
    as a full config before it replaces the file, so a bad save can't stop
    Harvey from starting."""
    data = await _json_body(request)
    raw = data.get("cities")
    if not isinstance(raw, list) or len(raw) > 50:
        return _refuse("Send a list of up to 50 cities.")
    names, coords = [], {}
    for item in raw:
        if not isinstance(item, dict):
            return _refuse("Each city needs a name.")
        name = " ".join(str(item.get("name") or "").split())
        coord = re.sub(r"\s+", "", str(item.get("coordinate") or ""))
        if not name or len(name) > 100:
            return _refuse("Each city needs a name (up to 100 characters).")
        if name.lower() in (n.lower() for n in names):
            return _refuse(f"{name} is listed twice.")
        if coord and not _COORD_RE.fullmatch(coord):
            return _refuse(f"{name}: coordinates are lat,lng or lat,lng,radius_km.")
        names.append(name)
        if coord:
            coords[name] = coord
    if not names:
        return _refuse("Keep at least one city.")

    path = _resolve_config_file()
    q = json.dumps  # a JSON string is a valid double-quoted YAML scalar
    lines = read_user_text(path).split("\n")
    try:
        lines = _replace_config_key(lines, ["icp", "geography"], ["  geography:"] + [f"    - {q(n, ensure_ascii=False)}" for n in names])
        lines = _replace_config_key(lines, ["icp", "geo_coordinates"],
                                    ["  geo_coordinates:"] + [f"    {q(n, ensure_ascii=False)}: {q(c)}" for n, c in coords.items()]
                                    if coords else ["  geo_coordinates: {}"])
    except ValueError as e:
        return _refuse(str(e))
    return _write_config_lines(path, lines) or {
        "success": True, "cities": len(names), "harvey_running": bool(_check_harvey_pid())}


def _write_config_lines(path, lines: list[str]):
    """Write the config, loaded as a full config first so a bad save can't
    stop Harvey from starting. A refusal response, or None once written."""
    from pydantic import ValidationError
    from harvey.config import ConfigError, load_config
    text = "\n".join(lines)
    fd, tmp = tempfile.mkstemp(prefix="harvey.", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        load_config(tmp)
    except (ConfigError, ValidationError) as e:
        return _refuse(f"Not saved: {e}")
    finally:
        os.unlink(tmp)
    # Written in place, not swapped in: in Docker the file is a single-file bind
    # mount (a rename can't replace it) on a read-only root (no temp file beside it).
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as e:
        logger.warning("Failed to write %s: %s", path, e)
        return _refuse(f"Could not write {path.name}: is it mounted read-only?")
    return None


# ── Business hours: when automatic email/WhatsApp may go out ──

@app.get("/api/settings/business-hours")
async def get_business_hours():
    from harvey.config import WEEKDAYS, load_config
    usage = load_config(str(_resolve_config_file())).usage
    return {"days": {d: usage.business_hours.get(d, "") for d in WEEKDAYS},
            "timezone": usage.quiet_hours.timezone, "holidays_country": usage.holidays_country}


@app.post("/api/settings/business-hours")
async def save_business_hours(request: Request):
    """Rewrite usage.business_hours from {day: "HH:MM-HH:MM", or "" when closed},
    and usage.holidays_country (a country code, or "" for no holidays)."""
    from harvey.config import WEEKDAYS
    data = await _json_body(request)
    days = data.get("days")
    country = data.get("holidays_country") or ""
    if not isinstance(country, str) or len(country.strip()) > 3:
        return _refuse("The holidays country is a two-letter code, e.g. BR.")
    if not isinstance(days, dict) or set(days) - set(WEEKDAYS):
        return _refuse("Send the hours for mon..sun.")
    open_days = {d: str(days[d]).strip() for d in WEEKDAYS if str(days.get(d) or "").strip()}
    if not open_days:
        return _refuse("Open at least one day.")
    path = _resolve_config_file()
    lines = read_user_text(path).split("\n")
    try:
        lines = _replace_config_key(lines, ["usage", "business_hours"], ["  business_hours:"] + [
            f"    {d}: {json.dumps(span)}" for d, span in open_days.items()])
        lines = _replace_config_key(lines, ["usage", "holidays_country"],
                                    [f"  holidays_country: {json.dumps(country.strip().upper())}"])
    except ValueError as e:
        return _refuse(str(e))
    return _write_config_lines(path, lines) or {
        "success": True, "harvey_running": bool(_check_harvey_pid())}


# ── Categories: the example site shown to each kind of business ──

@app.get("/api/settings/demos")
async def get_demos():
    """The categories in match order, how many companies each one catches
    (the first whose keyword is in a company's industry), and the industries
    no category catches yet."""
    from harvey.config import load_config
    from harvey.decide import find_demo
    demos = load_config(str(_resolve_config_file())).channels.whatsapp.demos
    caught, missed = [0] * len(demos), []
    for row in await query_db("SELECT industry, COUNT(*) AS n FROM companies GROUP BY industry ORDER BY n DESC"):
        demo = find_demo(row["industry"], demos)
        if demo:
            caught[demos.index(demo)] += row["n"]
        elif row["industry"]:
            missed.append({"industry": row["industry"], "n": row["n"]})
    return {"demos": [{**d.model_dump(), "companies": n} for d, n in zip(demos, caught)],
            "unmatched": missed[:15], "unmatched_total": sum(m["n"] for m in missed)}


@app.post("/api/settings/demos")
async def save_demos(request: Request):
    """Rewrite channels.whatsapp.demos, in the order given: the first match wins."""
    raw = (await _json_body(request)).get("demos")
    if not isinstance(raw, list) or len(raw) > 100:
        return _refuse("Send a list of up to 100 categories.")
    demos = []
    for d in raw:
        if not isinstance(d, dict):
            return _refuse("Each category needs a name, a site and keywords.")
        label = " ".join(str(d.get("label") or "").split())[:60]
        url = str(d.get("url") or "").strip()
        words = d.get("keywords")
        if not label:
            return _refuse("Each category needs a name.")
        if (urlparse(url).scheme != "https" or not urlparse(url).hostname or len(url) > 300
                or any(c.isspace() for c in url)):
            return _refuse(f"{label}: the demo site must be an https:// address.")
        if not isinstance(words, list) or not words or len(words) > 60:
            return _refuse(f"{label}: give between 1 and 60 keywords.")
        # Matched as lowercase fragments of the industry; a leading space ("  law") is kept on purpose.
        words = [str(w).lower().rstrip() for w in words if isinstance(w, str) and str(w).strip()][:60]
        if not words or any(len(w) > 40 for w in words):
            return _refuse(f"{label}: keywords are short words, up to 40 characters.")
        if label.lower() in (x["label"].lower() for x in demos):
            return _refuse(f"{label} is listed twice.")
        demos.append({"label": label, "url": url, "keywords": words})
    path = _resolve_config_file()
    lines = read_user_text(path).split("\n")
    q = lambda v: json.dumps(v, ensure_ascii=False)   # a JSON value is valid YAML flow style
    try:
        lines = _replace_config_key(lines, ["channels", "whatsapp", "demos"], [
            "    demos:                     # first match wins: specific before generic"] + [
            f"      - {{label: {q(d['label'])}, url: {q(d['url'])}, keywords: {q(d['keywords'])}}}"
            for d in demos] if demos else ["    demos: []"])
    except ValueError as e:
        return _refuse(str(e))
    return _write_config_lines(path, lines) or {
        "success": True, "harvey_running": bool(_check_harvey_pid())}


@app.post("/api/settings/test-instantly")
async def test_instantly(request: Request):
    """Test an Instantly API key."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    api_key = str(data.get("api_key", "") or "")
    if not api_key:
        return {"success": False, "message": "No API key provided."}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.instantly.ai/api/v2/accounts",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Connected to Instantly."}
            else:
                return {"success": False, "message": f"API returned {resp.status_code}. Check your key."}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


@app.post("/api/settings/test-whatsapp")
async def test_whatsapp():
    """Is the saved Evolution instance reachable and connected?"""
    from harvey.config import load_env
    from harvey.integrations.evolution import EvolutionClient
    # Saving through the dashboard reloads .env into the process, so this sees it.
    ok, message = await EvolutionClient.from_env(load_env()).test_connection()
    return {"success": ok, "message": message}


@app.post("/api/settings/test-instagram")
async def test_instagram():
    """Can the saved login reach the account? A success also resumes a channel
    Instagram paused (harvey/agents/instagram.py): the human checked it."""
    from harvey.agents.instagram import PAUSE_KEY
    from harvey.config import load_env
    from harvey.integrations.instagram import InstagramClient
    ok, message = await InstagramClient.from_env(load_env(), db_file=DB_PATH).test_connection()
    state = _state()
    await state.init_db()
    paused = await state.get_setting(PAUSE_KEY)
    if ok and paused:
        await state.delete_setting(PAUSE_KEY)
        message += " (Instagram resumed)"
    elif paused:
        message += f" (paused: {paused})"
    return {"success": ok, "message": message}


# ── Companies ──


COMPANIES_PAGE = 25
# Sortable columns, by the name the table uses; anything else sorts by newest.
_COMPANY_SORT = {"name": "c.name COLLATE NOCASE", "industry": "c.industry COLLATE NOCASE",
                 "location": "c.location COLLATE NOCASE", "contacts": "contact_count",
                 "source": "c.source", "created_at": "c.created_at"}


@app.get("/api/companies")
async def get_companies(q: str = "", page: int = 1, sort: str = "created_at", dir: str = "desc"):
    """One page of companies with contact counts, searchable by name, site,
    industry, place or phone, sorted by any column the table shows."""
    where, params = "", ()
    q = q.strip()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "WHERE " + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in (
            "c.name", "c.domain", "c.industry", "c.location", "c.phone"))
        params = (like,) * 5
    counted = await query_db(f"SELECT COUNT(*) AS n FROM companies c {where}", params)
    total = counted[0]["n"] if counted else 0   # no database yet
    order = _COMPANY_SORT.get(sort, "c.created_at") + (" ASC" if dir == "asc" else " DESC")
    page = max(1, page)
    rows = await query_db(f"""
        SELECT c.*,
            (SELECT COUNT(*) FROM prospects p WHERE p.company_id = c.id) as contact_count
        FROM companies c {where} ORDER BY {order}, c.id LIMIT ? OFFSET ?
    """, params + (COMPANIES_PAGE, (page - 1) * COMPANIES_PAGE))
    return {"rows": rows, "total": total, "page": page, "per_page": COMPANIES_PAGE}


_CONTACT_TEXT = ("first_name", "last_name", "title", "email", "phone", "linkedin_url")


async def _contact_fields(state, data: dict, prospect_id: str = ""):
    """The contact fields in a request, checked and ready to store, plus
    what an address or number now grades as; (fields, "") or (None, why)."""
    from harvey.config import load_config, load_env
    from harvey.integrations.email_finder import verify_published_address
    from harvey.phone import normalize_phone
    from harvey.users import valid_email
    fields = {}
    for key in _CONTACT_TEXT:
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > 300:
                return None, f"{key} must be text (up to 300 characters)."
            fields[key] = " ".join(value.split())
    if "email" in fields:
        email = fields["email"].lower()
        if email and not valid_email(email):
            return None, "That email address doesn't look right."
        other = await state.get_prospect_by_email(email) if email else None
        if other and other.id != prospect_id:
            return None, f"{email} is already a contact{' at ' + other.company if other.company else ''}."
        # Graded like any address Harvey finds: only a deliverable one gets cold email.
        status = await verify_published_address(email, load_env()) if email else ""
        fields.update(email=email, email_status=status, email_verified=status == "verified")
    if "phone" in fields:
        phone = normalize_phone(fields["phone"], load_config().channels.whatsapp.default_country_code) \
            if fields["phone"] else ""
        if fields["phone"] and not phone:
            return None, "That phone number doesn't look right. Include the area code."
        other = await state.get_prospect_by_phone(phone) if phone else None
        if other and other.id != prospect_id:
            return None, f"{phone} is already a contact{' at ' + other.company if other.company else ''}."
        fields.update(phone=phone, phone_verified=False)
    if fields.get("linkedin_url") and urlparse(fields["linkedin_url"]).scheme not in ("http", "https"):
        return None, "The LinkedIn address must start with https://."
    return fields, ""


@app.post("/api/companies/{company_id}/contacts")
async def add_contact(company_id: str, request: Request):
    """Add a person at a company by hand. They go through the same checks as
    anyone Harvey finds: an email is verified before any cold email."""
    from harvey.models.prospect import Prospect
    state = _state()
    company = await state.get_company(company_id)
    if not company:
        return JSONResponse({"success": False, "message": "No such company."}, status_code=404)
    fields, why = await _contact_fields(state, await _json_body(request))
    if why:
        return _refuse(why)
    if not any(fields.get(k) for k in ("first_name", "last_name", "email", "phone")):
        return _refuse("Give at least a name, an email or a phone.")
    prospect = Prospect(company_id=company.id, company=company.name, industry=company.industry,
                        source="manual", status="new", **fields)
    prospect_id = await state.add_prospect(prospect)
    return {"success": True, "id": prospect_id, "email_status": fields.get("email_status", "")}


@app.patch("/api/prospects/{prospect_id}")
async def edit_contact(prospect_id: str, request: Request):
    state = _state()
    if not await state.get_prospect(prospect_id):
        return JSONResponse({"success": False, "message": "No such contact."}, status_code=404)
    fields, why = await _contact_fields(state, await _json_body(request), prospect_id)
    if why:
        return _refuse(why)
    if not fields:
        return _refuse("Nothing to change.")
    await state.update_contact(prospect_id, **fields)
    return {"success": True, "email_status": fields.get("email_status", "")}


@app.delete("/api/prospects/{prospect_id}")
async def delete_contact(prospect_id: str):
    """Delete a contact nobody has written to yet, with their unsent messages."""
    refused = await _state().remove_contact(prospect_id)
    if refused:
        return _refuse(refused)
    return {"success": True}


@app.get("/api/companies/industries")
async def company_industries():
    """The kinds of business already on file, most common first: the choices
    when a company is edited."""
    return await query_db("SELECT industry AS name, COUNT(*) AS n FROM companies "
                          "WHERE industry != '' GROUP BY industry ORDER BY n DESC, industry LIMIT 300")


@app.patch("/api/companies/{company_id}")
async def edit_company(company_id: str, request: Request):
    """Fix what a listing got wrong: name, industry, place, phone, website."""
    from harvey.state import StateManager
    data = await _json_body(request)
    fields = {}
    for key in StateManager.COMPANY_EDITABLE:
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > 300:
                return _refuse(f"{key} must be text (up to 300 characters).")
            fields[key] = " ".join(value.split())
    if "name" in fields and not fields["name"]:
        return _refuse("A company needs a name.")
    if fields.get("website") and urlparse(fields["website"]).scheme not in ("http", "https"):
        return _refuse("The website must start with http:// or https://.")
    if not fields:
        return _refuse("Nothing to change.")
    if not await _state().update_company(company_id, **fields):
        return JSONResponse({"success": False, "message": "No such company."}, status_code=404)
    return {"success": True}


@app.delete("/api/companies/{company_id}")
async def delete_company(company_id: str):
    """Delete a company nobody has talked to yet; discovery won't add it back."""
    refused = await _state().remove_company(company_id)
    if refused:
        return _refuse(refused)
    return {"success": True}


# A Google Maps link the "Add a company" box accepts. Short links are resolved
# by following redirects, and only ever to these hosts.
_MAPS_HOSTS = re.compile(r"(^|\.)(google\.[a-z.]{2,8}|goo\.gl)$")


async def _maps_query(text: str) -> tuple[str, str]:
    """(what to search for, "lat,lng,radius_km" bias) from a name or a Maps link;
    ("", "") when a link can't be read."""
    from urllib.parse import parse_qs, unquote_plus
    if not text.lower().startswith(("http://", "https://")):
        return text, ""
    url = text
    import httpx
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for _ in range(4):
            host = (urlparse(url).hostname or "").lower()
            if not _MAPS_HOSTS.search(host):
                return "", ""
            if "goo.gl" not in host:
                break
            try:
                r = await client.get(url)
            except httpx.HTTPError:
                return "", ""
            url = r.headers.get("location", "")
    parsed = urlparse(url)
    place = re.search(r"/maps/place/([^/@]+)", parsed.path)
    name = unquote_plus(place.group(1)) if place else (parse_qs(parsed.query).get("q") or [""])[0]
    at = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", parsed.path)
    return " ".join(name.split())[:200], f"{at.group(1)},{at.group(2)},2" if at else ""


@app.post("/api/companies/lookup")
async def companies_lookup(request: Request):
    """Search Google Maps for one business, by name and city or a Maps link.
    Nothing is saved: the person picks the right result first."""
    from dataclasses import asdict
    import httpx
    from harvey.collectors.discover import PROVIDERS, TIMEOUT, DiscoveryQuery
    from harvey.config import load_env
    q = " ".join(str((await _json_body(request)).get("q") or "").split())[:500]
    if not q:
        return _refuse("Type a business name and city, or paste a Google Maps link.")
    term, bias = await _maps_query(q)
    if not term:
        return _refuse("Couldn't read that link. Paste the business name and city instead.")
    provider = PROVIDERS["google_places"]
    env = load_env().model_dump()
    if not provider.configured(env):
        return _refuse("Searching needs GOOGLE_PLACES_API_KEY in .env.")
    state = _state()
    run_id = await state.start_run("discover", provider=provider.key, params={"manual": term[:100]})
    provider.last_cost = 0.0
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            found = await provider.fetch(client, env, DiscoveryQuery(term=term, coordinate=bias, limit=5))
    except Exception as e:
        await state.finish_run(run_id, status="failed", cost_usd=provider.last_cost, error=str(e)[:300])
        logger.warning("Company lookup failed: %s", e)
        return _refuse("Google Maps didn't answer. Try again in a moment.", 502)
    await state.finish_run(run_id, status="completed", records=len(found), cost_usd=provider.last_cost)
    results = []
    for biz in found:
        known = ((biz.domain and await state.get_company_by_domain(biz.domain))
                 or (biz.external_id and await state.get_company_by_external_id(biz.external_id)))
        results.append({**asdict(biz), "known_id": known.id if known else ""})
    return {"results": results}


def _assessor(state, config, brain=None):
    from harvey.agents.whatsapp import WhatsAppSender
    from harvey.config import load_env
    return WhatsAppSender(brain, state, config, load_env(), send_pacing=False)


@app.post("/api/companies/add")
async def companies_add(request: Request):
    """Save a business picked from a lookup, then check whether Harvey can
    open a WhatsApp chat with it. Nothing is sent."""
    from harvey.collectors.discover import Business, DiscoveryQuery, _observations, _upsert, normalize_domain
    from harvey.config import load_config
    raw = (await _json_body(request)).get("business")
    if not isinstance(raw, dict):
        return _refuse("Pick a business from the search results.")
    text = lambda k, n: " ".join(str(raw.get(k) or "").split())[:n]
    number = lambda k: raw.get(k) if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool) else None
    biz = Business(
        name=text("name", 200), website=text("website", 300), phone=text("phone", 40),
        address=text("address", 300), category=text("category", 100),
        rating=number("rating"), review_count=number("review_count"),
        external_id=text("external_id", 200), source_url=text("source_url", 500), provider="google_places")
    if not biz.name or not biz.external_id.startswith("google_places:"):
        return _refuse("Pick a business from the search results.")
    # Shown as links later: only plain web addresses.
    web = lambda u: u if urlparse(u).scheme in ("http", "https") else ""
    biz.website, biz.source_url = web(biz.website), web(biz.source_url)
    biz.domain = normalize_domain(biz.website)
    biz.has_website = bool(biz.website)
    state = _state()
    await state.unremove_company(biz.domain, biz.external_id)   # deleted before, added back on purpose
    company_id, is_new = await _upsert(state, biz, DiscoveryQuery(term=biz.category), source="manual")
    if is_new:
        await state.add_observations(
            _observations(biz, company_id, await state.confirmed_signal_codes(), ""))
    company = await state.get_company(company_id)
    a = await _assessor(state, load_config()).assess(company, manual=True)
    return {"success": True, "company_id": company_id, "new": is_new,
            "checks": a.checks, "can_open": not a.blocker}


@app.post("/api/companies/{company_id}/whatsapp")
async def companies_open_whatsapp(company_id: str):
    """Write the first WhatsApp message for a company added by hand. It waits
    in the Outbox for approval, and goes out in business hours like any other."""
    from harvey.brain import Brain
    from harvey.config import load_config
    state = _state()
    company = await state.get_company(company_id)
    if not company:
        return JSONResponse({"success": False, "message": "No such company."}, status_code=404)
    config = load_config()
    sender = _assessor(state, config)
    if not sender.client.is_configured():
        return _refuse("WhatsApp (Evolution) is not configured.")   # a 503 would lock the dashboard
    a = await sender.assess(company, manual=True)
    if a.blocker:
        return _refuse(next((c["text"] for c in a.checks if not c["ok"] and not c["warning"]),
                            "Roadie Crew can't open a chat with this business."))
    sender.brain = Brain(state, config.brain)
    sender.skills = sender.brain.load_skills_for_agent("whatsapp") + await state.lessons_block()
    item_id = await sender.stage(company, a.phone, review=True)
    if not item_id:
        return _refuse("Roadie Crew couldn't write a usable first message. Try again.", 502)
    return {"success": True, "outbox_id": item_id}


@app.get("/api/companies/{company_id}/contacts")
async def get_company_contacts(company_id: str):
    """Get all contacts for a specific company."""
    rows = await query_db(
        "SELECT * FROM prospects WHERE company_id = ? ORDER BY score DESC",
        (company_id,),
    )
    return rows


# ── Feedback ──


@app.post("/api/feedback")
async def add_feedback(request: Request):
    """Add a comment/feedback on any entity."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    entity_type = str(data.get("entity_type", "") or "")[:50]
    entity_id = str(data.get("entity_id", "") or "")[:100]
    comment = str(data.get("comment", "") or "").strip()[:4000]
    if not comment:
        return {"success": False, "message": "Comment is required."}
    feedback_id = uuid.uuid4().hex[:12]
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            # Ensure the table exists so feedback works even on a fresh install
            await db.execute(
                """CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    comment TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )"""
            )
            await db.execute(
                "INSERT INTO feedback (id, entity_type, entity_id, comment) VALUES (?, ?, ?, ?)",
                (feedback_id, entity_type, entity_id, comment),
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to save feedback: %s", e)
        return {"success": False, "message": "Could not save feedback."}
    return {"success": True, "id": feedback_id}


@app.get("/api/feedback/{entity_type}/{entity_id}")
async def get_feedback(entity_type: str, entity_id: str):
    """Get feedback for an entity."""
    rows = await query_db(
        "SELECT * FROM feedback WHERE entity_type = ? AND entity_id = ? ORDER BY created_at DESC",
        (entity_type, entity_id),
    )
    return rows


# ── Harvey Controls ──


@app.get("/api/harvey/status")
async def get_harvey_status():
    """Check if Harvey is currently running."""
    if IN_CONTAINER:  # the loop is another container: its beacon file is the only sign of life
        alive = DB_PATH.parent / "harvey.alive"
        up = alive.exists() and time.time() - alive.stat().st_mtime < ALIVE_STALE_SECONDS
        return {"running": up, "pid": None, "started_at": None}
    pid = _check_harvey_pid()
    started = _harvey_started_at.isoformat() if _harvey_started_at else None
    return {"running": pid is not None, "pid": pid, "started_at": started}


@app.post("/api/harvey/start")
async def start_harvey():
    """Start Harvey's heartbeat loop as a subprocess."""
    global _harvey_process, _harvey_started_at

    if IN_CONTAINER:
        return {"success": False, "message": CONTAINER_NOTE}
    if _check_harvey_pid():
        return {"success": False, "message": "Roadie Crew is already running."}

    # Ensure data dir exists
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)

    try:
        log_handle = open(LOG_FILE, "a", encoding="utf-8")
        try:
            _harvey_process = subprocess.Popen(
                [sys.executable, "-m", "harvey"],
                cwd=str(PROJECT_ROOT),
                stdout=log_handle,
                stderr=log_handle,
                start_new_session=True,
            )
        finally:
            # Child holds its own copies of the fds; don't leak ours.
            log_handle.close()
    except Exception as e:
        logger.warning("Failed to start Harvey: %s", e)
        return {"success": False, "message": f"Failed to start Roadie Crew: {e}"}
    _harvey_started_at = datetime.now()

    # Write PID file
    try:
        PID_FILE.write_text(str(_harvey_process.pid), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write PID file: %s", e)

    return {"success": True, "pid": _harvey_process.pid}


@app.post("/api/harvey/stop")
async def stop_harvey():
    """Stop the Harvey subprocess."""
    global _harvey_process, _harvey_started_at

    if IN_CONTAINER:
        return {"success": False, "message": CONTAINER_NOTE}

    pid = _check_harvey_pid()
    if not pid:
        return {"success": False, "message": "Roadie Crew is not running."}

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait briefly for graceful shutdown
        for _ in range(10):
            try:
                os.kill(pid, 0)
                await asyncio.sleep(0.5)
            except ProcessLookupError:
                break
        else:
            # Force kill if still running
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except (ProcessLookupError, PermissionError):
        pass

    _harvey_process = None
    _harvey_started_at = None
    PID_FILE.unlink(missing_ok=True)

    return {"success": True}


@app.get("/api/harvey/logs")
async def get_harvey_logs():
    """Get recent log lines."""
    if not LOG_FILE.exists():
        return {"lines": []}
    try:
        # Tail only the last 64KB so a huge log file never blocks the UI
        with open(LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 65536))
            text = f.read().decode("utf-8", errors="replace")
        lines = text.strip().splitlines()[-100:]
        return {"lines": lines}
    except Exception:
        return {"lines": []}


# ── Pipeline Data (existing endpoints) ──


def _pipeline_bucket(status: str | None, stage: str | None, intent: str | None,
                     email_status: str | None = None) -> str:
    """One funnel stage per contact, from its status and latest conversation."""
    status = status or "new"
    if status in ("new", "queued"):
        return "to_write"
    if status == "contacted":
        # A bounce leaves the status alone and marks the address invalid.
        return "closed_out" if email_status == "invalid" else "contacted"
    if status == "meeting":
        return "ready_to_meet"
    if status == "replied" and stage != "closed_lost":
        if stage == "closing":
            return "ready_to_meet"
        if intent == "interested" or stage in ("presenting", "negotiating"):
            return "interested"
        return "replied"
    return "closed_out"   # lost, opted out, bounced, unwritable


@app.get("/api/funnel/cities")
async def funnel_by_city():
    """The pipeline per city, to compare markets."""
    try:
        return await _state().funnel_by_location()
    except Exception as e:
        logger.warning("funnel by city failed: %s", e)
        return []


@app.get("/api/stats")
async def get_stats():
    """Pipeline overview stats."""
    try:
        prospects = await query_db(
            "SELECT status, COUNT(*) as count FROM prospects GROUP BY status"
        )
        prospect_total = sum(r["count"] for r in prospects)
        prospect_map = {r["status"]: r["count"] for r in prospects}

        campaigns = await query_db(
            "SELECT status, COUNT(*) as count FROM campaigns GROUP BY status"
        )
        campaign_map = {r["status"]: r["count"] for r in campaigns}

        conversations = await query_db(
            "SELECT status, COUNT(*) as count FROM conversations GROUP BY status"
        )
        convo_map = {r["status"]: r["count"] for r in conversations}

        # Where each contact stands. Prospect status alone stops at
        # "replied"; how a reply went lives on its latest conversation.
        rows = await query_db("""
            SELECT p.status, p.email_status, l.stage, l.intent, COUNT(*) AS count FROM prospects p
            LEFT JOIN (SELECT prospect_id, stage, intent, ROW_NUMBER() OVER (
                         PARTITION BY prospect_id ORDER BY updated_at DESC, created_at DESC) AS rn
                       FROM conversations) l ON l.prospect_id = p.id AND l.rn = 1
            GROUP BY p.status, p.email_status, l.stage, l.intent
        """)
        pipeline = dict.fromkeys(
            ("to_write", "contacted", "replied", "interested", "ready_to_meet", "closed_out"), 0)
        for r in rows:
            pipeline[_pipeline_bucket(r["status"], r["stage"], r["intent"],
                                      r["email_status"])] += r["count"]

        actions = await query_db("SELECT COUNT(*) as count FROM actions")
        action_count = actions[0]["count"] if actions else 0

        usage = await query_db(
            "SELECT claude_calls, codex_calls FROM usage_log WHERE date = date('now')"
        )
        usage_today = (usage[0]["claude_calls"] or 0) if usage else 0
        codex_today = (usage[0]["codex_calls"] or 0) if usage else 0

        return {
            "prospects": {"total": prospect_total, "by_status": prospect_map},
            "campaigns": {"total": sum(campaign_map.values()), "by_status": campaign_map},
            "conversations": {"total": sum(convo_map.values()), "by_status": convo_map},
            "pipeline": pipeline,
            "actions_total": action_count,
            "claude_calls_today": usage_today,
            "codex_calls_today": codex_today,
            "brain_provider": _active_provider(),
            "brain_status": await _brain_status(),
        }
    except Exception as e:
        return {"error": str(e)}


_USAGE_SUM = (
    "COUNT(DISTINCT CASE WHEN session_id != '' THEN session_id ELSE id END) AS calls, "
    "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
    "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
    "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
    "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
    "ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd"
)

_quota_clients: dict = {}


def _active_brain():
    """The brain config harvey.yaml selects (None if unreadable)."""
    try:
        from harvey.config import load_brain_config
        return load_brain_config()
    except Exception:
        return None


async def _brain_status() -> dict:
    """Last recorded brain health transition ({} if none): lets the
    dashboard say "Codex paused: <why>" instead of looking healthy."""
    from harvey.brain import BRAIN_STATUS_KEY, active_brain_status
    rows = await query_db("SELECT value FROM settings WHERE key = ?", (BRAIN_STATUS_KEY,))
    # Only the configured provider's health (a stale Codex pause isn't
    # Claude's).
    return active_brain_status(rows[0]["value"], _active_provider()) if rows else {}


def _active_provider() -> str:
    brain = _active_brain()
    return brain.provider if brain else ""


def _quota_client_for(provider: str, model: str = ""):
    """One cached quota reader per provider (and Codex model). Each reads
    only its own subscription, so a Claude gauge is never shown as Codex or
    vice versa, and Codex shows the bucket metering the configured model."""
    key = (provider, model if provider == "codex" else "")
    if key not in _quota_clients:
        if provider == "codex":
            from harvey.codex import CodexClient, CodexQuotaClient
            _quota_clients[key] = CodexQuotaClient(CodexClient(model=model))
        elif provider == "claude":
            from harvey.integrations.quota import QuotaClient
            _quota_clients[key] = QuotaClient()
        else:
            return None
    return _quota_clients[key]


@app.get("/api/usage")
async def get_usage():
    """Token/cost accounting + live subscription quota for the Usage tab."""

    totals = {}
    for label, where in (
        ("today", "date(created_at) = date('now')"),
        ("week", "created_at >= datetime('now', '-7 days')"),
        ("month", "created_at >= datetime('now', '-30 days')"),
    ):
        rows = await query_db(f"SELECT {_USAGE_SUM} FROM usage_events WHERE {where}")
        totals[label] = rows[0] if rows else {}

    def grouped(expr, alias):
        return (
            f"SELECT {expr} AS {alias}, {_USAGE_SUM} FROM usage_events "
            f"WHERE created_at >= datetime('now', '-30 days') "
            f"GROUP BY {alias} ORDER BY output_tokens DESC LIMIT 25"
        )

    by_agent = await query_db(grouped("CASE WHEN agent = '' THEN 'other' ELSE agent END", "agent"))
    by_task = await query_db(grouped("CASE WHEN task = '' THEN 'other' ELSE task END", "task"))
    by_model = await query_db(grouped("CASE WHEN model = '' THEN 'unknown' ELSE model END", "model"))
    by_provider = await query_db(grouped("provider", "provider"))
    by_day = await query_db(
        f"SELECT date(created_at) AS day, {_USAGE_SUM} FROM usage_events "
        f"WHERE created_at >= datetime('now', '-30 days') "
        f"GROUP BY day ORDER BY day ASC"
    )

    brain = _active_brain()
    provider = brain.provider if brain else ""
    quota = None
    try:
        client = _quota_client_for(provider, brain.model if brain else "")
        if client is not None:
            quota = await client.get_utilization()
    except Exception as e:
        logger.debug("Quota lookup failed: %s", e)

    return {
        "provider": provider,
        "quota": quota,
        # Whose quota this is, even when it couldn't be read, so the UI shows
        # "unavailable" for the active provider instead of hiding the gauge.
        "quota_provider": provider,
        "quota_status": ("available" if quota else "unavailable") if provider else "",
        "by_provider": by_provider,
        "totals": totals,
        "by_day": by_day,
        "by_agent": by_agent,
        "by_task": by_task,
        "by_model": by_model,
    }


@app.get("/api/prospects")
async def get_prospects():
    rows = await query_db("SELECT * FROM prospects ORDER BY created_at DESC LIMIT 200")
    return rows


def _state():
    from harvey.state import StateManager
    return StateManager(db_path=str(DB_PATH))


# What a reviewer needs next to a draft to check its claims against the facts.
OUTBOX_CONTEXT_SIGNALS = (
    "TECH_STACK", "INCUMBENT_AGENCY", "HIRING_ROLE", "RUNNING_GOOGLE_ADS", "NO_ONLINE_BOOKING",
)


def _in(ids) -> str:
    return ",".join("?" for _ in ids)


async def _with_context(items: list[dict]) -> list[dict]:
    """Attach the campaign, company, contact and signals behind each draft."""
    if not items:
        return items
    pids = sorted({i["prospect_id"] for i in items if i.get("prospect_id")})
    cids = sorted({i["campaign_id"] for i in items if i.get("campaign_id")})

    people = {r["id"]: r for r in (await query_db(
        "SELECT p.id, p.first_name, p.last_name, p.title, p.source, p.email_status, "
        "p.score, p.personalization_notes, p.company_id, p.instagram_handle, COALESCE(c.name, p.company) AS company_name, "
        "c.domain, c.website, c.description, c.industry AS found_by "
        "FROM prospects p LEFT JOIN companies c ON c.id = p.company_id "
        f"WHERE p.id IN ({_in(pids)})", tuple(pids)) if pids else [])}
    campaigns = {r["id"]: r["name"] for r in (await query_db(
        f"SELECT id, name FROM campaigns WHERE id IN ({_in(cids)})",
        tuple(cids)) if cids else [])}

    company_ids = sorted({p["company_id"] for p in people.values() if p.get("company_id")})
    signals: dict[str, dict[str, str]] = {}
    if company_ids:
        rows = await query_db(
            "SELECT o.company_id, o.signal_code, o.value_num, o.value_text, "
            "o.observed_at, o.run_id, "
            "COALESCE(NULLIF(sc.label, ''), o.signal_code) AS label FROM observations o "
            "LEFT JOIN signal_codes sc ON sc.code = o.signal_code "
            f"WHERE o.company_id IN ({_in(company_ids)}) "
            f"AND o.signal_code IN ({_in(OUTBOX_CONTEXT_SIGNALS)}) "
            "ORDER BY o.observed_at, o.id",
            (*company_ids, *OUTBOX_CONTEXT_SIGNALS),
        )
        # Oldest first, so the latest observation wins. A signal can be seen
        # several times at once (one HIRING_ROLE row per open role): every
        # value from the latest sighting shows, not just one of them.
        latest: dict[tuple, tuple] = {}
        for r in rows:
            if r["value_text"]:
                value = r["value_text"]
            elif r["value_num"] is None:
                value = ""
            else:
                value = "yes" if r["value_num"] else "no"
            key = (r["company_id"], r["label"])
            seen = (r["run_id"] or r["observed_at"])
            prev_seen, values = latest.get(key, (None, []))
            if seen != prev_seen:
                values = []
            if value not in values:
                values = values + [value]
            latest[key] = (seen, values)
        for (company_id, label), (_, values) in latest.items():
            signals.setdefault(company_id, {})[label] = ", ".join(v for v in values if v)

    for item in items:
        p = people.get(item.get("prospect_id")) or {}
        # An Instagram DM goes to a numeric user id: show the @ instead.
        item["recipient"] = (f"@{p['instagram_handle']}" if item.get("channel") == "instagram"
                             and p.get("instagram_handle") else item.get("to_email", ""))
        item["context"] = {
            "campaign": campaigns.get(item.get("campaign_id"), ""),
            "company": p.get("company_name", ""),
            "website": p.get("website") or (f"https://{p['domain']}" if p.get("domain") else ""),
            "description": p.get("description", ""),
            "found_by": p.get("found_by", ""),
            "contact": " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x),
            "contact_title": p.get("title", ""),
            "contact_source": p.get("source", ""),
            "email_status": p.get("email_status", ""),
            "score": p.get("score"),
            "notes": p.get("personalization_notes", ""),
            "signals": signals.get(p.get("company_id"), {}),
        }
    return items


@app.get("/api/outbox")
async def get_outbox_api():
    """Outbox queue + kill-switch state for the Outbox tab."""
    try:
        state = _state()
        await state.init_db()
        # A send cut off mid-way shows up under Didn't send even when Harvey
        # isn't running.
        await state.fail_stale_sending_outbox()
        return {
            **_compliance_view(),
            "paused": await state.get_setting("sending_paused"),
            "pending": _add_sends_from(await _with_context(
                await state.get_outbox(status="pending_review", limit=100))),
            # Mail going out right now leads the list.
            "approved": (await state.get_outbox(status="sending", limit=10)
                         + await state.get_outbox(status="approved", limit=50)),
            "sent": (await query_db(
                "SELECT * FROM outbox WHERE status = 'sent' "
                "ORDER BY sent_at DESC LIMIT 25")),
            "failed": (await query_db(
                "SELECT * FROM outbox WHERE status IN ('failed','rejected','cancelled') "
                "ORDER BY updated_at DESC LIMIT 25")),
        }
    except Exception as e:
        return {"error": str(e)}


_OUTBOX_GROUPS = {
    # group: (statuses, order)
    "approved": (("sending", "approved"), "o.status = 'sending' DESC, o.send_at ASC, o.created_at ASC"),
    "sent": (("sent",), "o.sent_at DESC"),
    "failed": (("failed", "rejected", "cancelled"), "o.updated_at DESC"),
}
OUTBOX_PAGE = 25


@app.get("/api/outbox/list")
async def outbox_list(group: str = "approved", q: str = "", page: int = 1):
    """One page of an Outbox table, searchable by number or address, company,
    message text or the reason it didn't send. Rows carry the whole message."""
    if group not in _OUTBOX_GROUPS:
        return JSONResponse({"success": False, "message": "Unknown group."}, status_code=400)
    statuses, order = _OUTBOX_GROUPS[group]
    where = f"o.status IN ({_in(statuses)})"
    params: tuple = tuple(statuses)
    q = q.strip()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (" + " OR ".join(
            f"{c} LIKE ? ESCAPE '\\'" for c in ("o.to_email", "o.body", "o.subject", "o.error", "p.company")) + ")"
        params += (like,) * 5
    base = f"FROM outbox o LEFT JOIN prospects p ON p.id = o.prospect_id WHERE {where}"
    counted = await query_db(f"SELECT COUNT(*) AS n {base}", params)
    total = counted[0]["n"] if counted else 0   # no database yet
    page = max(1, page)
    rows = await query_db(
        f"SELECT o.*, p.company AS company, CASE WHEN o.channel = 'instagram' "
        f"AND COALESCE(p.instagram_handle, '') != '' THEN '@' || p.instagram_handle "
        f"ELSE o.to_email END AS recipient {base} ORDER BY {order} LIMIT ? OFFSET ?",
        params + (OUTBOX_PAGE, (page - 1) * OUTBOX_PAGE))
    if group == "approved":
        _add_sends_from(rows)
    return {"rows": rows, "total": total, "page": page, "per_page": OUTBOX_PAGE}


_FOLLOW_UPS = ("FROM conversations c LEFT JOIN prospects p ON p.id = c.prospect_id "
               "WHERE c.status = 'open' AND c.follow_up_at != ''")


@app.get("/api/outbox/follow-ups")
async def outbox_follow_ups(q: str = ""):
    """Follow-ups Harvey planned for leads who said they'd get back: no
    message yet (it is written when due), so they live on the conversation."""
    where, params = _FOLLOW_UPS, ()
    q = q.strip()[:100]
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where += " AND (p.company LIKE ? ESCAPE '\\' OR p.phone LIKE ? ESCAPE '\\')"
        params = (like, like)
    rows = await query_db(
        "SELECT c.id AS conversation_id, c.follow_up_at, c.follow_ups, c.thread_json, "
        f"p.company, p.phone {where} ORDER BY c.follow_up_at LIMIT 200", params)
    for r in rows:
        try:
            thread = json.loads(r.pop("thread_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            thread = []
        last = thread[-1] if thread else {}
        r["last"] = {"sender": last.get("sender", ""), "content": str(last.get("content", ""))[:200]}
    return {"rows": rows, "total": len(rows)}


@app.get("/api/outbox/counts")
async def outbox_counts():
    """How many rows each Outbox tab holds."""
    counts = {g: 0 for g in _OUTBOX_GROUPS}
    for r in await query_db("SELECT status, COUNT(*) AS n FROM outbox GROUP BY status"):
        for g, (statuses, _) in _OUTBOX_GROUPS.items():
            if r["status"] in statuses:
                counts[g] += r["n"]
    found = await query_db(f"SELECT COUNT(*) AS n {_FOLLOW_UPS}")
    counts["follow_ups"] = found[0]["n"] if found else 0
    return counts


def _add_sends_from(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Mark rows business hours hold back with `sends_from`: when the window
    next opens after max(send_at, now), as naive UTC like send_at. The daily
    caps can push a row later still; that isn't predicted here."""
    from harvey.config import closed_until, load_config
    try:
        config = load_config(str(_resolve_config_file()))
    except Exception:
        return rows  # no hours to show; the table still lists the rows
    now = now or datetime.now(timezone.utc)
    for r in rows:
        try:
            at = datetime.fromisoformat(r["send_at"]).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        opens = closed_until(config, max(at, now))
        if opens:
            r["sends_from"] = opens.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    return rows


@app.post("/api/outbox/approve-all")
async def outbox_approve_all():
    try:
        state = _state()
        await state.init_db()
        n = await state.approve_outbox()
        return {"success": True, "approved": n}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/approve")
async def outbox_approve(item_id: str):
    try:
        state = _state()
        await state.init_db()
        n = await state.approve_outbox(item_id)
        return {"success": bool(n)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/edit")
async def outbox_edit(item_id: str, request: Request):
    """Rewrite a draft awaiting review. Never approves it."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    subject = str(body.get("subject") or "").strip()
    text = str(body.get("body") or "").strip()
    try:
        state = _state()
        await state.init_db()
        item = await state.get_outbox_item(item_id)
        if not item or item.get("status") != "pending_review":
            return JSONResponse({"success": False,
                                 "message": "Only drafts awaiting review can be edited."},
                                status_code=409)
        if item.get("kind") == "reply":
            # Gmail threads a reply by its subject: a new one would land as a
            # fresh, unthreaded email to someone who wrote in. Only the body
            # of a reply is editable; its stored subject is kept exactly.
            subject = item.get("subject") or ""
        if not subject.strip() or not text:
            return JSONResponse({"success": False,
                                 "message": "Subject and body are both required."},
                                status_code=400)
        # The sender re-checks every email before it goes out; an edit it
        # would block is refused now, while the reviewer can still fix it,
        # instead of failing quietly at send time. Content rules only: the
        # recipient isn't something an edit can change.
        from harvey.gate import pre_send_check, whatsapp_check
        checked = text
        footer = _compliance_view()["footer"]
        if item.get("channel") == "whatsapp":
            from harvey.config import load_config
            wa = load_config().channels.whatsapp
            gate = whatsapp_check(
                item.get("to_email", ""), text, kind=item.get("kind", "sequence"),
                opt_out_line=wa.opt_out_line, allowed_prices=wa.allowed_prices,
                allowed_links=[d.url for d in wa.demos])
        else:
            if item.get("kind") == "sequence" and footer and not text.rstrip().endswith(footer):
                checked = text.rstrip() + "\n\n" + footer  # as the Sender will send it
            gate = pre_send_check("reviewer@example.com", subject, checked)
        if not gate:
            return JSONResponse({"success": False,
                                 "message": "The sender would have blocked this: "
                                            + "; ".join(gate.reasons)},
                                status_code=400)
        if not await state.edit_outbox_draft(item_id, subject, text):
            return JSONResponse({"success": False,
                                 "message": "Only drafts awaiting review can be edited."},
                                status_code=409)
        await state.log_action("outbox_edited", "dashboard", {
            "outbox_id": item_id, "to_email": item.get("to_email", ""),
            "old_subject": item.get("subject", ""), "old_body": item.get("body", ""),
        })
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


# A hand send is only offered to someone Harvey could still email: never to
# someone who replied, opted out, bounced or was already contacted.
HAND_SENDABLE_STATUSES = ("new", "queued")
HAND_SENDABLE_EMAIL = ("verified", "risky")


def _harvey_can_send() -> bool:
    """Can Harvey send email itself (a connected mailbox, or Instantly)?

    Sending by hand from the dashboard is only offered when it can't: with
    two senders, any timing gap between them emails someone twice. If the
    config can't be read, the answer is yes (the safe side: no hand sends).
    """
    try:
        from harvey.config import load_config, load_env
        from harvey.integrations.mail_provider import NATIVE_PROVIDERS, get_mail_provider
        config, env = load_config(), load_env()
        from harvey.config import legacy_sending_held
        email = config.channels.email
        if not email.enabled or legacy_sending_held(config):
            return False
        if (email.provider or "").strip().lower() in NATIVE_PROVIDERS:
            provider = get_mail_provider(config, env)
            return bool(provider and provider.is_configured())
        return bool(env.instantly_api_key)
    except Exception:
        return True


def _send_as() -> str:
    """The address Harvey sends as, so Gmail opens the right account."""
    try:
        from harvey.config import load_config
        return load_config().persona.email or ""
    except Exception:
        return ""


def _compliance_view() -> dict:
    """What the Sender adds under each cold email, so a reviewer sees the
    email as it will leave, and the warning when the address is missing."""
    try:
        from harvey.config import compliance_footer_lines, compliance_notice, load_config
        config = load_config()
        return {"footer": "\n".join(compliance_footer_lines(config)),
                "compliance_notice": compliance_notice(config)}
    except Exception:
        return {"footer": "", "compliance_notice": ""}


async def _harvey_committed_ids() -> set[str]:
    """Everyone Harvey has approved or sent mail for (Outbox or Instantly).

    Approved mail can be going out at this moment, so these people are past
    the point where a hand send can be recorded safely. So is anyone whose
    send was cut off mid-way: it may have reached them. A draft that only
    awaits review isn't: marking the person sent cancels it.
    """
    from harvey.state import MAYBE_SENT_PREFIX
    ids = {r["prospect_id"] for r in await query_db(
        "SELECT DISTINCT prospect_id FROM outbox WHERE status IN ('approved', 'sending', 'sent') "
        "OR (status = 'failed' AND substr(error, 1, ?) = ?)",
        (len(MAYBE_SENT_PREFIX), MAYBE_SENT_PREFIX))}
    for row in await query_db(
            "SELECT prospect_ids_json FROM campaigns WHERE COALESCE(instantly_campaign_id, '') != ''"):
        try:
            found = json.loads(row.get("prospect_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(found, list):
            ids.update(i for i in found if isinstance(i, str))
    return ids


async def _hand_sent_ids() -> set[str]:
    from harvey.state import MANUAL_SEND_KEY
    rows = await query_db("SELECT key FROM settings WHERE key LIKE ?", (MANUAL_SEND_KEY + "%",))
    return {r["key"][len(MANUAL_SEND_KEY):] for r in rows}


async def _harvey_sent_to(prospect_id: str) -> bool:
    """Did Harvey itself email this person (native send, or an Instantly campaign)?

    A send in flight, or cut off mid-way, counts: it may have reached them.
    """
    from harvey.state import MAYBE_SENT_PREFIX
    if await query_db(
            "SELECT 1 FROM outbox WHERE prospect_id = ? AND (status IN ('sending', 'sent') "
            "OR (status = 'failed' AND substr(error, 1, ?) = ?)) LIMIT 1",
            (prospect_id, len(MAYBE_SENT_PREFIX), MAYBE_SENT_PREFIX)):
        return True
    for row in await query_db(
            "SELECT prospect_ids_json FROM campaigns WHERE COALESCE(instantly_campaign_id, '') != ''"):
        try:
            if prospect_id in json.loads(row.get("prospect_ids_json") or "[]"):
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False


def _refuse(message: str, code: int = 409):
    return JSONResponse({"success": False, "message": message}, status_code=code)


@app.post("/api/prospects/{prospect_id}/sent")
async def mark_prospect_sent(prospect_id: str, request: Request):
    """The user sent this email by hand (e.g. from Gmail): record it, or undo it."""
    from harvey.state import HAND_SENT_REASON, MANUAL_SEND_KEY
    try:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict) or not isinstance(body.get("sent", True), bool):
            return _refuse('expected {"sent": true|false}', 400)
        sent = body.get("sent", True)
        rows = await query_db("SELECT status FROM prospects WHERE id = ?", (prospect_id,))
        if not rows:
            return _refuse("unknown prospect", 404)
        current = rows[0].get("status") or "new"
        state = _state()
        await state.init_db()
        key = MANUAL_SEND_KEY + prospect_id
        marked = bool(await state.get_setting(key))

        if sent:
            if marked:
                return {"success": True, "sent": True, "already": True}
            if current not in HAND_SENDABLE_STATUSES:
                # A reply, opt-out or close is never touched; neither is
                # someone Harvey already emailed.
                return {"success": True, "sent": True, "status": current}
            # Allowed even when Harvey can send: recording "I already emailed
            # them" can only ever stop Harvey sending. Not once Harvey has
            # approved or sent mail to them, though: that may be going out now.
            # The marker goes down FIRST: a drain that checks after this sees
            # it and cancels; one that checked before only holds an approved
            # row, which the check below then sees.
            await state.set_setting(key, current)
            if prospect_id in await _harvey_committed_ids():
                await state.delete_setting(key)
                return _refuse("Roadie Crew has already approved or sent an email to this person. "
                               "Check the Outbox.")
            await state.update_prospect_status(prospect_id, "contacted")
            # Anything Harvey had queued for them must not go out as well.
            changed = await state.cancel_pending_outbox_for_prospect(
                prospect_id, reason=HAND_SENT_REASON)
        else:
            if not marked:
                if current in HAND_SENDABLE_STATUSES:
                    return {"success": True, "sent": False}
                if current == "contacted":
                    # Setting them back would make Harvey write and send again.
                    return _refuse("This wasn't marked as sent by hand here, so it stays sent.")
                return {"success": True, "sent": True, "status": current}
            if current != "contacted":
                # They replied, opted out or bounced since: that stands.
                return {"success": True, "sent": True, "status": current}
            previous = await state.get_setting(key) or "queued"
            await state.update_prospect_status(
                prospect_id, previous if previous in HAND_SENDABLE_STATUSES else "queued")
            await state.delete_setting(key)
            # What marking sent cancelled comes back for review, never straight out.
            changed = await state.restore_hand_cancelled_outbox(
                prospect_id, reason=HAND_SENT_REASON)
        if sent:
            # Open in Gmail adds today's footer; remember it as Harvey's text.
            footer = _compliance_view()["footer"]
            await state.remember_footer_lines(footer.split("\n"))
        await state.log_action(
            action_type="manual_send" if sent else "manual_send_undone",
            agent="user",
            details={"prospect_id": prospect_id, "outbox_items": changed},
        )
        return {"success": True, "sent": sent}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/outbox/{item_id}/reject")
async def outbox_reject(item_id: str):
    try:
        state = _state()
        await state.init_db()
        if not await state.reject_outbox_item(item_id):
            return _refuse("That email is already sending or sent, so it can't be rejected.")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/lessons")
async def lessons_list():
    """Lessons Harvey proposed from corrections, awaiting a decision."""
    try:
        state = _state()
        await state.init_db()
        return {"lessons": await state.get_lessons("proposed")}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/lessons/{lesson_id}/{decision}")
async def lessons_decide(lesson_id: str, decision: str):
    if decision not in ("approve", "reject"):
        return _refuse("Unknown decision.")
    try:
        state = _state()
        await state.init_db()
        if not await state.set_lesson_status(lesson_id, decision + "d"):
            return _refuse("No such lesson.")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/sending/{action}")
async def sending_toggle(action: str):
    if action not in ("pause", "resume"):
        return JSONResponse({"success": False, "message": "unknown action"}, status_code=400)
    try:
        state = _state()
        await state.init_db()
        if action == "pause":
            await state.set_setting("sending_paused", "paused from dashboard")
        else:
            await state.set_setting("sending_paused", "")
            await state.set_setting("bounce_count", "0")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.get("/api/export/prospects.csv")
async def export_prospects(all: bool = False, min_score: int = 0, email_status: str = ""):
    """Sequencer-ready CSV download of the prospect list."""
    from harvey.state import StateManager
    from harvey.export import export_prospects_csv

    state = StateManager(db_path=str(DB_PATH))
    try:
        await state.init_db()
        statuses = [s.strip() for s in email_status.split(",") if s.strip()] or None
        _, text = await export_prospects_csv(
            state, email_statuses=statuses, min_score=min_score, include_all=all,
        )
    except Exception as e:
        logger.warning("Prospect export failed: %s", e)
        text = ""
    return PlainTextResponse(
        text,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="prospects.csv"'},
    )


@app.get("/api/campaigns")
async def get_campaigns():
    rows = await query_db("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100")
    harvey_sends = _harvey_can_send()
    send_as = "" if harvey_sends else _send_as()
    footer = _compliance_view()["footer"]
    committed = await _harvey_committed_ids()
    hand_sent = await _hand_sent_ids()
    for row in rows:
        try:
            row["sequence"] = json.loads(row.get("sequence_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["sequence"] = []
        try:
            row["prospect_ids"] = json.loads(row.get("prospect_ids_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["prospect_ids"] = []
        # Who each draft goes to, so the UI can open it in the user's own mail client.
        ids = row["prospect_ids"]
        whatsapp = row.get("channel") == "whatsapp"
        row["recipients"] = await query_db(
            f"SELECT id, email, phone, first_name, last_name, company, title, status, email_status, "
            f"source, personalization_notes FROM prospects "
            f"WHERE id IN ({','.join('?' * len(ids))}) AND {'phone' if whatsapp else 'email'} != ''",
            tuple(ids),
        ) if ids else []
        progress = {r["prospect_id"]: r for r in await query_db(
            "SELECT prospect_id, SUM(status = 'sent') AS sent, COUNT(*) AS total, "
            "MIN(CASE WHEN status IN ('pending_review', 'approved') THEN send_at END) AS next_at "
            "FROM outbox WHERE campaign_id = ? AND kind = 'sequence' AND status != 'cancelled' "
            "GROUP BY prospect_id", (row["id"],))}
        for r in row["recipients"]:
            p = progress.get(r["id"]) or {}
            r["sent"], r["next_at"] = p.get("sent") or 0, p.get("next_at")
        row["send_as"] = send_as
        # A cold email sent by hand needs the same footer Harvey adds.
        row["footer"] = footer
        for r in row["recipients"]:
            r["sent_by_hand"] = r["id"] in hand_sent
            # "I already emailed them": always recordable before Harvey commits.
            # WhatsApp is only ever sent by Harvey: no "sent by hand" for it.
            r["can_mark_sent"] = (
                not whatsapp and r["id"] not in hand_sent and r["id"] not in committed
                and (r.get("status") or "new") in HAND_SENDABLE_STATUSES
            )
            # The Gmail button: only when Harvey can't send itself.
            r["can_send_by_hand"] = (
                r["can_mark_sent"] and not harvey_sends
                and (r.get("email_status") or "guess") in HAND_SENDABLE_EMAIL
            )
    return rows


@app.get("/api/conversations")
async def get_conversations():
    rows = await query_db("""
        SELECT c.*, p.first_name, p.last_name, p.email as prospect_email, p.company
        FROM conversations c
        LEFT JOIN prospects p ON c.prospect_id = p.id
        ORDER BY c.updated_at DESC LIMIT 100
    """)
    # The stored thread holds replies and Harvey's answers; the cold emails
    # a reply answers live in the Outbox. Attach those (sent ones only) and
    # any answer still waiting for approval, so the page shows the exchange.
    pids = sorted({r["prospect_id"] for r in rows if r.get("prospect_id")})
    emails: dict[str, list] = {}
    if pids:
        for e in await query_db(
                "SELECT prospect_id, campaign_id, step, subject, body, sent_at FROM outbox "
                f"WHERE kind = 'sequence' AND status = 'sent' AND prospect_id IN ({_in(pids)}) "
                "ORDER BY sent_at", tuple(pids)):
            emails.setdefault(e.pop("prospect_id"), []).append(e)
    cids = [r["id"] for r in rows]
    drafts = {d.pop("conversation_id"): d for d in (await query_db(
        "SELECT id, conversation_id, subject, body, send_at FROM outbox "
        f"WHERE kind = 'reply' AND status = 'pending_review' AND conversation_id IN ({_in(cids)}) "
        "ORDER BY created_at", tuple(cids)) if cids else [])}
    for row in rows:
        try:
            row["thread"] = json.loads(row.get("thread_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            row["thread"] = []
        # A prospect in two campaigns: show the sequence this reply answers.
        camp = row.get("campaign_id") or ""
        row["emails"] = [{k: v for k, v in e.items() if k != "campaign_id"}
                         for e in emails.get(row.get("prospect_id"), [])
                         if not camp or e.get("campaign_id") == camp]
        row["draft"] = drafts.get(row["id"])
    return rows


@app.post("/api/conversations/{convo_id}/send")
async def conversation_send(convo_id: str, request: Request):
    """The team answers a WhatsApp or Instagram chat from the dashboard. Sent
    at once, as a person: no model, no outbox. Never to someone who opted out."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = str(data.get("text") or "").strip() if isinstance(data, dict) else ""
    if not text or len(text) > 1500:
        return _refuse("Write a message (up to 1500 characters).")
    row, err = await _chat_of(convo_id)
    if err:
        return err
    if row["pstatus"] == "opted_out":
        return _refuse("This person opted out. Nothing can be sent to them.")
    from harvey.models.conversation import Message
    client, err = _chat_client(row["channel"])
    if err:
        return err
    app_name = _APP_NAMES[row["channel"]]
    # Instagram refuses a DM outside its 24h window itself; the error says so.
    result = await client.send_text(row["to"], text)
    if not result.ok:
        return JSONResponse({"success": False, "message": f"{app_name} did not send it: {result.error}"},
                            status_code=502)
    state = _state()
    convo = await state.get_conversation(convo_id)
    convo.thread.append(Message(sender="team", content=text))
    await state.update_conversation(convo_id, thread_json=convo.thread_json(), status="needs_human",
                                    follow_up_at="")
    await _team_took_over(state, convo.prospect_id)
    await state.log_action(f"{row['channel']}_sent", "dashboard", {
        "to": row["to"], "kind": "team", "message_id": result.message_id})
    return {"success": True}


async def _chat_ideas(convo_id: str, ask: str, task: str) -> list[str]:
    """Up to three messages the model proposes for this WhatsApp chat, from
    the conversation, the knowledge base and the example sites. `ask` says
    what to propose and the JSON shape ({"suggestions": [...]})."""
    from harvey.brain import Brain
    from harvey.config import load_config
    from harvey.decide import find_demo
    from harvey.gate import chat_brand
    config = load_config()
    wa = config.channels.whatsapp
    state = _state()
    convo = await state.get_conversation(convo_id)
    prospect = await state.get_prospect(convo.prospect_id) if convo.prospect_id else None
    who = {"harvey": "Theo", "team": "Equipe"}
    history = "\n".join(f"{who.get(m.sender, 'Cliente')}: {m.content}" for m in convo.thread[-12:])
    industry = getattr(prospect, "industry", "") or ""
    demo = find_demo(industry, wa.demos)
    demos = (f"{demo.label}: {demo.url}" if demo else
             "; ".join(f"{d.label}: {d.url}" for d in wa.demos) or "none")
    brain = Brain(state, config.brain)
    persona = config.persona
    raw = await brain.think_json(
        f"""You help the team of {chat_brand(persona.company)} answer a small business on {_APP_NAMES.get(convo.channel, "WhatsApp")}. Tone: {persona.tone}. Write in {wa.language}.

## The lead
Business: {getattr(prospect, "company", "") or "unknown"}
Kind of business: {industry or "unknown"}
Example sites (the only links you may use): {demos}

## Conversation (latest last)
{history}

## KNOWLEDGE BASE (the only source for facts)
{brain.load_skill("knowledge_base")}

## How to chat
{brain.load_skill("whatsapp_outreach")}

{ask}
Short, like a person on {_APP_NAMES.get(convo.channel, "WhatsApp")}, no greeting if the chat is already going.
Never invent prices or facts not in the knowledge base.""",
        agent="dashboard", task=task, **({"model": wa.model} if wa.model else {}))
    items = raw.get("suggestions") if isinstance(raw, dict) else None
    return [s.strip() for s in items or [] if isinstance(s, str) and 0 < len(s.strip()) <= 1500][:3]


@app.post("/api/conversations/{convo_id}/suggest")
async def conversation_suggest(convo_id: str, request: Request):
    """A few possible next messages for the team to pick from, or, given the
    team's own draft, a better version of it. Nothing is sent: the result
    only fills the reply box for a person to edit."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    draft = str(data.get("draft") or "").strip()[:1500] if isinstance(data, dict) else ""
    row, err = await _chat_of(convo_id)
    if err:
        return err
    if draft:
        ask = ("The team wrote this draft to send next:\n" + draft + "\n\nRewrite it to fit the conversation "
               "better and move the sale forward, keeping what it means to say. "
               'JSON: {"suggestions": ["the improved message"]}')
    else:
        ask = ("Suggest 3 different next messages the team could send now, each moving the sale one step "
               "forward (e.g. ask what they thought, propose a short call, answer what they asked). "
               'JSON: {"suggestions": ["...", "...", "..."]}')
    suggestions = await _chat_ideas(convo_id, ask, "suggest_replies")
    if not suggestions:
        return JSONResponse({"success": False, "message": "Could not get suggestions right now."},
                            status_code=502)
    return {"success": True, "suggestions": suggestions}


_APP_NAMES = {"whatsapp": "WhatsApp", "instagram": "Instagram"}


async def _chat_of(convo_id: str, apps: tuple[str, ...] = ("whatsapp", "instagram")):
    """(row, None) for a chat conversation on one of `apps`, or (None, an
    error response). row: channel, to (phone or Instagram id), phone, pstatus."""
    rows = await query_db(
        "SELECT c.channel, p.phone, p.instagram_id, p.status AS pstatus FROM conversations c "
        "LEFT JOIN prospects p ON p.id = c.prospect_id WHERE c.id = ?", (convo_id,))
    if not rows:
        return None, JSONResponse({"success": False, "message": "No such conversation."}, status_code=404)
    row = dict(rows[0])
    row["to"] = row.get("instagram_id") if row["channel"] == "instagram" else row.get("phone")
    if row["channel"] not in apps or not row["to"]:
        return None, _refuse(f"Only {' or '.join(_APP_NAMES[a] for a in apps)} chats work here.")
    return row, None


def _chat_client(channel: str):
    """(client, None) for the app this chat is on, or (None, an error response)."""
    from harvey.config import load_env
    if channel == "instagram":
        from harvey.integrations.instagram import InstagramClient
        client, what = InstagramClient.from_env(load_env(), db_file=DB_PATH), "Instagram"
    else:
        from harvey.integrations.evolution import EvolutionClient
        client, what = EvolutionClient.from_env(load_env()), "WhatsApp (Evolution)"
    if client.is_configured():
        return client, None
    return None, JSONResponse({"success": False, "message": f"{what} is not configured."}, status_code=503)


_TIMELINE_ACTIONS = ("decision", "handoff", "escalation", "inbound_lead", "opt_out", "human_turn",
                     "outbox_edited", "stage_whatsapp")


async def _lead_detail(prospect_id: str) -> dict | None:
    """The lead, its company, the signals Harvey found, and every contact so
    far (messages sent, decisions, handoffs), newest first. None if unknown."""
    rows = await query_db(
        "SELECT id, first_name, last_name, email, phone, title, status AS pstatus, source, "
        "score, industry, company, linkedin_url, company_id, personalization_notes, "
        "created_at AS pcreated FROM prospects WHERE id = ?", (prospect_id,))
    if not rows:
        return None
    r = rows[0]
    state = _state()
    company, signals = None, []
    if r.get("company_id"):
        comp = await state.get_company(r["company_id"])
        if comp:
            company = {k: getattr(comp, k) for k in (
                "name", "domain", "website", "industry", "location", "phone", "description", "source", "source_url")}
            labels = {c["code"]: c.get("label") or c["code"] for c in await state.get_signal_codes()}
            signals = [labels.get(code, code) for code in await state.company_signal_codes(comp.id)]
    keys = [k for k in (r.get("phone"), r.get("email")) if k]
    events = []
    for o in await query_db(
            "SELECT created_at, sent_at, kind, step, status, channel, body FROM outbox "
            "WHERE prospect_id = ? ORDER BY created_at", (prospect_id,)):
        what = ("Opening message" if o["step"] == 1 else f"Follow-up {o['step'] - 1}")             if o["kind"] == "sequence" else "Reply"
        events.append({"ts": o["sent_at"] or o["created_at"], "type": "message",
                       "text": f"{what} ({o['channel'] or 'email'}) · {o['status']}: " + (o["body"] or "")[:140]})
    for key in keys:
        for a in await query_db(
                f"SELECT created_at, action_type, details_json FROM actions WHERE action_type IN ({_in(_TIMELINE_ACTIONS)}) "
                "AND details_json LIKE ? ORDER BY created_at", (*_TIMELINE_ACTIONS, f"%{key}%")):
            try:
                d = json.loads(a["details_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                d = {}
            detail = d.get("reason") or d.get("overridden") or d.get("intent") or d.get("reply_preview") or ""
            extra = f" ({d['action']})" if a["action_type"] == "decision" and d.get("action") else ""
            events.append({"ts": a["created_at"], "type": a["action_type"],
                           "text": a["action_type"].replace("_", " ").capitalize() + extra + (f": {detail}"[:160] if detail else "")})
    return {
        "lead": {k: r.get(k) for k in ("first_name", "last_name", "phone", "email", "title", "pstatus",
                                        "source", "score", "industry", "company", "linkedin_url", "pcreated",
                                        "personalization_notes")},
        "company": company, "signals": signals, "history": events,
    }


def _newest_first(events: list[dict]) -> list[dict]:
    events = list({(e["ts"], e["type"], e["text"]): e for e in events}.values())
    events.sort(key=lambda e: str(e["ts"] or ""), reverse=True)
    return events[:80]


@app.get("/api/conversations/{convo_id}/lead")
async def conversation_lead(convo_id: str):
    """Who this conversation is with, and every contact so far."""
    rows = await query_db(
        "SELECT prospect_id, channel, stage, intent, status, created_at FROM conversations WHERE id = ?",
        (convo_id,))
    if not rows:
        return JSONResponse({"success": False, "message": "No such conversation."}, status_code=404)
    c = rows[0]
    detail = await _lead_detail(c["prospect_id"]) or {
        "lead": {}, "company": None, "signals": [], "history": []}
    detail["history"] = _newest_first(detail["history"] + [
        {"ts": c["created_at"], "type": "conversation", "text": f"Conversation started ({c['channel']})"}])
    detail["conversation"] = {k: c.get(k) for k in ("stage", "intent", "status", "created_at")}
    return detail


@app.get("/api/campaigns/{campaign_id}/prospects/{prospect_id}")
async def campaign_prospect(campaign_id: str, prospect_id: str):
    """One person in a campaign: who they are, every message both ways
    (the campaign's steps, sent or scheduled, their replies and Harvey's
    answers), and the timeline of everything that happened."""
    detail = await _lead_detail(prospect_id)
    if detail is None:
        return JSONResponse({"success": False, "message": "No such prospect."}, status_code=404)
    messages = []
    for o in await query_db(
            "SELECT id, kind, step, status, subject, body, send_at, sent_at, error, created_at FROM outbox "
            "WHERE prospect_id = ? AND (campaign_id = ? OR kind = 'reply') AND status != 'cancelled' "
            "ORDER BY created_at", (prospect_id, campaign_id)):
        messages.append({"who": "harvey", "ts": o["sent_at"] or o["send_at"] or o["created_at"], **o})
    convos = await query_db(
        "SELECT thread_json, created_at FROM conversations WHERE prospect_id = ?", (prospect_id,))
    for c in convos:
        try:
            thread = json.loads(c.get("thread_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            thread = []
        for m in thread if isinstance(thread, list) else []:
            if not isinstance(m, dict) or m.get("sender") == "harvey":
                continue  # Harvey's answers are already here from the Outbox
            messages.append({"who": "lead" if m.get("sender") == "prospect" else "team",
                             "ts": m.get("timestamp"), "body": m.get("content") or "", "status": "received"})
        detail["history"].append({"ts": c["created_at"], "type": "conversation", "text": "Replied: conversation started"})
    messages.sort(key=lambda m: str(m.get("ts") or ""))
    detail["history"] = _newest_first(detail["history"])
    detail["messages"] = messages
    return detail


@app.get("/api/conversations/{convo_id}/chat")
async def conversation_chat(convo_id: str, limit: int = 30, before: int = 0):
    """The real chat on WhatsApp or Instagram (both directions; WhatsApp media
    included), newest `limit` messages older than `before` (epoch seconds), oldest first."""
    row, err = await _chat_of(convo_id)
    if err:
        return err
    client, err = _chat_client(row["channel"])
    if err:
        return err
    try:
        messages, more = await client.get_chat(row["to"], limit=max(1, min(limit, 60)), before=max(before, 0))
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Could not read the chat: {e}"}, status_code=502)
    kinds = await _state().get_whatsapp_sent_kinds()
    for m in messages:
        m["who"] = ("lead" if not m["from_me"]
                    else "team" if kinds.get(m["id"]) == "team"
                    else "harvey" if m["id"] in kinds else "team_phone")
    return {"messages": messages, "has_more": more}


@app.get("/api/conversations/{convo_id}/media/{message_id}")
async def conversation_media(convo_id: str, message_id: str):
    """One attachment of this chat (never one from another chat)."""
    from fastapi.responses import Response
    row, err = await _chat_of(convo_id, apps=("whatsapp",))
    if err:
        return err
    client, err = _chat_client("whatsapp")
    if err:
        return err
    try:
        recent, _ = await client.get_chat(row["phone"], limit=60)
        known = {m["id"] for m in recent}
        if message_id not in known:
            older, _ = await client.get_chat(row["phone"], limit=200, before=min(m["ts"] for m in recent) if recent else 0)
            known |= {m["id"] for m in older}
        if message_id not in known:
            return JSONResponse({"success": False, "message": "That file is not in this chat."}, status_code=404)
        data, mimetype, filename = await client.get_media(message_id)
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Could not load the file: {e}"}, status_code=502)
    return Response(content=data, media_type=mimetype or "application/octet-stream",
                    headers={"Cache-Control": "private, max-age=3600",
                             "Content-Disposition": 'inline; filename="%s"' % (filename or "file").replace('"', "")})


REACTIONS = ("👍", "❤️", "😂", "😮", "😢", "🙏")   # WhatsApp's quick row; the UI offers the same


async def _chat_message(client, phone: str, message_id: str) -> dict | None:
    """The message `message_id` if it is in this chat (recent or older), else None."""
    recent, _ = await client.get_chat(phone, limit=60)
    found = next((m for m in recent if m["id"] == message_id), None)
    if found is None and recent:
        older, _ = await client.get_chat(phone, limit=200, before=min(m["ts"] for m in recent))
        found = next((m for m in older if m["id"] == message_id), None)
    return found


@app.post("/api/conversations/{convo_id}/react")
async def conversation_react(convo_id: str, request: Request):
    """React to one message of this chat on WhatsApp: {message_id, emoji};
    an empty emoji removes the reaction."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data if isinstance(data, dict) else {}
    message_id, emoji = str(data.get("message_id") or ""), str(data.get("emoji") or "")
    if not message_id or (emoji and emoji not in REACTIONS):
        return _refuse("Pick one of the reactions offered.", 400)
    row, err = await _chat_of(convo_id, apps=("whatsapp",))
    if err:
        return err
    if row["pstatus"] == "opted_out":
        return _refuse("This person opted out. Nothing can be sent to them.")
    client, err = _chat_client("whatsapp")
    if err:
        return err
    try:
        target = await _chat_message(client, row["phone"], message_id)
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Could not read the chat: {e}"}, status_code=502)
    if target is None:
        return JSONResponse({"success": False, "message": "That message is not in this chat."}, status_code=404)
    result = await client.send_reaction(target.get("jid") or row["phone"] + "@s.whatsapp.net",
                                        target["from_me"], message_id, emoji)
    if not result.ok:
        return JSONResponse({"success": False, "message": f"WhatsApp did not send it: {result.error}"},
                            status_code=502)
    await _state().log_action("whatsapp_reaction", "dashboard",
                              {"to": row["phone"], "message_id": message_id, "emoji": emoji})
    return {"success": True}


MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024


@app.post("/api/conversations/{convo_id}/attach")
async def conversation_attach(convo_id: str, request: Request):
    """The team sends a file on WhatsApp: {filename, mimetype, data (base64), caption}."""
    import base64
    try:
        data = await request.json()
    except Exception:
        data = {}
    data = data if isinstance(data, dict) else {}
    filename = str(data.get("filename") or "file")[:120]
    mimetype = str(data.get("mimetype") or "application/octet-stream")[:100]
    caption = str(data.get("caption") or "").strip()[:1000]
    b64 = str(data.get("data") or "").split(",")[-1]
    if not b64 or len(b64) * 3 // 4 > MAX_ATTACHMENT_BYTES:
        return _refuse("Pick a file up to 16 MB.", 400)
    try:
        base64.b64decode(b64, validate=True)
    except Exception:
        return _refuse("That file could not be read.", 400)
    row, err = await _chat_of(convo_id, apps=("whatsapp",))
    if err:
        return err
    if row["pstatus"] == "opted_out":
        return _refuse("This person opted out. Nothing can be sent to them.")
    client, err = _chat_client("whatsapp")
    if err:
        return err
    result = await client.send_media(row["phone"], filename, mimetype, b64, caption)
    if not result.ok:
        return JSONResponse({"success": False, "message": f"WhatsApp did not send it: {result.error}"},
                            status_code=502)
    state = _state()
    await state.update_conversation(convo_id, status="needs_human", follow_up_at="")
    convo = await state.get_conversation(convo_id)
    await _team_took_over(state, convo.prospect_id if convo else "")
    await state.log_action("whatsapp_sent", "dashboard", {
        "to": row["phone"], "kind": "team", "message_id": result.message_id, "file": filename})
    return {"success": True}


@app.post("/api/conversations/{convo_id}/status")
async def set_conversation_status(convo_id: str, request: Request):
    """Close a conversation, or hand one Harvey escalated back to it.

    Harvey answers new replies only on open conversations, so "open" means
    "Harvey, carry on" and "closed" means it's done."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    status = data.get("status") if isinstance(data, dict) else None
    if status not in ("open", "closed"):
        return JSONResponse({"success": False, "message": "status must be open or closed"},
                            status_code=400)
    rows = await query_db("SELECT status FROM conversations WHERE id = ?", (convo_id,))
    if not rows:
        return JSONResponse({"success": False, "message": "No such conversation."},
                            status_code=404)
    state = _state()
    await state.update_conversation(convo_id, status=status)
    await state.log_action("conversation_status", "dashboard",
                           {"conversation_id": convo_id, "from": rows[0]["status"], "to": status})
    result = {}
    if status == "closed":
        convo = await state.get_conversation(convo_id)
        await state.update_conversation(convo_id, follow_up_at="")
        if convo and convo.prospect_id:
            await state.cancel_pending_outbox_for_prospect(convo.prospect_id, reason="conversation closed")
    elif rows[0]["status"] == "needs_human":
        result = await _reassess(state, convo_id)
    return {"success": True, "status": status, "follow_up_at": result.get("follow_up_at"), **result}


@app.post("/api/conversations/{convo_id}/follow-up/preview")
async def preview_conversation_follow_up(convo_id: str):
    """What the planned follow-up would say if it went out now. Nothing is
    stored: the real one is written from the chat when it comes due."""
    from harvey.agents.handler import Handler
    from harvey.brain import Brain
    from harvey.config import load_config, load_env
    state = _state()
    convo = await state.get_conversation(convo_id)
    if not convo or not convo.follow_up_at:
        return _refuse("No follow-up is planned for this conversation.")
    prospect = await state.get_prospect(convo.prospect_id) if convo.prospect_id else None
    if not prospect or not prospect.phone:
        return _refuse("This conversation has no WhatsApp number.")
    config = load_config()
    try:
        text = await Handler(Brain(state, config.brain), state, config, load_env())._write_follow_up(prospect, convo)
    except Exception as e:
        logger.warning(f"Follow-up preview failed: {e}")
        text = ""
    if not text:
        return _refuse("Roadie Crew couldn't write a preview right now. Try again in a moment.", 502)
    return {"success": True, "text": text}


@app.post("/api/conversations/{convo_id}/follow-up")
async def set_conversation_follow_up(convo_id: str, request: Request):
    """The follow-up Harvey planned: {"action": "cancel"} drops it, "now"
    makes it due, so the next cycle in business hours writes and sends it."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    action = data.get("action") if isinstance(data, dict) else None
    if action not in ("cancel", "now"):
        return _refuse("action must be cancel or now", 400)
    rows = await query_db("SELECT follow_up_at FROM conversations WHERE id = ?", (convo_id,))
    if not rows:
        return _refuse("No such conversation.", 404)
    if not rows[0]["follow_up_at"]:
        return _refuse("No follow-up is planned for this conversation.")
    due = "" if action == "cancel" else datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    state = _state()
    await state.update_conversation(convo_id, follow_up_at=due)
    await state.log_action("follow_up_" + action, "dashboard", {"conversation_id": convo_id})
    return {"success": True, "follow_up_at": due}


async def _team_took_over(state, prospect_id: str):
    """The team just wrote in this chat: whatever Harvey still had queued for
    the person was written before that and would talk over them."""
    if prospect_id:
        await state.cancel_pending_outbox_for_prospect(prospect_id, reason="team answered")


async def _reassess(state, convo_id: str) -> dict:
    """Handed back to Harvey: it reads the chat and answers what was left
    unanswered, plans a follow-up for a lead who will get back (or after the
    team's last word), waits, or hands it back (agents/handler.py)."""
    from harvey.agents.handler import Handler
    from harvey.brain import Brain
    from harvey.config import load_config, load_env
    convo = await state.get_conversation(convo_id)
    if not convo:
        return {}
    config = load_config()
    try:
        return await Handler(Brain(state, config.brain), state, config, load_env()).reassess_whatsapp(convo)
    except Exception as e:     # the hand-back itself stands; the next reply is answered as usual
        logger.warning(f"Hand-back reassessment failed: {e}")
        return {"next": "wait"}


@app.get("/api/trend")
async def get_trend(days: int = 14, tz: int = 0):
    """Daily counts for the last `days` days in the viewer's time zone
    (`tz`: minutes east of UTC; stored times are UTC): contacts found,
    emails sent, first replies. Days with nothing are 0."""
    from datetime import datetime, timedelta, timezone
    days = max(1, min(90, days))
    tz = max(-840, min(840, tz))
    shift = f"{tz:+d} minutes"
    today = (datetime.now(timezone.utc) + timedelta(minutes=tz)).date()
    labels = [(today - timedelta(days=n)).isoformat() for n in range(days - 1, -1, -1)]

    async def per_day(table: str, column: str, where: str = "") -> list[int]:
        rows = await query_db(
            f"SELECT date({column}, ?) AS d, COUNT(*) AS n FROM {table} "
            f"WHERE date({column}, ?) >= ? {where} GROUP BY d", (shift, shift, labels[0]))
        counts = {r["d"]: r["n"] for r in rows}
        return [counts.get(day, 0) for day in labels]

    return {
        "days": labels,
        "found": await per_day("prospects", "created_at"),
        "sent": await per_day("outbox", "sent_at", "AND status = 'sent'"),
        "sent_whatsapp": await per_day("outbox", "sent_at", "AND status = 'sent' AND channel = 'whatsapp'"),
        "replies": await per_day("conversations", "created_at"),
    }


@app.get("/api/activity")
async def get_activity():
    rows = await query_db("SELECT * FROM actions ORDER BY created_at DESC LIMIT 100")
    for row in rows:
        try:
            row["details"] = json.loads(row.get("details_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["details"] = {}
    return rows


# ── Dashboard UI ──


# ── Discovery: the provider menu, an estimate, and a run ──
#
# This is the only stage that spends money, so the UI never starts one without
# showing what it will cost first.

_discovery_task: asyncio.Task | None = None
_discovery_report: dict | None = None
_discovery_kind = "discover"   # or "profile": reading sites has no stop switch


def _discovery_queries(body: dict, config):
    from harvey.collectors.discover import build_queries

    cities = [c.strip() for c in (body.get("cities") or []) if c.strip()] or None
    return build_queries(
        config, cities=cities,
        depth=int(body.get("depth") or 30),
        limit=int(body.get("limit") or 100),
    )


@app.get("/api/discover/providers")
async def get_discovery_providers():
    """What each source does, what it costs, and whether it's ready to use."""
    try:
        from harvey.collectors.discover import DEFAULT_PROVIDER, provider_menu
        from harvey.config import load_env

        state = _state()
        await state.init_db()
        return {
            "providers": provider_menu(load_env().model_dump()),
            "default": DEFAULT_PROVIDER,
            "selected": await state.get_setting("discovery_provider") or DEFAULT_PROVIDER,
            "paused": await state.get_setting("discovery_paused"),
            "running": bool(_discovery_task and not _discovery_task.done()),
            "running_kind": _discovery_kind,
            "unprofiled": await state.count_companies_needing_profile(),
            "last_report": _discovery_report,
        }
    except Exception as e:
        logger.exception("discovery providers failed")
        return {"providers": [], "error": str(e)}


@app.post("/api/discover/estimate")
async def estimate_discovery(request: Request):
    """Projected spend and the exact query list, before anything is called."""
    try:
        from harvey.collectors.discover import PROVIDERS, estimate_cost
        from harvey.config import load_config

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"error": f"unknown provider {provider!r}"},
                                status_code=400)

        queries = _discovery_queries(body, load_config())
        return {
            "provider": provider,
            "queries": [q.keyword() for q in queries],
            "query_count": len(queries),
            "estimated_cost": round(estimate_cost(provider, queries), 4),
            "free": PROVIDERS[provider].estimate(queries) == 0,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/discover/run")
async def start_discovery(request: Request):
    """Kick off a run in the background and hand back immediately.

    Discovery takes minutes, not milliseconds — holding the request open
    would just time out. Progress shows up in the run log.
    """
    global _discovery_task, _discovery_report

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.collectors.discover import PROVIDERS
        from harvey.config import load_config
        from harvey.pipeline import run_prospecting

        body = await request.json()
        provider = body.get("provider") or ""
        if provider not in PROVIDERS:
            return JSONResponse({"success": False,
                                 "message": f"unknown provider {provider!r}"},
                                status_code=400)

        config = load_config()
        queries = _discovery_queries(body, config)
        max_spend = float(body.get("max_spend") or 1.0)

        state = _state()
        await state.init_db()
        await state.set_setting("discovery_provider", provider)
        await state.set_setting("discovery_paused", "")

        global _discovery_kind
        _discovery_kind = "discover"

        async def _go():
            global _discovery_report
            try:
                # Discovery chains straight into profiling: reading the sites
                # is free, and it is what makes the results worth anything.
                result = await run_prospecting(state, config, provider, queries,
                                               max_spend=max_spend)
                _discovery_report = {
                    **(result.discover or {}),
                    "profiled_companies": result.profiled_companies,
                    "profile_observations": result.profile_observations,
                    "errors": result.errors,
                }
            except Exception as exc:
                logger.exception("discovery run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        _discovery_report = None
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "queries": len(queries)}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/profile/run")
async def start_profile():
    """Read the websites of everything discovered but not yet looked at.

    Free and model-free, so there is nothing to estimate and no cap to set.
    """
    global _discovery_task, _discovery_report

    if _discovery_task and not _discovery_task.done():
        return JSONResponse({"success": False, "message": "a run is already going"},
                            status_code=409)
    try:
        from harvey.pipeline import configured_triggers, run_profile_stage

        state = _state()
        await state.init_db()
        pending = await state.count_companies_needing_profile()
        if not pending:
            return {"success": True, "pending": 0}

        async def _go():
            global _discovery_report
            try:
                companies, observations, _ = await run_profile_stage(
                    state, limit=200, triggers=configured_triggers())

                _discovery_report = {
                    "profiled_companies": companies,
                    "profile_observations": observations,
                }
            except Exception as exc:
                logger.exception("profile run failed")
                _discovery_report = {"errors": [str(exc)], "stopped": "failed"}

        global _discovery_kind
        _discovery_kind = "profile"
        _discovery_report = None
        _discovery_task = asyncio.create_task(_go())
        return {"success": True, "pending": pending}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@app.post("/api/discover/stop")
async def stop_discovery():
    """Kill switch. Read between batches, so an in-flight run stops cleanly."""
    try:
        state = _state()
        await state.init_db()
        await state.set_setting("discovery_paused", "stopped from dashboard")
        return {"success": True}
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


def _parse_ts(value) -> datetime | None:
    """SQLite CURRENT_TIMESTAMP / isoformat strings -> naive UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "").split("+")[0])
    except ValueError:
        return None


async def _scalar(sql: str, params: tuple = ()) -> int:
    rows = await query_db(sql, params)
    return int(next(iter(rows[0].values())) or 0) if rows else 0


async def _first(sql: str, params: tuple = (), key: str = "t"):
    """First row's `key`, or None — query_db returns [] on any DB problem."""
    rows = await query_db(sql, params)
    return rows[0].get(key) if rows else None


# A run row still says 'running' after its process was killed (closed window,
# reboot). No new facts for this long means it is not coming back.
STALE_RUN_SECONDS = 900
# Contact-finding has no run row; a recent prospects_checked_at means it is active.
CONTACT_ACTIVE_SECONDS = 180
# Moved on from "waiting to be emailed" (opted_out too: never offered again).
SENT_STATUSES = ("contacted", "replied", "meeting", "closed", "lost", "opted_out")
REPLY_STATUSES = ("replied", "meeting", "closed")


def _eta_text(minutes) -> str:
    if minutes is None:
        return ""
    return "less than a minute left" if minutes < 1 else f"about {round(minutes)} min left"


@app.get("/api/live-status")
async def get_live_status():
    """Plain-language progress: each pipeline step, what is running, how long is left.

    Built for someone who would otherwise keep asking "what's the status?".
    Everything is derived from the database, so it is true whatever started
    the work — `harvey run`, a CLI command, or a one-off script. Every total
    counts only what the step can act on (a business with no website is never
    read), so a finished step always reaches 100% instead of sticking.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC
    has_site = "COALESCE(domain, '') != ''"
    companies = await _scalar("SELECT COUNT(*) FROM companies")
    with_site = await _scalar(f"SELECT COUNT(*) FROM companies WHERE {has_site}")
    no_site = companies - with_site
    profiled = await _scalar(
        f"SELECT COUNT(*) FROM companies WHERE {has_site} AND profile_checked_at IS NOT NULL")
    checked = await _scalar(
        f"SELECT COUNT(*) FROM companies WHERE {has_site} "
        "AND profile_checked_at IS NOT NULL AND prospects_checked_at IS NOT NULL")

    prospects = await query_db("SELECT id, email, email_status, status FROM prospects")
    with_email = [p for p in prospects if p.get("email")]
    by_status: dict[str, int] = {}
    for p in with_email:
        k = (p.get("email_status") or "guess").lower()
        by_status[k] = by_status.get(k, 0) + 1
    deliverable = {p["id"] for p in with_email
                   if (p.get("email_status") or "") in ("verified", "risky")}
    unconfirmed = sum(n for k, n in by_status.items()
                      if k not in ("verified", "risky", "invalid"))

    # Drafts are counted per person, not per campaign: one campaign can cover many.
    drafted: set[str] = set()
    for row in await query_db("SELECT prospect_ids_json FROM campaigns"):
        try:
            ids = json.loads(row.get("prospect_ids_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            ids = []
        if isinstance(ids, list):
            drafted.update(i for i in ids if isinstance(i, str))
    sent = {p["id"] for p in prospects if p.get("status") in SENT_STATUSES}
    # Who is left for the user to send by hand: only when Harvey can't send
    # itself, and never someone Harvey has queued, sent to, or can't reach.
    harvey_sends = _harvey_can_send()
    awaiting_review = await _scalar("SELECT COUNT(*) FROM outbox WHERE status = 'pending_review'")
    # A WhatsApp-only operation has no emails to find, check or write: its funnel
    # is the WhatsApp outbox, so the email steps would only read 0 forever.
    wa = {r["status"]: r["n"] for r in await query_db(
        "SELECT status, COUNT(*) AS n FROM outbox WHERE channel = 'whatsapp' GROUP BY status")}
    wa_total = sum(n for k, n in wa.items() if k != "cancelled")
    wa_only = bool(wa_total) and not with_email and not drafted
    by_hand: set[str] = set()
    if not harvey_sends:
        skip = await _harvey_committed_ids() | await _hand_sent_ids()
        by_hand = {p["id"] for p in prospects
                   if p["id"] in drafted and p["id"] not in skip
                   and (p.get("status") or "new") in HAND_SENDABLE_STATUSES
                   and (p.get("email_status") or "guess") in HAND_SENDABLE_EMAIL}
    replies = sum(1 for p in prospects if p.get("status") in REPLY_STATUSES)
    cost = float(await _first(
        "SELECT COALESCE(SUM(cost_usd), 0) AS c FROM runs", key="c") or 0)

    running, stopped = [], []
    for run in await query_db(
            "SELECT * FROM runs WHERE status = 'running' ORDER BY started_at"):
        started = _parse_ts(run.get("started_at"))
        elapsed = max(0.0, (now - started).total_seconds()) if started else 0.0
        last = _parse_ts(await _first(
            "SELECT MAX(observed_at) AS t FROM observations WHERE run_id = ?",
            (run["id"],))) or started
        item = {"stage": run.get("stage"), "provider": run.get("provider"),
                "elapsed_min": round(elapsed / 60, 1), "eta_min": None}
        if last and (now - last).total_seconds() > STALE_RUN_SECONDS:
            stopped.append(item)
            continue
        if run.get("stage") == "profile" and started:
            done_in_run = await _scalar(
                f"SELECT COUNT(*) FROM companies WHERE {has_site} AND profile_checked_at >= ?",
                (run["started_at"],))
            left = max(0, with_site - profiled)
            if left and done_in_run and elapsed > 0:
                item["eta_min"] = left / (done_in_run / elapsed) / 60
        running.append(item)

    last_contact = _parse_ts(await _first(
        "SELECT MAX(prospects_checked_at) AS t FROM companies"))
    finding_emails = bool(
        last_contact and (now - last_contact).total_seconds() < CONTACT_ACTIVE_SECONDS)
    reading = any(r["stage"] == "profile" for r in running)
    finding = any(r["stage"] == "discover" for r in running)

    def step(label, done, total, active, detail, info=False):
        if active:
            state_ = "running"
        elif info:
            state_ = "done" if done else "waiting"
        elif total and done >= total:
            state_ = "done"
        elif done:
            state_ = "partial"
        else:
            state_ = "waiting"
        return {"label": label, "done": done, "total": total,
                "state": state_, "detail": detail}

    profile_eta = next((r["eta_min"] for r in running if r["stage"] == "profile"), None)
    read_detail = f"{profiled} of {with_site} read"
    if reading and profile_eta is not None:
        read_detail += " · " + _eta_text(profile_eta)
    if no_site:
        read_detail += f" · {no_site} have no website (skipped)"

    email_steps = [
        step("Find emails", checked, profiled, finding_emails,
             f"{checked} of {profiled} checked · {len(with_email)} emails found"),
        step("Check emails are real", len(with_email) - unconfirmed, len(with_email), False,
             f"{by_status.get('verified', 0)} verified · {by_status.get('risky', 0)} risky · "
             f"{unconfirmed} not confirmed"
             + (f" · {by_status['invalid']} bad" if by_status.get("invalid") else "")),
        step("Write first emails", len(drafted), len(deliverable | drafted), False,
             f"{len(drafted)} written · see Campaigns"),
        step("Sent", len(sent & drafted), len(drafted), False,
             f"{len(sent & drafted)} of {len(drafted)} sent"),
    ]
    whatsapp_steps = [
        step("Write first messages", wa_total, wa_total, False,
             f"{wa_total} written · {wa.get('pending_review', 0)} waiting for your review"),
        step("Sent on WhatsApp", wa.get("sent", 0), wa_total, False,
             f"{wa.get('sent', 0)} of {wa_total} sent"),
    ]
    steps = [
        step("Find businesses", companies, 0, finding,
             f"{companies} businesses found", info=True),
        step("Read their websites", profiled, with_site, reading, read_detail),
        *(whatsapp_steps if wa_only else email_steps),
        step("Replies", replies, 0, False,
             f"{replies} replies" if replies else "No replies yet", info=True),
    ]

    next_tab = ""   # where the next step is done, when it's done in the dashboard
    if reading or finding:
        next_up = "Wait — Roadie Crew is working. This page updates by itself."
    elif finding_emails and not wa_only:
        next_up = "Wait — emails are being found."
    elif stopped:
        next_up, next_tab = "A job stopped before finishing. Run it again.", "discover"
    elif (harvey_sends or wa_only) and awaiting_review:
        next_up, next_tab = f"Review the {awaiting_review} draft(s) waiting in the Outbox.", "outbox"
    elif by_hand:
        next_up, next_tab = f"Open Campaigns and send the {len(by_hand)} unsent email(s).", "campaigns"
    elif wa_only:
        next_up, next_tab = "Search more cities or industries to get more leads.", "discover"
    elif profiled > checked:
        next_up, next_tab = f"Find emails for the {profiled - checked} businesses not checked yet.", "controls"
    elif deliverable - drafted:
        next_up, next_tab = f"Write first emails for {len(deliverable - drafted)} more contact(s).", "controls"
    elif not companies:
        next_up, next_tab = "Search for businesses to get started.", "discover"
    else:
        next_up, next_tab = "Search more cities or industries to get more leads.", "discover"

    return {
        "steps": steps,
        "running": [dict(r, eta_text=_eta_text(r["eta_min"])) for r in running],
        "stopped": stopped,
        "next": next_up,
        "next_tab": next_tab,
        "cost_usd": round(cost, 4),
        "updated_at": now.isoformat() + "Z",
    }


# ── Notifications: what needs attention, derived from the state of things ──
#
# Each row is one thing (a conversation handed to you, the approval queue, a
# system problem), not one event. They are recomputed when the bell asks: a
# new message in a handed-over chat makes its notification unread again, and a
# cause that is gone takes its notification away. Muted and archived ones stay.

_wa_link = {"at": 0.0, "up": True}


async def _whatsapp_link_up() -> bool:
    """Is WhatsApp connected? Asked at most once a minute."""
    import time
    if time.time() - _wa_link["at"] < 60:
        return _wa_link["up"]
    from harvey.config import load_config, load_env
    from harvey.integrations.evolution import EvolutionClient
    up = True
    if load_config().channels.whatsapp.enabled:
        client = EvolutionClient.from_env(load_env())
        try:
            up = bool(client.is_configured()) and (await asyncio.wait_for(client.test_connection(), 3))[0]
        except Exception:
            up = False
    _wa_link.update(at=time.time(), up=up)
    return up


async def _refresh_notifications(state) -> None:
    from harvey.notify import state_alerts
    raised = await state_alerts(state)

    async def raise_(key, title, detail="", tone="warn", tab="", target="", signature=""):
        raised.append((key, title, detail, tone, tab, target, signature))

    brain = await _brain_status()
    if brain.get("status") == "paused":
        await raise_("system:brain", "Roadie Crew's model is paused",
                     str(brain.get("reason") or "It can't be used right now.")[:200], "bad", "controls")
    if not await _whatsapp_link_up():
        await raise_("system:whatsapp", "WhatsApp is offline",
                     "Roadie Crew can't read or send chats until the Evolution instance reconnects.", "bad", "settings")
    await state.sync_notifications(raised)


@app.get("/api/notifications")
async def notifications_list(view: str = "inbox"):
    state = _state()
    await state.init_db()
    await _refresh_notifications(state)
    return {"items": await state.get_notifications(view), "unread": await state.count_unread_notifications()}


@app.post("/api/notifications/read-all")
async def notifications_read_all():
    await _state().mark_all_notifications_read()
    return {"success": True}


@app.post("/api/notifications/{nid}/{action}")
async def notifications_act(nid: str, action: str):
    status = {"read": "read", "mute": "muted", "unmute": "read", "archive": "archived", "unarchive": "read"}.get(action)
    if not status:
        return _refuse("Unknown action.", 400)
    if not await _state().set_notification_status(nid, status):
        return JSONResponse({"success": False, "message": "No such notification."}, status_code=404)
    return {"success": True}


@app.delete("/api/notifications/{nid}")
async def notifications_delete(nid: str):
    if not await _state().delete_notification(nid):
        return JSONResponse({"success": False, "message": "No such notification."}, status_code=404)
    return {"success": True}


async def _whatsapp_today(state) -> dict:
    """WhatsApp at a glance: is it on, connected, how much of today's cap is used."""
    from harvey.config import load_config, load_env
    from harvey.integrations.evolution import EvolutionClient
    cfg = load_config().channels.whatsapp
    if not cfg.enabled:
        return {"enabled": False}
    client = EvolutionClient.from_env(load_env())
    connected = None
    if client.is_configured():
        try:
            connected, _ = await asyncio.wait_for(client.test_connection(), 3)
        except Exception:
            connected = False
    return {"enabled": True, "connected": connected,
            "sent_today": await state.count_outbox_sent_today(channel="whatsapp"),
            "cap": cfg.max_daily_sends,
            "chats": len(await query_db(
                "SELECT id FROM conversations WHERE channel = 'whatsapp' AND status IN ('open', 'needs_human')"
                " AND COALESCE(intent, '') != 'ooo'"))}


@app.get("/api/today")
async def get_today():
    """What needs a human, right now.

    The dashboard opens here rather than on a setup checklist: the question a
    returning user actually has is "is anything waiting on me?", and the
    answer is usually a short list or nothing at all.
    """
    items: list[dict] = []
    stats: dict = {}
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        proposed = [c for c in codes if c.get("status") == "proposed"]
        confirmed = [c for c in codes if c.get("status") == "confirmed"]
        pending = await state.get_outbox(status="pending_review", limit=200)
        approved = await state.get_outbox(status="approved", limit=200)
        paused = await state.get_setting("sending_paused")
        counts = await state.count_prospects_by_status()

        # A bot greeting or away message is not someone talking to us.
        convos = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'open'"
            " AND COALESCE(intent, '') != 'ooo'"
        )
        open_convos = convos[0]["n"] if convos else 0
        autos = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'open' AND intent = 'ooo'"
        )
        auto_replies = autos[0]["n"] if autos else 0
        handed = await query_db(
            "SELECT COUNT(*) AS n FROM conversations WHERE status = 'needs_human'"
        )
        needs_human = handed[0]["n"] if handed else 0
        companies = await query_db("SELECT COUNT(*) AS n FROM companies")
        n_companies = companies[0]["n"] if companies else 0

        unprofiled_count = await state.count_companies_needing_profile()
        wa = await _whatsapp_today(state)
        pending_wa = sum(1 for i in pending if i.get("channel") == "whatsapp")
        approved_wa = sum(1 for i in approved if i.get("channel") == "whatsapp")
        stats = {
            "whatsapp": wa,
            "outbox_pending_whatsapp": pending_wa,
            "outbox_approved_whatsapp": approved_wa,
            "live_conversations": open_convos + needs_human,
            "companies": n_companies,
            "prospects": sum(counts.values()),
            "signals_confirmed": len(confirmed),
            "outbox_pending": len(pending),
            "outbox_approved": len(approved),
            "open_conversations": open_convos,
            "needs_human": needs_human,
            "auto_replies": auto_replies,
            "signals_proposed": len(proposed),
            "email_provider": _current_provider(),
            "unprofiled": unprofiled_count,
        }

        # Ordered by how much it blocks Harvey from doing anything at all.
        brain = await _brain_status()
        if brain.get("status") == "paused":
            who = "Codex" if brain.get("provider") == "codex" else "Roadie Crew's model"
            items.append({
                "key": "brain-paused", "tone": "bad",
                "title": f"{who} is paused, so Roadie Crew is waiting",
                "detail": (str(brain.get("reason") or "It can't be used right now.")[:300] +
                           " Replies and queued emails are untouched; Roadie Crew resumes "
                           "once it's fixed. harvey status --check says what's wrong."),
                "action": "Open the log", "tab": "controls",
            })

        if paused:
            items.append({
                "key": "paused", "tone": "bad",
                "title": "Sending is paused",
                "detail": str(paused) + ". Nothing will go out until you resume it.",
                "action": "Review and resume", "tab": "outbox",
            })

        setup = await get_setup_status()
        if isinstance(setup, dict) and setup.get("percent", 100) < 100:
            missing = [c["label"] for c in setup.get("checks", [])
                       if c.get("required") and not c.get("done")]
            items.append({
                "key": "setup", "tone": "warn",
                "title": "Finish setting Roadie Crew up",
                "detail": ", ".join(missing[:3]) or "Some required steps are incomplete.",
                "action": "Open setup", "tab": "settings",
            })

        if proposed:
            items.append({
                "key": "signals", "tone": "warn",
                "title": (f"{len(proposed)} signal waiting for your confirmation"
                          if len(proposed) == 1
                          else f"{len(proposed)} signals waiting for your confirmation"),
                "detail": ("Roadie Crew won't collect anything you haven't approved. "
                           "Confirm which signals define a good prospect for you."),
                "action": "Review signals", "tab": "signals",
            })
        elif not confirmed:
            items.append({
                "key": "signals-none", "tone": "warn",
                "title": "No signals confirmed",
                "detail": "Every signal is rejected, so prospecting has nothing to collect.",
                "action": "Review signals", "tab": "signals",
            })

        if not n_companies and confirmed:
            items.append({
                "key": "discover", "tone": "good",
                "title": "No companies yet",
                "detail": ("Signals are confirmed but nothing has been collected. "
                           "Discovery is free to try — no account needed."),
                "action": "Find businesses", "tab": "discover",
            })

        unprofiled = unprofiled_count
        if unprofiled:
            items.append({
                "key": "profile", "tone": "good",
                "title": f"{unprofiled} companies not looked at yet",
                "detail": ("Reading their websites is free and it is what makes "
                           "a first message specific — what they have, what they "
                           "are missing, whether they are spending on ads."),
                "action": "Read their sites", "tab": "discover",
            })

        if needs_human:
            items.append({
                "key": "needs-human", "tone": "bad",
                "title": (f"{needs_human} conversation needs you" if needs_human == 1
                          else f"{needs_human} conversations need you"),
                "detail": ("Roadie Crew stopped and handed these over: an escalation, or a "
                           "reply it had no safe answer for. Reply yourself, then close "
                           "it or hand it back."),
                "action": "Read them", "tab": "conversations",
            })

        if pending:
            items.append({
                "key": "outbox", "tone": "warn",
                "title": (f"{len(pending)} message waiting for approval" if len(pending) == 1
                          else f"{len(pending)} messages waiting for approval"),
                "detail": ("Nothing sends until you approve it. Read them one at a time"
                           + (f" ({pending_wa} on WhatsApp, {len(pending) - pending_wa} by email)."
                              if pending_wa and pending_wa != len(pending) else
                              " (WhatsApp)." if pending_wa else ".")),
                "action": "Open the decisions desk", "tab": "outbox",
            })

        if open_convos:
            items.append({
                "key": "replies", "tone": "good",
                "title": (f"{open_convos} live conversation" if open_convos == 1
                          else f"{open_convos} live conversations"),
                "detail": "People replied. Check how Roadie Crew is handling them."
                          + (f" ({auto_replies} more were only automatic replies.)" if auto_replies else ""),
                "action": "Read conversations", "tab": "conversations",
            })

        return {"items": items, "stats": stats}
    except Exception as e:
        logger.exception("today load failed")
        return {"items": [], "stats": stats, "error": str(e)}


# ── Signals: Harvey proposes, the user confirms ──
#
# Nothing is collected until a human has said yes to it. This is the gate the
# whole prospecting pipeline hangs off: collectors ask `state.confirmed_signal_codes()`
# and skip anything that isn't in the set.

CATEGORY_META = {
    "discovery": {
        "label": "Discovery — who exists",
        "blurb": "How Roadie Crew finds businesses at all, and how visible they are. "
                 "This is the only stage that costs money.",
    },
    "profile": {
        "label": "Profile — what they are",
        "blurb": "Read from the pages a business already publishes. Free, no AI "
                 "tokens, three HTTP requests per company. These are the signals "
                 "that make an email specific.",
    },
    "people": {
        "label": "People — who decides",
        "blurb": "Named humans and whether they're the one who can say yes.",
    },
    "verification": {
        "label": "Contactability — can you reach them",
        "blurb": "Whether the address will actually deliver, and what to do when "
                 "it won't.",
    },
}
CATEGORY_ORDER = ["discovery", "profile", "people", "verification"]


@app.get("/api/signals")
async def get_signals():
    """The signal vocabulary, grouped for review, with live cohort sizes."""
    try:
        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seeding is idempotent and never overrides a decision the user made,
        # so it is safe to run on every load — new signals shipped in an
        # upgrade show up as `proposed` without any migration step.
        await seed_signal_catalog(state)

        codes = await state.get_signal_codes()
        counts = {c["signal_code"]: c for c in await state.signal_counts()}

        groups, summary = [], {"proposed": 0, "confirmed": 0, "rejected": 0}
        for cat in CATEGORY_ORDER:
            rows = []
            for sig in codes:
                if sig.get("category") != cat:
                    continue
                seen = counts.get(sig["code"], {})
                rows.append({
                    **sig,
                    "companies": seen.get("companies", 0),
                    "observations": seen.get("observations", 0),
                })
            if rows:
                meta = CATEGORY_META.get(cat, {})
                groups.append({
                    "key": cat,
                    "label": meta.get("label", cat.title()),
                    "blurb": meta.get("blurb", ""),
                    "signals": rows,
                })
        for sig in codes:
            summary[sig.get("status", "proposed")] = (
                summary.get(sig.get("status", "proposed"), 0) + 1
            )

        return {"summary": summary, "groups": groups, "total": len(codes)}
    except Exception as e:
        logger.exception("signals load failed")
        return {"error": str(e), "groups": [], "summary": {}}


@app.post("/api/signals/status")
async def set_signals_status(request: Request):
    """Confirm or reject one signal, or a whole category at once."""
    try:
        body = await request.json()
        status = (body.get("status") or "").strip()
        codes = body.get("codes") or ([body["code"]] if body.get("code") else [])
        if status not in ("proposed", "confirmed", "rejected"):
            return JSONResponse(
                {"success": False, "message": f"invalid status: {status!r}"},
                status_code=400,
            )
        if not codes:
            return JSONResponse(
                {"success": False, "message": "no signal codes given"}, status_code=400
            )

        from harvey.signals import seed_signal_catalog

        state = _state()
        await state.init_db()
        # Seed first: a confirm that arrives before anything has loaded the
        # catalog would otherwise report success while changing nothing.
        await seed_signal_catalog(state)

        changed, unknown = 0, []
        for code in codes:
            if await state.set_signal_status(code, status):
                changed += 1
            else:
                unknown.append(code)
        return {
            "success": True, "changed": changed,
            "status": status, "unknown": unknown,
        }
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


COHORT_PAGE_SIZE = 50
COHORT_MAX_PAGE_SIZE = 200


async def _cohort_companies(require: list[str], exclude: list[str],
                            offset: int = 0, limit: int | None = None):
    state = _state()
    await state.init_db()
    return await state.cohort_companies(require, exclude, offset, limit)


def _codes(raw) -> list[str]:
    """Accept lists or CSV strings, with bounded SQL parameter counts."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list) or any(not isinstance(c, str) for c in raw):
        raise ValueError("signals must be a list of strings or comma-separated string")
    codes = list(dict.fromkeys(c.strip() for c in raw if c.strip()))
    if len(codes) > 200 or any(len(c) > 128 for c in codes):
        raise ValueError("too many or oversized signal codes")
    return codes


def _cohort_integer(raw, default):
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError("offset and limit must be integers")
    value = int(raw)
    if abs(value) > 9223372036854775807:
        raise ValueError("offset or limit is too large")
    return value


@app.post("/api/cohort")
async def preview_cohort(request: Request):
    """How many companies carry ALL these signals and none of those.

    The payoff for confirming signals: a cohort is a query, not a list. Set
    intersection happens in SQL — intersecting in JS over a capped fetch
    silently returns the wrong answer. ``offset``/``limit`` page through the
    matches; ``size`` is always the whole cohort.
    """
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("request body must be an object")
        require = _codes(body.get("require"))
        exclude = _codes(body.get("exclude"))
        offset = max(0, _cohort_integer(body.get("offset"), 0))
        limit = min(max(1, _cohort_integer(body.get("limit"), COHORT_PAGE_SIZE)),
                    COHORT_MAX_PAGE_SIZE)
        if not require:
            return {"size": 0, "offset": 0, "limit": limit, "companies": []}

        size, rows = await _cohort_companies(require, exclude, offset, limit)
        return {"size": size, "offset": offset, "limit": limit,
                "companies": rows}
    except (ValueError, TypeError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"size": 0, "companies": [], "error": str(e)}, status_code=500)


def _csv_cell(value):
    text = str(value or "")
    # Spreadsheet software may ignore leading whitespace before formulas.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


@app.get("/api/cohort.csv")
async def export_cohort(require: str = "", exclude: str = ""):
    """The whole cohort as a CSV download — every match, not just one page."""
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "domain", "website", "industry", "location"])
    try:
        codes = _codes(require)
        excluded = _codes(exclude)
        if codes:
            _, rows = await _cohort_companies(codes, excluded)
            for r in rows:
                writer.writerow([_csv_cell(r.get(k)) for k in
                                 ("name", "domain", "website", "industry", "location")])
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.warning("Cohort export failed: %s", e)
        return JSONResponse({"error": "Cohort export failed"}, status_code=500)
    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cohort.csv"'},
    )


@app.get("/api/runs")
async def get_runs_api():
    """The collector run log — what ran, when, what it produced and cost."""
    try:
        state = _state()
        await state.init_db()
        await state.sweep_stale_runs()
        runs = await state.get_runs(limit=25)
        # Same rule as the live panel: a "running" run that has recorded
        # nothing for STALE_RUN_SECONDS has stopped, whatever its row says.
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for run in runs:
            if run.get("status") != "running":
                continue
            last = _parse_ts(await _first(
                "SELECT MAX(observed_at) AS t FROM observations WHERE run_id = ?",
                (run["id"],))) or _parse_ts(run.get("started_at"))
            if last and (now - last).total_seconds() > STALE_RUN_SECONDS:
                run["status"] = "stalled"
        return runs
    except Exception as e:
        return {"error": str(e)}


WEB_DIR = (Path(__file__).resolve().parent / "web")


TEXT_TYPES = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}
BINARY_TYPES = {".woff2": "font/woff2", ".woff": "font/woff", ".png": "image/png"}


@app.get("/static/{path:path}")
async def static_file(path: str):
    """Serve the dashboard's own assets from disk.

    Read per-request rather than cached at import: editing app.css and hitting
    reload is the whole point of having them as real files. Fonts are vendored
    rather than fetched from a CDN — this is a local tool and it should work
    with the network off.
    """
    target = (WEB_DIR / path).resolve()
    root = WEB_DIR.resolve()
    if not target.is_file() or not target.is_relative_to(root):
        return PlainTextResponse("not found", status_code=404)

    if target.suffix in BINARY_TYPES:
        return Response(
            target.read_bytes(),
            media_type=BINARY_TYPES[target.suffix],
            headers={"Cache-Control": "public, max-age=604800"},
        )
    return PlainTextResponse(
        target.read_text(encoding="utf-8"),
        media_type=TEXT_TYPES.get(target.suffix, "text/plain"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


# ── Accounts: sign in, invites, users ──

_login_failures: dict[str, list[float]] = {}
LOGIN_WINDOW, LOGIN_MAX = 900, 8


def _login_blocked(key: str) -> bool:
    import time
    now = time.monotonic()
    recent = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW]
    _login_failures[key] = recent
    return len(recent) >= LOGIN_MAX


def _login_failed(key: str) -> None:
    import time
    _login_failures.setdefault(key, []).append(time.monotonic())


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _me(request: Request) -> dict:
    return request.scope.get("harvey_user") or {}


async def _start_session(response, user: dict) -> None:
    from harvey import users as acct
    token = acct.new_token()
    await _state().create_session(acct.hash_token(token), user["id"], acct.expiry(acct.SESSION_DAYS))
    await _state().touch_login(user["id"])
    response.set_cookie(
        auth.SESSION_COOKIE, token, max_age=acct.SESSION_DAYS * 86400, httponly=True,
        secure=_public_origin().startswith("https://"), samesite="strict", path="/")


@app.post("/api/auth/login")
async def auth_login(request: Request):
    from harvey import users as acct
    data = await _json_body(request)
    email = str(data.get("email") or "").strip().lower()[:254]
    password = str(data.get("password") or "")[:200]
    if _login_blocked(email) or _login_blocked("*"):
        return JSONResponse({"success": False, "message": "Too many attempts. Wait a few minutes."},
                            status_code=429)
    user = await _state().get_user_by_email(email) if email else None
    ok = acct.check_password(password, (user or {}).get("password_hash", ""))
    if not (ok and user and user["status"] == "active"):
        _login_failed(email)
        _login_failed("*")
        return JSONResponse({"success": False, "message": "Wrong email or password."}, status_code=401)
    response = JSONResponse({"success": True, "role": user["role"]})
    await _start_session(response, user)
    return response


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    from harvey.users import hash_token
    value = auth.cookie_value(request.headers.getlist("cookie"))
    if value:
        await _state().delete_session(hash_token(value))
    response = JSONResponse({"success": True})
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = _me(request)
    return {"email": user.get("email", ""), "name": user.get("name", ""),
            "role": user.get("role", "operator"), "owner": not user.get("id")}


@app.post("/api/auth/invite-info")
async def auth_invite_info(request: Request):
    from harvey.users import hash_token
    token = str((await _json_body(request)).get("token") or "")[:200]
    user = await _state().get_user_by_invite(hash_token(token))
    if not user:
        return JSONResponse({"success": False, "message": "This invite is invalid or has expired."},
                            status_code=404)
    return {"success": True, "email": user["email"], "name": user["name"], "role": user["role"]}


@app.post("/api/auth/accept")
async def auth_accept(request: Request):
    from harvey import users as acct
    data = await _json_body(request)
    token = str(data.get("token") or "")[:200]
    password = str(data.get("password") or "")
    name = str(data.get("name") or "").strip()[:80]
    if len(password) < acct.MIN_PASSWORD or len(password) > 200:
        return _refuse(f"Pick a password of at least {acct.MIN_PASSWORD} characters.")
    user = await _state().get_user_by_invite(acct.hash_token(token))
    if not user:
        return JSONResponse({"success": False, "message": "This invite is invalid or has expired."},
                            status_code=404)
    await _state().activate_user(user["id"], acct.hash_password(password), name)
    response = JSONResponse({"success": True, "role": user["role"]})
    await _start_session(response, user)
    return response


async def _send_invite(user_id: str, email: str, name: str, role: str, invited_by: str) -> dict:
    """(Re)issue an invite and email it with the SMTP/mailbox Harvey already
    uses. The row exists either way, so a failed email can be resent."""
    from harvey import users as acct
    from harvey.config import load_config, load_env
    from harvey.integrations.mail_provider import get_mail_provider

    token = acct.new_token()
    await _state().save_invite(email, name, role, acct.hash_token(token),
                               acct.expiry(acct.INVITE_DAYS), invited_by)
    base = _public_origin()
    link = f"{base}/#invite={token}" if base else ""
    result = {"success": True, "email_sent": False, "invite_url": link}
    if not base:
        result["message"] = "Set dashboard.public_url in harvey.local.yaml so the link points somewhere."
        return result
    config = load_config()
    provider = get_mail_provider(config, load_env())
    if provider is None or not provider.is_configured():
        result["message"] = "No mailbox is configured to send the invite. Copy the link instead."
        return result
    who = name or email
    body = (f"Olá {who},\n\n{invited_by or 'A equipe'} convidou você para o painel do Roadie Crew "
            f"como {'administrador' if role == 'admin' else 'operador'}.\n\n"
            f"Crie sua senha neste link (vale por {acct.INVITE_DAYS} dias):\n{link}\n\n"
            "Se você não esperava este convite, ignore este e-mail.")
    sent = await provider.send_email(email, "Convite para o painel do Roadie Crew", body)
    result["email_sent"] = sent.ok
    result["message"] = "Invite sent." if sent.ok else f"The email failed: {sent.error}"
    return result


@app.get("/api/users")
async def users_list():
    return {"users": await _state().list_users()}


@app.post("/api/users/invite")
async def users_invite(request: Request):
    from harvey import users as acct
    data = await _json_body(request)
    email = str(data.get("email") or "").strip().lower()
    name = str(data.get("name") or "").strip()[:80]
    role = str(data.get("role") or "operator")
    if not acct.valid_email(email):
        return _refuse("That doesn't look like an email address.")
    if role not in acct.ROLES:
        return _refuse("Role must be admin or operator.")
    existing = await _state().get_user_by_email(email)
    if existing and existing["status"] != "invited":
        return _refuse("That person already has an account.")
    me = _me(request)
    return await _send_invite("", email, name, role, me.get("name") or me.get("email") or "")


@app.post("/api/users/{user_id}/resend")
async def users_resend(user_id: str, request: Request):
    user = await _state().get_user(user_id)
    if not user or user["status"] != "invited":
        return _refuse("Only a pending invite can be sent again.")
    me = _me(request)
    return await _send_invite(user["id"], user["email"], user["name"], user["role"],
                              me.get("name") or me.get("email") or "")


async def _other_user(user_id: str, request: Request):
    """The target of an admin action, or a refusal. Nobody changes their own account."""
    user = await _state().get_user(user_id)
    if not user:
        return None, JSONResponse({"success": False, "message": "No such user."}, status_code=404)
    if user_id == _me(request).get("id"):
        return None, _refuse("You can't change your own account.")
    return user, None


@app.post("/api/users/{user_id}/role")
async def users_role(user_id: str, request: Request):
    from harvey import users as acct
    role = str((await _json_body(request)).get("role") or "")
    if role not in acct.ROLES:
        return _refuse("Role must be admin or operator.")
    user, refusal = await _other_user(user_id, request)
    if refusal:
        return refusal
    await _state().set_user_role(user["id"], role)
    return {"success": True}


@app.post("/api/users/{user_id}/status")
async def users_status(user_id: str, request: Request):
    status = str((await _json_body(request)).get("status") or "")
    if status not in ("active", "disabled"):
        return _refuse("Status must be active or disabled.")
    user, refusal = await _other_user(user_id, request)
    if refusal:
        return refusal
    if not await _state().set_user_status(user["id"], status):
        return _refuse("This person hasn't accepted the invite yet.")
    return {"success": True}


@app.delete("/api/users/{user_id}")
async def users_delete(user_id: str, request: Request):
    user, refusal = await _other_user(user_id, request)
    if refusal:
        return refusal
    await _state().delete_user(user["id"])
    return {"success": True}


class DashboardSetupError(Exception):
    """The dashboard can't start safely; the message says what to do."""


def _check_loopback_bind(host: str) -> str:
    """The host to bind, which must be a loopback address. The dashboard has
    no remote-access mode: from another machine, use an SSH tunnel."""
    name = (host or "").strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if not auth.is_loopback(name):
        raise DashboardSetupError(
            f"Refusing to listen on {host!r}: the dashboard only listens on this machine "
            "(127.0.0.1, ::1 or localhost). To reach it from elsewhere, tunnel instead: "
            "ssh -L 5555:127.0.0.1:5555 <this-host>")
    return name


def dashboard_url(host: str = "127.0.0.1", port: int = 5555) -> str:
    """The unlock link. The token rides in the #fragment, which browsers
    never send to the server (so it stays out of logs and Referer); the
    page moves it to sessionStorage and strips it from the address bar."""
    name = _check_loopback_bind(host)
    try:
        if ipaddress.ip_address(name).version == 6:
            name = f"[{name}]"
    except ValueError:
        pass
    return f"http://{name}:{port}/#token={token_store.get()}"


def start_dashboard(host: str = "127.0.0.1", port: int = 5555):
    """Start the dashboard server (loopback only, token required)."""
    import uvicorn

    try:
        bind = _check_loopback_bind(host)
        url = dashboard_url(host, port)
    except (DashboardSetupError, auth.TokenError) as e:
        raise DashboardSetupError(str(e)) from e

    if IN_CONTAINER:
        # Container logs persist and get shared: keep the unlock link out of them.
        print("\n  Roadie Crew Dashboard running. Print the unlock link with:\n\n"
              "    docker compose exec dashboard python -m harvey dashboard --url\n")
    else:
        print(f"\n  Roadie Crew Dashboard running. Open this link to unlock it:\n\n    {url}\n")
        print(f"  The token is stored in {token_store.path} (owner-only). Keep the link")
        print("  private; anyone with it can use the dashboard. Press Ctrl+C to stop.\n")
    if os.name == "nt":
        print("  Windows: the token file is protected by your folder's permissions,")
        print("  not POSIX file modes.\n")
    uvicorn.run(app, host=bind, port=port, log_level="warning",
                proxy_headers=False, server_header=False, date_header=False)
