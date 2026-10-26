"""Analyst — tracks performance and identifies what's working."""

import json
import logging
import math
from datetime import datetime
from pathlib import Path

from harvey.models.campaign import SUBJECT_STYLES
from harvey.state import StateManager

logger = logging.getLogger("harvey.analyst")

MIN_SENDS_PER_VARIANT = 30
Z_95 = 1.96   # two-sided 95%


def subject_winner(stats: dict) -> tuple[str, float, float] | None:
    """(winner, its reply rate, the other's) once both variants have enough
    sends and the gap is significant (two-proportion z-test), else None."""
    a, b = stats.get("A"), stats.get("B")
    if not a or not b or min(a["sent"], b["sent"]) < MIN_SENDS_PER_VARIANT:
        return None
    ra, rb = a["replied"] / a["sent"], b["replied"] / b["sent"]
    pooled = (a["replied"] + b["replied"]) / (a["sent"] + b["sent"])
    se = math.sqrt(pooled * (1 - pooled) * (1 / a["sent"] + 1 / b["sent"]))
    if se == 0 or abs(ra - rb) / se < Z_95:
        return None
    return ("A", ra, rb) if ra > rb else ("B", rb, ra)


class Analyst:
    """Analyzes campaign performance, reply rates, and ICP segment conversion.

    Runs during idle cycles and writes a report to data/analytics.json.
    """

    def __init__(self, state: StateManager):
        self.state = state

    async def run(self):
        """Generate analytics report from current pipeline data."""
        logger.info("Analyst: Running performance analysis...")

        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "pipeline": await self._pipeline_summary(),
            "campaigns": await self._campaign_performance(),
            "intents": await self._intent_breakdown(),
            "stages": await self._stage_breakdown(),
            "insights": [],
        }

        # Generate insights
        report["insights"] = self._generate_insights(report)
        report["subject_test"] = await self.state.get_variant_stats()
        await self._subject_lesson(report)

        # Write report atomically (temp file + rename) so a crash mid-write
        # never leaves a corrupt analytics.json for the dashboard to choke on.
        data_dir = Path(self.state.db_path).parent
        report_path = data_dir / "analytics.json"
        try:
            tmp_path = report_path.with_suffix(".json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            tmp_path.replace(report_path)
            logger.info(f"Analyst: Report written to {report_path}")
        except OSError as e:
            logger.error(f"Analyst: Failed to write report: {e}")

        # Log key metrics
        pipeline = report["pipeline"]
        total = sum(pipeline.values())
        if total > 0:
            reply_rate = pipeline.get("replied", 0) / max(pipeline.get("contacted", 0), 1) * 100
            logger.info(
                f"Analyst: {total} prospects | "
                f"{pipeline.get('contacted', 0)} contacted | "
                f"Reply rate: {reply_rate:.1f}%"
            )

        for insight in report["insights"]:
            logger.info(f"Analyst: Insight — {insight}")

    async def _subject_lesson(self, report: dict):
        """Propose the email 1 subject test's winner as a lesson (once: the
        text is fixed, so re-proposing it is a no-op). A person approves it."""
        found = subject_winner(report["subject_test"])
        if not found:
            return
        winner, won, lost = found
        loser = "B" if winner == "A" else "A"
        text = (f"Email 1 subjects that {SUBJECT_STYLES[winner]} get more replies than ones "
                f"that {SUBJECT_STYLES[loser]}: write them that way.")
        evidence = (f"A/B test: {won:.0%} vs {lost:.0%} replies over "
                    f"{sum(v['sent'] for v in report['subject_test'].values())} first emails")
        report["insights"].append(f"TEST: {text} ({evidence}).")
        if await self.state.add_lesson(text, evidence):
            logger.info(f"Analyst: subject test has a winner ({winner}); lesson proposed.")

    async def _pipeline_summary(self) -> dict:

        """Count prospects by status."""
        return await self.state.count_prospects_by_status()

    async def _campaign_performance(self) -> list[dict]:
        """Get stats for each campaign."""
        return await self.state.get_campaign_stats()

    async def _intent_breakdown(self) -> dict:
        """Count replies by intent classification."""
        return await self.state.get_intent_distribution()

    async def _stage_breakdown(self) -> dict:
        """Count conversations by sales stage."""
        return await self.state.get_stage_distribution()

    def _generate_insights(self, report: dict) -> list[str]:
        """Derive actionable insights from the data.

        Every insight names the metric, the threshold it crossed, and the
        specific file or config to change — so a human (or Harvey itself)
        can act on it without further analysis.
        """
        insights = []
        pipeline = report.get("pipeline") or {}
        intents = report.get("intents") or {}
        campaigns = report.get("campaigns") or []
        stages = report.get("stages") or {}

        # Pipeline health
        total_new = pipeline.get("new", 0)
        total_queued = pipeline.get("queued", 0)
        total_contacted = pipeline.get("contacted", 0)
        total_replied = pipeline.get("replied", 0)
        total_opted_out = pipeline.get("opted_out", 0)
        total_interested = intents.get("interested", 0)
        total_objections = intents.get("objection", 0)
        total_unsubscribes = intents.get("unsubscribe", 0) + total_opted_out
        total_escalations = intents.get("escalate", 0)

        if total_contacted > 10:
            reply_rate = total_replied / total_contacted * 100
            if reply_rate < 5:
                insights.append(
                    f"ACTION: Reply rate is low ({reply_rate:.1f}%, benchmark 5-10%). "
                    "Rewrite email 1 in prompts/writer.md with a sharper observation, "
                    "and test 2-3 new subject lines via skills/email_frameworks.md."
                )
            elif reply_rate > 15:
                insights.append(
                    f"KEEP: Reply rate is strong ({reply_rate:.1f}%). Don't change the "
                    "current messaging. Scale prospecting to feed it more leads."
                )

        # Deliverability / reputation warning — the most urgent signal
        if total_contacted > 10:
            optout_rate = total_unsubscribes / total_contacted * 100
            if optout_rate > 2:
                insights.append(
                    f"URGENT: Opt-out rate is {optout_rate:.1f}% (>2% risks blacklisting). "
                    "Pause sending, tighten ICP targeting in harvey.yaml, and reduce "
                    "max_daily_sends until the list quality improves."
                )

        if total_escalations > 0:
            insights.append(
                f"HUMAN NEEDED: {total_escalations} conversation(s) escalated (angry/legal). "
                "Review conversations with status 'needs_human' in the dashboard before "
                "any further sending to those domains."
            )

        # Objection patterns
        total_replies = sum(intents.values()) if intents else 0
        if total_replies > 5:
            objection_pct = total_objections / total_replies * 100
            if objection_pct > 40:
                insights.append(
                    f"ACTION: {objection_pct:.0f}% of replies are objections. Add the "
                    "recurring objections to objection_responses in harvey.yaml and "
                    "skills/objection_handling.md, or narrow targeting to better-fit accounts."
                )

        # Interest conversion
        if total_interested > 0 and total_contacted > 0:
            interest_rate = total_interested / total_contacted * 100
            insights.append(
                f"Interest rate: {interest_rate:.1f}% of contacted prospects show interest "
                f"({total_interested} of {total_contacted})."
            )

        # Campaign comparison — name winner AND loser so the fix is obvious
        rated = [c for c in campaigns if c.get("reply_rate") is not None]
        if len(rated) >= 2:
            best = max(rated, key=lambda c: c.get("reply_rate", 0))
            worst = min(rated, key=lambda c: c.get("reply_rate", 0))
            if best.get("reply_rate", 0) > 0 and best is not worst:
                insights.append(
                    f"ACTION: '{best.get('name')}' ({best.get('reply_rate')}% replies) beats "
                    f"'{worst.get('name')}' ({worst.get('reply_rate', 0)}%). Reuse the winner's "
                    "angle and subject-line style for the next campaign; pause or rewrite the loser."
                )

        # Pipeline bottlenecks
        if stages.get("engaged", 0) > 3 and stages.get("closing", 0) == 0:
            insights.append(
                "ACTION: Prospects engage but none reach closing. Update prompts/handler.md "
                "CLOSING guidance to propose specific meeting times earlier (by message 2)."
            )
        if total_queued > 0 and total_contacted == 0:
            insights.append(
                f"BLOCKED: {total_queued} prospects are queued but nothing has been sent. "
                "Check the Instantly API key and Sender logs — campaigns may be failing to deploy."
            )
        if total_new < 10 and total_queued == 0:
            insights.append(
                f"ACTION: Prospect pool is nearly empty ({total_new} new). Scout should run "
                "next cycle; if it keeps coming up dry, broaden the ICP in harvey.yaml."
            )

        if not insights:
            insights.append("Not enough data yet. Keep prospecting and sending campaigns.")

        return insights
