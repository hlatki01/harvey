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
) -> tuple[int, int, str]:
    """Profile whatever currently needs it.

    Returns (companies, observations, run_id). Free — three HTTP requests per
    business, no browser, no model call — so it is safe to run on every
    heartbeat without touching the Claude budget.
    """
    from harvey.collectors.profile import profile_companies

    pending = await state.companies_needing_profile(limit=limit,
                                                    stale_days=stale_days)
    if not pending:
        return 0, 0, ""

    logger.info("profile: %d companies need a look", len(pending))
    observations, run_id = await profile_companies(state, pending)
    return len(pending), observations, run_id


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
            state, limit=profile_limit)
        report.profiled_companies = companies
        report.profile_observations = observations
        report.profile_run_id = run_id
    except Exception as e:
        logger.exception("profile stage failed")
        report.errors.append(f"profile: {e}")

    return report


async def run_auto_discovery(state, config) -> dict | None:
    """One scheduled slice of discovery, or None when it isn't due.

    Walks the (industry x city) list `queries_per_run` at a time, keeping its
    place in settings so every cycle of the heartbeat can call this cheaply.
    Spend is capped per day across runs; a paid provider with no budget is a no-op.
    """
    from datetime import datetime, timedelta, timezone
    from harvey.collectors.discover import PROVIDERS, build_queries, run_discovery

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
    all_queries = build_queries(config)
    if not all_queries or (budget <= 0 and PROVIDERS[cfg.provider].estimate(all_queries[:1]) > 0):
        return None

    start = int(await state.get_setting("discovery_auto_cursor") or 0) % len(all_queries)
    batch = [all_queries[(start + i) % len(all_queries)]
             for i in range(min(cfg.queries_per_run, len(all_queries)))]
    await state.set_setting("discovery_auto_cursor", str((start + len(batch)) % len(all_queries)))

    report = await run_discovery(state, config, cfg.provider, batch, max_spend=max(budget, 0.0))
    logger.info("Auto discovery: %d new companies from %d queries (%s)",
                report.new_companies, report.queries, report.stopped or "done")
    return report.as_dict()
