"""
SALES_LEARNING_PLAN.md Phase 6: best time to call. Pick-up rates from first attempts, smoothed;
retries moved to a better hour but always inside the campaign and legal windows; callbacks untouched.
"""

import random
from datetime import datetime, timedelta, timezone

from core.outbound import gate, outcomes, timing
from core.outbound import models as m

NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)        # Tuesday 10:00 in Brisbane
BRISBANE = {"_id": "lead_qld", "country": "AU", "region_code": "QLD", "timezone": "Australia/Brisbane",
            "timezones": ["Australia/Brisbane"]}
MAINLAND = {"_id": "lead_au", "country": "AU", "timezone": "",
            "timezones": ["Australia/Sydney", "Australia/Brisbane", "Australia/Adelaide", "Australia/Darwin", "Australia/Perth"]}
CAMPAIGN = {"_id": "cmp_1", "mode": "human_assisted", "retry_timing": "smart", "retry_after_hours": [48, 96], "max_attempts": 3,
            "calling_window": {"start": "09:30", "end": "17:00", "days": [1, 2, 3, 4, 5]}}


def _table(hot=(), cold_rate=0.1, hot_rate=0.6, n=40):
    cells = {}
    for dow in range(1, 8):
        for hour in range(7, 21):
            rate = hot_rate if (dow, hour) in hot or hour in hot else cold_rate
            cells[f"{dow}-{hour}"] = [round(n * rate), n]
    reached = sum(c[0] for c in cells.values())
    dials = sum(c[1] for c in cells.values())
    return {"cells": cells, "reached": reached, "dials": dials}


def test_rates_count_first_attempts_only_and_are_smoothed():
    rows = [{"status": "done", "attempt_no": 1, "outcome": "not_interested", "amd": "human", "country": "AU",
             "local": {"dow": 2, "hour": 10}},
            {"status": "done", "attempt_no": 1, "outcome": "no_answer", "country": "AU", "local": {"dow": 2, "hour": 10}},
            {"status": "done", "attempt_no": 2, "outcome": "demo_booked", "amd": "human", "country": "AU", "local": {"dow": 2, "hour": 10}},
            {"status": "done", "attempt_no": 1, "outcome": "blocked_by_gate", "country": "AU", "local": {"dow": 2, "hour": 10}}]
    t = timing.reach_table(rows)["AU"]
    assert t["cells"]["2-10"] == [1, 2] and t["dials"] == 2                 # retries and gate blocks don't count
    empty_r, empty_m = timing.smoothed(t, 3, 15)
    assert abs(empty_r / (empty_r + empty_m) - 0.5) < 1e-9                  # no data: the country's rate
    lucky = {"cells": {"2-10": [1, 1], "2-11": [9, 99]}, "reached": 10, "dials": 100}   # one dial, one answer, 10% country
    r, miss = timing.smoothed(lucky, 2, 10)
    assert abs(r / (r + miss) - 3 / 21) < 1e-9                              # pulled from 100% toward 10%


def test_retries_stay_inside_campaign_and_legal_hours():
    rng = random.Random(1)
    sunday = datetime(2026, 12, 5, 23, 0, tzinfo=timezone.utc)              # Sunday 9 am Brisbane: closed
    christmas = datetime(2026, 12, 24, 22, 0, tzinfo=timezone.utc)          # Christmas Day morning in Australia
    for lead in (BRISBANE, MAINLAND):
        for earliest in (NOW, sunday, christmas, NOW + timedelta(hours=9)):
            t = timing.best_retry_at(lead, CAMPAIGN, earliest=earliest, table=_table(hot=(9, 16)), rng=rng, horizon_h=48)
            if t is not None:
                assert gate.window_open(lead, CAMPAIGN, t), (lead["_id"], earliest, t)
                assert earliest <= t <= earliest + timedelta(hours=48, minutes=11)


