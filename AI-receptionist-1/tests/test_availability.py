"""Availability engine (Roadmap Task 2.7): where booking bugs live, so this is tested hard."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.services import availability as av
from core.services.availability import Interval, ResourceWindow, free_slots, opening_intervals, rank, slots_in
from core.tenancy.models import HoursEntry

UTC = timezone.utc
CHI = "America/Chicago"


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


def _local(tz, y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _hours(*dows, open="09:00", close="17:00"):
    return [HoursEntry(dow=d, open=open, close=close) for d in dows]


# ── Opening intervals ──────────────────────────────────────

def test_opening_intervals_use_business_timezone():
    # Friday 20 Nov 2026 in Chicago (CST, UTC-6): 09:00 local = 15:00 UTC
    [iv] = opening_intervals(_hours(4), date(2026, 11, 20), CHI)
    assert iv.start == _utc(2026, 11, 20, 15) and iv.end == _utc(2026, 11, 20, 23)


def test_closed_days_have_no_intervals():
    assert opening_intervals(_hours(0, 1, 2, 3, 4), date(2026, 11, 21), CHI) == []   # Saturday


def test_overnight_hours_end_on_the_next_day():
    [iv] = opening_intervals([HoursEntry(dow=4, open="20:00", close="02:00")], date(2026, 11, 20), CHI)
    assert iv.start == _local(CHI, 2026, 11, 20, 20) and iv.end == _local(CHI, 2026, 11, 21, 2)


def test_twenty_four_hours():
    [iv] = opening_intervals([HoursEntry(dow=4, open="00:00", close="24:00")], date(2026, 11, 20), CHI)
    assert iv.end - iv.start == timedelta(hours=24)


def test_dst_fall_back_day_is_25_local_hours_long_but_hours_stay_local():
    # 1 Nov 2026: clocks go back in Chicago. 09:00–17:00 local must still be 8 hours.
    [iv] = opening_intervals(_hours(6), date(2026, 11, 1), CHI)
    assert iv.end - iv.start == timedelta(hours=8)
    assert iv.start.astimezone(ZoneInfo(CHI)).hour == 9


def test_dst_spring_forward():
    # 8 Mar 2026: 02:00 local doesn't exist. Hours 09:00–17:00 are unaffected but UTC offset changes.
    [iv] = opening_intervals(_hours(6), date(2026, 3, 8), CHI)
    assert iv.start == _utc(2026, 3, 8, 14)      # CDT is UTC-5 after the switch
    assert iv.end - iv.start == timedelta(hours=8)


def test_two_split_shifts_are_kept_apart():
    hours = [HoursEntry(dow=4, open="09:00", close="12:00"), HoursEntry(dow=4, open="13:00", close="17:00")]
    ivs = opening_intervals(hours, date(2026, 11, 20), CHI)
    assert len(ivs) == 2


# ── Set algebra ────────────────────────────────────────────

def test_subtract_splits_and_trims():
    free = [Interval(_utc(2026, 1, 1, 9), _utc(2026, 1, 1, 17))]
    busy = [Interval(_utc(2026, 1, 1, 12), _utc(2026, 1, 1, 13)), Interval(_utc(2026, 1, 1, 16), _utc(2026, 1, 1, 18))]
    out = av.subtract(free, busy)
    assert out == [Interval(_utc(2026, 1, 1, 9), _utc(2026, 1, 1, 12)), Interval(_utc(2026, 1, 1, 13), _utc(2026, 1, 1, 16))]


def test_intersect():
    a = [Interval(_utc(2026, 1, 1, 9), _utc(2026, 1, 1, 17))]
    b = [Interval(_utc(2026, 1, 1, 10), _utc(2026, 1, 1, 18))]
    assert av.intersect(a, b) == [Interval(_utc(2026, 1, 1, 10), _utc(2026, 1, 1, 17))]


def test_slots_step_and_fit():
    free = [Interval(_utc(2026, 1, 1, 9), _utc(2026, 1, 1, 10, 30))]
    slots = slots_in(free, timedelta(minutes=45), timedelta(minutes=15), "r1")
    assert [s.start.minute for s in slots] == [0, 15, 30, 45]
    assert slots[-1].end == _utc(2026, 1, 1, 10, 30)   # last slot ends exactly at closing


def test_slots_align_to_step_when_window_starts_oddly():
    free = [Interval(_utc(2026, 1, 1, 9, 7), _utc(2026, 1, 1, 10))]
    slots = slots_in(free, timedelta(minutes=15), timedelta(minutes=15), "r1")
    assert slots[0].start == _utc(2026, 1, 1, 9, 15)


# ── free_slots end to end ─────────────────────────────────

NOW = _local(CHI, 2026, 11, 19, 8)   # Thu 19 Nov, 08:00 local — before opening


def test_resource_hours_narrow_business_hours_and_busy_times_remove_slots():
    anna = ResourceWindow("anna", hours=_hours(3, open="10:00", close="14:00"),
                          busy=[Interval(_local(CHI, 2026, 11, 19, 11), _local(CHI, 2026, 11, 19, 12))])
    slots = free_slots(business_hours=_hours(3), tz=CHI, resources=[anna], duration=timedelta(minutes=60),
                       day_from=date(2026, 11, 19), day_to=date(2026, 11, 19), now=NOW, step=timedelta(minutes=60))
    starts = [s.start.astimezone(ZoneInfo(CHI)).hour for s in slots]
    assert starts == [10, 12, 13]     # 11:00 busy; 14:00 would end after Anna leaves


def test_min_notice_and_past_slots_are_excluded():
    now = _local(CHI, 2026, 11, 19, 12, 10)
    r = ResourceWindow("r")
    slots = free_slots(business_hours=_hours(3), tz=CHI, resources=[r], duration=timedelta(minutes=30),
                       day_from=date(2026, 11, 19), day_to=date(2026, 11, 19), now=now,
                       min_notice=timedelta(hours=2), step=timedelta(minutes=30))
    first = slots[0].start.astimezone(ZoneInfo(CHI))
    assert (first.hour, first.minute) == (14, 30)   # 12:10 + 2 h = 14:10 → next 30-min step


def test_business_exception_closes_the_whole_day_but_only_that_day():
    holiday = [Interval(_local(CHI, 2026, 11, 19, 0), _local(CHI, 2026, 11, 20, 0))]
    slots = free_slots(business_hours=_hours(3, 4), tz=CHI, resources=[ResourceWindow("r")],
                       duration=timedelta(minutes=30), day_from=date(2026, 11, 19), day_to=date(2026, 11, 20),
                       now=NOW, business_exceptions=holiday)
    days = {s.start.astimezone(ZoneInfo(CHI)).date() for s in slots}
    assert days == {date(2026, 11, 20)}


def test_two_staff_with_different_hours_both_offer_slots():
    anna = ResourceWindow("anna", hours=_hours(3, open="09:00", close="12:00"))
    mo = ResourceWindow("mo", hours=_hours(3, open="13:00", close="17:00"))
    slots = free_slots(business_hours=_hours(3), tz=CHI, resources=[anna, mo], duration=timedelta(minutes=60),
                       day_from=date(2026, 11, 19), day_to=date(2026, 11, 19), now=NOW, step=timedelta(minutes=60))
    by = {}
    for s in slots:
        by.setdefault(s.resource_id, []).append(s.start.astimezone(ZoneInfo(CHI)).hour)
    assert by == {"anna": [9, 10, 11], "mo": [13, 14, 15, 16]}


def test_back_to_back_buffer():
    appts = [{"start": _local(CHI, 2026, 11, 19, 10), "end": _local(CHI, 2026, 11, 19, 10, 45)}]
    busy = av.appointment_busy(appts, timedelta(minutes=15))
    r = ResourceWindow("r", busy=busy)
    slots = free_slots(business_hours=_hours(3), tz=CHI, resources=[r], duration=timedelta(minutes=45),
                       day_from=date(2026, 11, 19), day_to=date(2026, 11, 19), now=NOW, step=timedelta(minutes=15))
    local = [s.start.astimezone(ZoneInfo(CHI)).strftime("%H:%M") for s in slots]
    assert "09:15" in local and "09:30" not in local      # 9:15 ends exactly at 10:00; 9:30 would overlap
    assert "10:45" not in local and "11:00" in local      # the 15-min buffer after 10:45 is respected


def test_overnight_business_slots_cross_midnight():
    hours = [HoursEntry(dow=3, open="20:00", close="02:00")]
    now = _local(CHI, 2026, 11, 19, 12)
    slots = free_slots(business_hours=hours, tz=CHI, resources=[ResourceWindow("r")], duration=timedelta(minutes=60),
                       day_from=date(2026, 11, 19), day_to=date(2026, 11, 19), now=now, step=timedelta(minutes=60))
    local = [s.start.astimezone(ZoneInfo(CHI)).hour for s in slots]
    assert local == [20, 21, 22, 23, 0, 1]


# ── Ranking, ids, speech ───────────────────────────────────

def test_rank_prefers_closeness_then_distinct_times():
    base = _utc(2026, 1, 1, 9)
    slots = [av.Slot("a", base + timedelta(hours=h), base + timedelta(hours=h + 1)) for h in range(8)]
    slots += [av.Slot("b", base + timedelta(hours=3), base + timedelta(hours=4))]
    top = rank(slots, requested=base + timedelta(hours=3), n=3)
    assert [s.start.hour for s in top] == [12, 11, 13]
    assert all(top[0].start != s.start for s in top[1:])


def test_rank_without_request_is_earliest_first():
    base = _utc(2026, 1, 1, 9)
    slots = [av.Slot("a", base + timedelta(hours=h), base + timedelta(hours=h + 1)) for h in (5, 1, 3)]
    assert [s.start.hour for s in rank(slots, None, 2)] == [10, 12]


def test_slot_id_roundtrip():
    s = av.Slot("res_01ABC", _utc(2026, 11, 20, 15, 30), _utc(2026, 11, 20, 16))
    assert av.decode_slot_id(s.slot_id) == ("res_01ABC", s.start)
    assert av.decode_slot_id("garbage") is None


def test_lock_blocks_cover_the_appointment_in_5_minute_steps():
    blocks = av.lock_blocks(_utc(2026, 1, 1, 9, 2), _utc(2026, 1, 1, 9, 45))
    assert blocks[0] == _utc(2026, 1, 1, 9, 0) and blocks[-1] == _utc(2026, 1, 1, 9, 40) and len(blocks) == 9


def test_spoken_dates():
    today = date(2026, 11, 19)
    assert av.spoken(_local(CHI, 2026, 11, 19, 15, 30), CHI, today=today) == "today at 3:30 PM"
    assert av.spoken(_local(CHI, 2026, 11, 20, 9), CHI, today=today) == "tomorrow at 9:00 AM"
    assert av.spoken(_local(CHI, 2026, 11, 22, 13), CHI, today=today) == "Sunday the 22nd at 1:00 PM"
    assert av.spoken(_local(CHI, 2026, 11, 23, 13), CHI).startswith("Monday the 23rd")
    assert av.spoken(_local(CHI, 2026, 11, 11, 13), CHI).startswith("Wednesday the 11th")
