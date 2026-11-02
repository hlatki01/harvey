"""The prospecting pipeline: DISCOVER → PROFILE → (people, verification).

Each stage is independently runnable and independently re-runnable. That is
the point of the design, not an accident of it: profiling can re-run without
re-buying discovery, and discovery can widen without re-profiling everyone.

What chains them is a *query*, not a hand-off. PROFILE asks the database
"which companies have I not looked at, or not looked at lately?" rather than
being handed a list by DISCOVER. So a profile run started for any reason
picks up whatever discovery left behind, a crashed run resumes by itself, and
re-observing on a schedule turns the same signal into a time series.

Segment before you enrich: enrichment cost scales with list size and
conversion does not, so profile the businesses you would actually contact,
not every row you hold.
"""

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("harvey.pipeline")

# How long a profile observation stays fresh. Re-reading a site every quarter
# is what makes "they dropped their agency" a visible event.
PROFILE_STALE_DAYS = 90


@dataclass
class PipelineReport:
    """What a full pass did, stage by stage."""

    discover: dict | None = None
    profiled_companies: int = 0
    profile_observations: int = 0
    profile_run_id: str = ""
    skipped: str = ""
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "discover": self.discover,
            "profiled_companies": self.profiled_companies,
            "profile_observations": self.profile_observations,
            "profile_run_id": self.profile_run_id,
            "skipped": self.skipped,
            "errors": self.errors,
        }

    def summary_line(self) -> str:
        parts = []
        if self.discover:
            parts.append(f"{self.discover.get('new_companies', 0)} new companies")
        if self.profiled_companies:
            parts.append(f"{self.profiled_companies} profiled "
                         f"({self.profile_observations} observations)")
        return ", ".join(parts) or "nothing to do"


async def run_profile_stage(
    state,
    limit: int = 100,
    stale_days: int = PROFILE_STALE_DAYS,
    triggers=None,
) -> tuple[int, int, str]:
    """Profile whatever currently needs it.

    Returns (companies, observations, run_id). Free — three HTTP requests per
    business, no browser, no model call — so it is safe to run on every
    heartbeat without touching the Claude budget. With a TriggersConfig, a
    re-read that changed one of its signals boosts that company's prospects.
    """
    from harvey.collectors.profile import profile_companies

    pending = await state.companies_needing_profile(limit=limit,
                                                    stale_days=stale_days)
    if not pending:
        return 0, 0, ""

    logger.info("profile: %d companies need a look", len(pending))
    observations, run_id = await profile_companies(state, pending)
    if triggers is not None and triggers.signals:
        await apply_triggers(state, run_id, triggers)
    return len(pending), observations, run_id


def configured_triggers():
    """harvey.yaml's triggers, or None when there is no usable config."""
    from harvey.config import ConfigError, load_config
    try:
        return load_config().triggers
    except (ConfigError, ValueError):
        return None


def _shown(value: str, value_type: str) -> str:

    if value_type == "bool":
        return {"1": "yes", "0": "no"}.get(value, value)
    return value or "nothing"


async def apply_triggers(state, run_id: str, triggers) -> int:
    """Boost the prospects of every company whose trigger signals changed in
    this run. Returns how many prospects were boosted."""
    from harvey.signals import SIGNAL_CATALOG

    catalog = {s["code"]: s for s in SIGNAL_CATALOG}
    boosted = 0
    for change in await state.signal_changes(run_id, triggers.signals):
        sig = catalog[change["signal_code"]]
        new = ", ".join(_shown(v, sig["value_type"]) for v in change["new"])
        old = ", ".join(_shown(v, sig["value_type"]) for v in change["old"])
        note = f"New since our last look: {sig['label']}: {new} (was {old})."
        n = await state.boost_new_prospects(change["company_id"], triggers.score_boost, note)
        boosted += n
        await state.log_action(
            action_type="signal_changed", agent="profile",
            details={"company_id": change["company_id"], "signal": change["signal_code"],
                     "old": change["old"], "new": change["new"], "prospects_boosted": n})
        logger.info(f"profile: {change['company_id']} {change['signal_code']} {old} -> {new} "
                    f"({n} prospect(s) boosted)")
    return boosted