def test_a_retry_tries_a_different_hour_and_prefers_what_they_said():
    table = _table(hot=(10, 14))
    picks_tried = 0
    for seed in range(40):
        t = timing.best_retry_at(BRISBANE, CAMPAIGN, earliest=NOW, table=table, tried=[(2, 10)], rng=random.Random(seed),
                                 horizon_h=8)
        local = t.astimezone(timing.ZoneInfo("Australia/Brisbane"))
        picks_tried += (local.isoweekday(), local.hour) == (2, 10)
    assert picks_tried < 10                                                   # the hour that already failed loses
    assert timing.preferred_hours("after 2pm") >= {14, 15, 16} and 13 not in timing.preferred_hours("after 2pm")
    assert timing.preferred_hours("mornings are best") == set(range(7, 12))
    assert timing.preferred_hours("") == set()


def test_retries_are_spread_not_bunched():
    table = _table(hot=(10, 11, 14, 15))
    picks = {timing.best_retry_at(BRISBANE, CAMPAIGN, earliest=NOW, table=table, rng=random.Random(s), horizon_h=24)
             for s in range(30)}
    assert len(picks) > 5


async def _lead(db, **kw):
    await db[m.LEADS].insert_one({**BRISBANE, "phone": "+61730000000", "owner": "platform", "status": "in_progress",
                                  "attempts": 1, **kw})


async def test_smart_timing_moves_a_retry_but_fixed_timing_is_unchanged(db):
    await _lead(db)
    await db[timing.STATS].insert_one({"_id": "timing:AU", **_table(hot=(15,))})
    timing._CACHE.clear()
    await outcomes.apply_to_lead(db, "lead_qld", CAMPAIGN, "no_answer", now=NOW)
    smart = (await db[m.LEADS].find_one({"_id": "lead_qld"}))["next_eligible_at"].replace(tzinfo=timezone.utc)
    assert NOW + timedelta(hours=48) <= smart <= NOW + timedelta(hours=72, minutes=11)
    assert gate.window_open(BRISBANE, CAMPAIGN, smart)
    await db[m.LEADS].update_one({"_id": "lead_qld"}, {"$set": {"status": "in_progress"}})
    fixed = {k: v for k, v in CAMPAIGN.items() if k != "retry_timing"}          # saved before the setting existed
    await outcomes.apply_to_lead(db, "lead_qld", fixed, "no_answer", now=NOW)
    plain = (await db[m.LEADS].find_one({"_id": "lead_qld"}))["next_eligible_at"].replace(tzinfo=timezone.utc)
    assert plain == NOW + timedelta(hours=48)


async def test_callbacks_keep_the_agreed_time(db):
    await _lead(db)
    agreed = NOW + timedelta(days=1, hours=5)
    await outcomes.apply_to_lead(db, "lead_qld", CAMPAIGN, "callback", now=NOW, callback_at=agreed)
    assert (await db[m.LEADS].find_one({"_id": "lead_qld"}))["next_eligible_at"].replace(tzinfo=timezone.utc) == agreed


async def test_refresh_stores_a_table_per_country(db):
    await db[m.ATTEMPTS].insert_many([
        {"_id": f"a{i}", "call_id": f"c{i}", "attempt_no": 1, "status": "done", "country": "AU", "amd": "human" if i % 2 else None,
         "outcome": "not_interested" if i % 2 else "no_answer", "local": {"dow": 2, "hour": 10}, "created_at": NOW - timedelta(days=1)}
        for i in range(6)])
    assert await timing.refresh(db, now=NOW) == 1
    doc = await db[timing.STATS].find_one({"_id": "timing:AU"})
    assert doc["cells"]["2-10"] == [3, 6]
    cells = timing.heatmap(doc)
    assert next(c for c in cells if c["dow"] == 2 and c["hour"] == 10)["dials"] == 6
    assert next(c for c in cells if c["dow"] == 3 and c["hour"] == 10)["rate"] is None
