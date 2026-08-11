"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping (the part you
rewrite per carrier) can be tested as plain functions.
"""
from datetime import datetime, timedelta, timezone

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunyou import parcels as parcels_module
from custom_components.sunyou.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    ParcelStatus,
)
from custom_components.sunyou.parcels import (
    _collect_events,
    apply_delivered_filter,
    build_history,
    map_event_status,
    map_parcel_status,
    normalize_parcel,
    parse_iso,
    sort_parcels_by_ts,
)

from .payloads import (
    active_sample,
    delivered_sample,
    event,
    failed_sample,
    not_found_sample,
)

# ---------------------------------------------------------------------------
# map_parcel_status / map_event_status — the 17-pair vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("101", ParcelStatus.REGISTERED),  # PreAlert
        ("102", ParcelStatus.REGISTERED),  # InboundScan
        ("105", ParcelStatus.IN_TRANSIT),  # Dispatch
        ("106", ParcelStatus.IN_TRANSIT),  # PortDeparture
        ("201", ParcelStatus.IN_TRANSIT),  # TransitCountryArrival
        ("205", ParcelStatus.IN_TRANSIT),  # TransitCountryDeparted
        ("206", ParcelStatus.IN_TRANSIT),  # DestinationAirPortArrival
        ("2061", ParcelStatus.IN_TRANSIT),  # HandoverLastMile
        ("2062", ParcelStatus.IN_TRANSIT),  # ClearanceProcess
        ("2064", ParcelStatus.IN_TRANSIT),  # ClearanceSuccessed
        ("2067", ParcelStatus.IN_TRANSIT),  # DeliveryStationArrival
        ("2068", ParcelStatus.OUT_FOR_DELIVERY),  # DeliveryStationDepart
        ("2071", ParcelStatus.OUT_FOR_DELIVERY),  # OutForDelivery
        ("2072", ParcelStatus.OUT_FOR_DELIVERY),  # OutForDelivery_Second
        ("208", ParcelStatus.PROBLEM),  # DeliveryFail_Other
        ("20899", ParcelStatus.PROBLEM),  # DeliveryFail_SecondOther
        ("209", ParcelStatus.DELIVERED),  # Delivered
    ],
)
def test_map_parcel_status_known(status, expected):
    assert map_parcel_status(status) == expected


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status("") == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status("999999") == ParcelStatus.UNKNOWN


def test_map_event_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_event_status(None) is None
    assert map_event_status("777") is None
    assert map_event_status("209") == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status("31337", "TotallyMadeUp") == ParcelStatus.UNKNOWN
    assert map_parcel_status("31337", "TotallyMadeUp") == ParcelStatus.UNKNOWN
    assert caplog.text.count("31337") == 1
    assert "TotallyMadeUp" in caplog.text
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# displayStatus is never read
# ---------------------------------------------------------------------------


def test_displayStatus_is_never_read_even_when_it_lies():
    """All 16 delivered parcels in the research capture carried
    ``displayStatus: "4"`` — a reconstructed table once read that as
    "undelivered". Prove the code ignores it entirely regardless of value."""
    raw = delivered_sample()
    raw["displayStatus"] = "4"  # exactly what every real delivered parcel sent
    assert normalize_parcel(raw)["status"] == ParcelStatus.DELIVERED


def test_displayStatus_is_stripped_from_raw():
    """Not even kept for reference — so nobody downstream is tempted to read it."""
    parcel = normalize_parcel(delivered_sample())
    assert "displayStatus" not in parcel["raw"]
    # everything else survives
    assert parcel["raw"]["orderNo"] == delivered_sample()["orderNo"]


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    assert parse_iso("2026-04-29T13:12:42").tzinfo == timezone.utc
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_event_timestamp_combines_naive_createtime_with_sibling_timezone():
    """The regression the trap warns about: parsing createTime as UTC would
    put a delivery up to 8 hours out. +08:00 origin event must NOT collapse
    to a UTC reading of the same wall-clock digits."""
    history = build_history(
        [event("101", "PreAlert", "2021-07-05 16:14:54", "+08:00")]
    )
    assert history[0]["timestamp"] == "2021-07-05T16:14:54+08:00"
    assert parse_iso(history[0]["timestamp"]) != parse_iso(
        "2021-07-05T16:14:54+00:00"
    )


def test_event_timestamp_handles_negative_offset():
    history = build_history(
        [event("209", "Delivered", "2026-04-29T13:12:42".replace("T", " "), "-04:00")]
    )
    assert history[0]["timestamp"].endswith("-04:00")


def test_event_createtime_parse_failure_drops_event_and_warns_once(caplog):
    parcels_module._create_time_parse_failure_logged = False
    history = build_history(
        [
            event("101", "PreAlert", "not-a-timestamp", "+08:00"),
            event("102", "InboundScan", "also-not-a-timestamp", "+08:00"),
            event("209", "Delivered", "2021-07-20 11:30:00", "+08:00"),
        ]
    )
    assert len(history) == 1
    assert history[0]["raw_status"] == "Delivered"
    assert caplog.text.count("createTime") >= 1
    assert "issues/new" in caplog.text
    # a second (and third) parse failure does not add another warning
    assert caplog.text.count("did not match") == 1


def test_event_malformed_timezone_falls_back_to_utc(caplog):
    """A ``timeZone`` value that does not match ``±HH:MM`` is treated the
    same as a missing one — UTC fallback, one-shot warning — rather than
    raising."""
    parcels_module._missing_timezone_logged = False
    raw_event = event("209", "Delivered", "2021-07-20 11:30:00", "bogus")
    history = build_history([raw_event])
    assert history[0]["timestamp"] == "2021-07-20T11:30:00+00:00"
    assert "no timeZone sibling" in caplog.text


def test_newest_skips_events_with_unparseable_timestamps():
    """The newest event is picked by parsed time, skipping any event whose
    timestamp failed to parse — never by list position."""
    raw = {
        "orderNo": "SYAE1",
        "result": {
            "origin": {
                "items": [
                    event("209", "Delivered", "2021-07-20 11:30:00", "+08:00"),
                    event("101", "PreAlert", "not-a-timestamp", "+08:00"),
                ]
            }
        },
    }
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED


def test_event_missing_timezone_falls_back_to_utc_and_warns_once(caplog):
    parcels_module._missing_timezone_logged = False
    first = event("209", "Delivered", "2021-07-20 11:30:00", "+08:00")
    del first["timeZone"]
    second = event("101", "PreAlert", "2021-07-05 16:14:54", "+08:00")
    del second["timeZone"]
    history = build_history([second, first])
    assert history[-1]["timestamp"] == "2021-07-20T11:30:00+00:00"
    assert caplog.text.count("no timeZone sibling") == 1
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# build_history / result-leg concatenation
# ---------------------------------------------------------------------------


def test_build_history_orders_oldest_to_newest():
    events = delivered_sample()["result"]["origin"]["items"]
    history = build_history(events)
    assert len(history) == 2
    assert history[0]["raw_status"] == "PreAlert"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    events = [
        event("105", "Dispatch", f"2026-04-{day:02d} 10:00:00", "+08:00")
        for day in range(1, 26)
    ]
    assert len(build_history(events, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"status": "101"}]) == []  # no createTime
    assert build_history(["not-a-dict"]) == []


def test_normalize_iterates_result_legs_not_just_origin(caplog):
    """Never index result['origin'] directly — concatenate every leg present,
    and warn once about any leg other than 'origin' (none has ever been
    observed carrying real data besides it)."""
    raw = delivered_sample()
    raw["result"]["destination"] = {
        "items": [event("2071", "OutForDelivery", "2021-07-19 09:00:00", "+02:00")]
    }
    parcel = normalize_parcel(raw, include_history=True)
    assert len(parcel["history"]) == 3
    assert "leg=destination" in caplog.text
    assert "issues/new" in caplog.text


def test_unexpected_leg_warns_only_once(caplog):
    raw = {
        "result": {
            "transit": {
                "items": [event("105", "Dispatch", "2026-01-01 00:00:00", "+08:00")]
            }
        }
    }
    _collect_events(raw)
    _collect_events(raw)
    assert caplog.text.count("leg=transit") == 1


def test_normalize_handles_missing_or_malformed_result():
    assert normalize_parcel({"orderNo": "X"})["status"] == ParcelStatus.UNKNOWN
    assert normalize_parcel({"orderNo": "X", "result": "not-a-dict"})[
        "status"
    ] == ParcelStatus.UNKNOWN
    assert normalize_parcel({"orderNo": "X", "result": {"origin": "not-a-dict"}})[
        "status"
    ] == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "SunYou"
    assert parcel["barcode"] == "SYAE006809461"  # orderNo, not trackingNumber
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Delivered"  # eventCode, not lastContent
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2021-07-20T11:30:00+08:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["url"] == "https://sypost.net/search?orderNo=SYAE006809461"
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 2
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED
    assert parcel["history"][-1]["status"] == ParcelStatus.DELIVERED


def test_normalize_in_flight_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "DestinationAirPortArrival"
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    # no ETA anywhere in the SunYou payload
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_normalize_delivery_fail_parcel_maps_to_problem():
    """A band-ordering trap: the newest event (208, a delivery *attempt*
    failure) must win over the earlier out-for-delivery event, and PROBLEM
    must not be confused with RETURNING."""
    parcel = normalize_parcel(failed_sample())
    assert parcel["status"] == ParcelStatus.PROBLEM
    assert parcel["raw_status"] == "DeliveryFail_Other"
    assert parcel["delivered"] is False


def test_normalize_out_of_order_events_still_pick_the_newest():
    """Newest is decided by parsed timestamp, never by list position."""
    raw = delivered_sample()
    items = raw["result"]["origin"]["items"]
    raw["result"]["origin"]["items"] = list(reversed(items))
    parcel = normalize_parcel(raw)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2021-07-20T11:30:00+08:00"


def test_normalize_not_found_payload_reports_unknown():
    """Defensive: a has:false body should never reach normalize_parcel in
    practice (the API client already returns None for it), but if it does,
    it must not crash — there are no events to derive a status from."""
    parcel = normalize_parcel(not_found_sample())
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["barcode"] == "SY999999999"


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel({"orderNo": "SYAE000000001"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["history"] is None


def test_normalize_keeps_last_mile_handoff_under_raw():
    """trackingNumber is the last-mile carrier's number, not the barcode —
    it must never end up as `barcode`, only under `raw`."""
    parcel = normalize_parcel(delivered_sample())
    assert parcel["barcode"] != "SU014952148MI"
    assert parcel["raw"]["trackingNumber"] == "SU014952148MI"


def test_normalize_warns_once_on_first_carrier_name(caplog):
    parcels_module._carrier_name_logged = False
    normalize_parcel(delivered_sample())
    normalize_parcel(delivered_sample())
    assert caplog.text.count("carries a last-mile carrierName") == 1
    assert "meest" in caplog.text
    assert "issues/new" in caplog.text


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES


def test_capabilities_are_url_and_history_only():
    """SunYou exposes nothing about the last leg — see normalize_parcel's docstring."""
    assert CAPABILITIES == {"url", "history"}