async def run_prospecting(
    state,
    config,
    provider: str | None = None,
    queries=None,
    max_spend: float = 1.0,
    profile: bool = True,
    profile_limit: int = 200,
    dry_run: bool = False,
) -> PipelineReport:
    """Discover businesses, then read what their websites say about them.

    The two stages are deliberately not coupled through a list: profiling
    queries the database for what needs attention, so it also sweeps up
    anything an earlier run left unfinished.
    """
    from harvey.collectors.discover import DEFAULT_PROVIDER, run_discovery

    report = PipelineReport()

    discovery = await run_discovery(
        state, config, provider or DEFAULT_PROVIDER, queries,
        max_spend=max_spend, dry_run=dry_run,
    )
    report.discover = discovery.as_dict()
    report.errors.extend(discovery.errors)

    if dry_run:
        report.skipped = "dry run"
        return report
    if not profile:
        report.skipped = "profiling skipped"
        return report

    # Profile runs even when discovery found nothing new: a previous run may
    # have been interrupted, and observations go stale on a schedule.
    try:
        companies, observations, run_id = await run_profile_stage(
            state, limit=profile_limit, triggers=getattr(config, "triggers", None))

        report.profiled_companies = companies
        report.profile_observations = observations
        report.profile_run_id = run_id
    except Exception as e:
        logger.exception("profile stage failed")
        report.errors.append(f"profile: {e}")

    return report


async def run_auto_discovery(state, config, brain=None) -> dict | None:
    """One scheduled slice of discovery, or None when it isn't due.

    Takes `queries_per_run` (industry x city) searches at a time: ones never
    run first, then the longest unsearched, skipping searches that came back
    mostly with businesses already known (until `recheck_after_days`). When
    there is little new ground left and `expand_cities` is on, Harvey adds the
    next city itself (with `brain`), at most `city_additions_per_week` times.
    Spend is capped per day across runs; a paid provider with no budget is a no-op.
    """
    from datetime import datetime, timedelta, timezone
    from harvey.collectors.discover import PROVIDERS, run_discovery

    cfg = config.discovery
    if not cfg.enabled or cfg.provider not in PROVIDERS:
        return None

    since = (datetime.now(timezone.utc) - timedelta(hours=cfg.min_hours_between_runs)).strftime("%Y-%m-%d %H:%M:%S")
    async with state._connect() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM runs WHERE stage = 'discover' AND started_at >= ?", (since,)
        ) as cur:
            if (await cur.fetchone())[0]:
                return None
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM runs "
            "WHERE stage = 'discover' AND date(started_at) = date('now')"
        ) as cur:
            spent = (await cur.fetchone())[0]

    budget = cfg.max_spend_per_day - spent
    all_queries = await discovery_queries(state, config)
    if not all_queries or (budget <= 0 and PROVIDERS[cfg.provider].estimate(all_queries[:1]) > 0):
        return None

    yields = await state.query_yields()
    if cfg.expand_cities and brain is not None:
        fresh = sum(1 for q in all_queries if (q.term, q.location) not in yields)
        runs_a_day = 24 / max(cfg.min_hours_between_runs, 1)
        if fresh < cfg.queries_per_run * runs_a_day * 3:     # under three days of new ground
            if await expand_cities(state, config, brain, yields):
                all_queries = await discovery_queries(state, config)

    start = int(await state.get_setting("discovery_auto_cursor") or 0) % len(all_queries)
    rotated = all_queries[start:] + all_queries[:start]
    recheck = (datetime.now(timezone.utc) - timedelta(days=cfg.recheck_after_days)).replace(tzinfo=None).isoformat()

    def worn_out(y: dict) -> bool:
        return y["last_run_at"] > recheck and (
            not y["last_found"] or y["last_new"] / y["last_found"] < cfg.exhausted_below)

    def rank(q) -> tuple:
        y = yields.get((q.term, q.location))
        return (0, "") if y is None else (1, y["last_run_at"])

    ready = [q for q in rotated if (q.term, q.location) not in yields or not worn_out(yields[(q.term, q.location)])]
    batch = sorted(ready, key=rank)[:cfg.queries_per_run]      # stable: the rotation breaks ties
    if not batch:
        logger.info("Auto discovery: every search is worn out until a recheck is due.")
        return None
    await state.set_setting("discovery_auto_cursor", str((start + len(batch)) % len(all_queries)))

    report = await run_discovery(state, config, cfg.provider, batch, max_spend=max(budget, 0.0))
    logger.info("Auto discovery: %d new companies from %d queries (%s)",
                report.new_companies, report.queries, report.stopped or "done")
    return report.as_dict()


