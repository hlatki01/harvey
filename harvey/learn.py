"""Lessons: how Theo improves without retraining anything.

When a person rejects or edits one of Theo's chat replies, that correction is
the one reliable signal. This reads recent corrections, asks the model for a
few short rules that would have avoided them, and stores them as *proposed*.
Nothing reaches a prompt until a person approves it (dashboard or
``harvey lessons``); approved lessons are added to the decision prompt.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("harvey.learn")

MIN_HOURS_BETWEEN_RUNS = 6
MAX_LESSONS_PER_RUN = 3
MAX_LESSON_CHARS = 200
LAST_RUN_KEY = "lessons_last_run"
_TS = "%Y-%m-%d %H:%M:%S"


async def propose_lessons(state, brain) -> int:
    """Turn new corrections into proposed lessons. Returns how many were stored."""
    now = datetime.utcnow()
    last = await state.get_setting(LAST_RUN_KEY)
    if last and now - datetime.strptime(last, _TS) < timedelta(hours=MIN_HOURS_BETWEEN_RUNS):
        return 0
    corrections = await state.get_corrections(last or "1970-01-01 00:00:00")
    await state.set_setting(LAST_RUN_KEY, now.strftime(_TS))
    if not corrections:
        return 0

    shown = "\n".join(
        f"- {c['kind'].upper()} ({c['channel']}): \"{c['before'][:300]}\""
        + (f" -> became \"{c['after'][:300]}\"" if c["after"] else "")
        for c in corrections[:15])
    known = "\n".join(f"- {l['text']}" for l in await state.get_lessons() if l["status"] != "rejected")
    prompt = f"""Theo is a sales assistant that writes to leads by WhatsApp and email. A person
rejected or edited these drafts:

{shown}

Rules we already have (do not repeat):
{known or "- none"}

Say which channel a rule is for only if it matters.
Write at most {MAX_LESSONS_PER_RUN} short rules (one sentence each, under {MAX_LESSON_CHARS} characters,
in English) that would have avoided these corrections. Only rules the corrections
clearly support; if they show no pattern, return none. Never add prices or links.
Return JSON: {{"lessons": ["..."]}}"""
    raw = await brain.think_json(prompt, session_id="harvey-learn", agent="analyst", task="propose_lessons")
    items = raw.get("lessons") if isinstance(raw, dict) else None
    stored = 0
    for text in (items if isinstance(items, list) else [])[:MAX_LESSONS_PER_RUN]:
        text = str(text).strip()
        if text and len(text) <= MAX_LESSON_CHARS and await state.add_lesson(text, f"{len(corrections)} corrections"):
            stored += 1
    logger.info(f"Learn: {stored} lesson(s) proposed from {len(corrections)} correction(s)")
    return stored