async def discovery_queries(state, config) -> list:
    """The configured (industry x city) searches plus the cities Harvey added."""
    from harvey.collectors.discover import build_queries
    configured = {c.lower() for c in config.icp.geography}
    extra = [c for c in await state.discovery_cities() if c["name"].lower() not in configured]
    queries = build_queries(config, cities=list(config.icp.geography) + [c["name"] for c in extra])
    coords = {c["name"]: c["coordinate"] for c in extra}
    for q in queries:
        if not q.coordinate and coords.get(q.location):
            q.coordinate = coords[q.location]
    return queries


CITY_RE = re.compile(r"^[^,\n]{2,60}, [A-Z]{2}$")


async def expand_cities(state, config, brain, yields: dict | None = None) -> str:
    """Add the next city to discovery, if one is due this week. Returns its name, or ""."""
    from datetime import datetime, timedelta, timezone
    from harvey import notify
    from harvey.collectors.discover import geocode_city
    cfg = config.discovery
    now = datetime.now(timezone.utc)
    stamp = "%Y-%m-%d %H:%M:%S"
    tried = await state.discovery_cities(removed=True)
    week_ago = (now - timedelta(days=7)).strftime(stamp)
    if sum(1 for c in tried if (c["added_at"] or "") >= week_ago) >= cfg.city_additions_per_week:
        return ""
    spacing = (now - timedelta(days=7 / max(cfg.city_additions_per_week, 1))).strftime(stamp)
    if any((c["added_at"] or "") >= spacing for c in tried):
        return ""      # spread across the week, not two on the same day

    yields = yields if yields is not None else await state.query_yields()
    per_city: dict[str, list[int]] = {}
    for (_, loc), y in yields.items():
        tally = per_city.setdefault(loc, [0, 0])
        tally[0] += y["last_found"]
        tally[1] += y["last_new"]
    listed = list(config.icp.geography) + [
        c["name"] for c in tried if not c["removed"] and c["name"] not in config.icp.geography]
    never = [c["name"] for c in tried if c["removed"]]
    lines = [f"- {c}: last searches found {per_city.get(c, [0, 0])[0]} businesses, "
             f"{per_city.get(c, [0, 0])[1]} of them new" for c in listed]
    avoid = ("Never suggest these (taken out or not found): " + ", ".join(never)) if never else ""
    prompt = f"""Harvey finds small businesses on Google Maps to sell websites to. It searches these Brazilian cities:
{chr(10).join(lines)}
{avoid}

Suggest the ONE next Brazilian city to add (any state): one with many small businesses
(barbershops, salons, pet shops, garages, clinics, shops), not already listed, ideally near
the cities above. Bigger and closer first.

Return ONLY JSON: {{"city": "Name, UF", "radius_km": 10 to 50 by the city's size, "why": "one short sentence in Brazilian Portuguese"}}"""
    raw = await brain.think_json(prompt, session_id="harvey-discovery", agent="analyst", task="expand_cities")
    if not isinstance(raw, dict):
        return ""
    city = " ".join(str(raw.get("city") or "").split())
    if not CITY_RE.match(city) or city.lower() in {c.lower() for c in listed + never}:
        logger.warning("Auto discovery: unusable city suggestion %r", raw.get("city"))
        return ""
    try:
        radius = max(10, min(50, int(raw.get("radius_km") or 25)))
    except (TypeError, ValueError):
        radius = 25
    coordinate = await geocode_city(state, city, radius)
    why = " ".join(str(raw.get("why") or "").split())[:200]
    if not coordinate:
        # Kept, as removed, so it is never proposed again.
        await state.add_discovery_city(city, "", "not found on the map")
        await state.remove_discovery_city(city)
        return ""
    await state.add_discovery_city(city, coordinate, why)
    await state.log_action("city_added", "discovery", {"city": city, "coordinate": coordinate, "why": why})
    logger.info("Auto discovery: added %s (%s)", city, why)
    await notify.push(state, config, "system", f"city:{city}",
                      f"Harvey: adicionei {city} à busca de empresas. {why}")
    return city
