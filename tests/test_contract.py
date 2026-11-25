"""The parcel contract's checks, run over every normalised sample."""

from datetime import UTC, datetime

import pytest

from custom_components.sunyou.const import ParcelStatus
from custom_components.sunyou.parcels import (
    is_pickup,
    normalize_parcel,
    planned_for_status,
    planned_window,
)

from .payloads import (
    active_sample,
    delivered_sample,
    event,
    failed_sample,
    out_for_delivery_sample,
    pickup_sample,
    returning_sample,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _collected() -> dict:
    sample = pickup_sample()
    sample["result"]["origin"]["items"].append(
        event("209", "Delivered", "2026-08-13 15:00:00", "+02:00")
    )
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "out_for_delivery": out_for_delivery_sample,
    "problem": failed_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "returning": returning_sample,
    "bare": lambda: {"orderNo": "SYAE000000001"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param]())


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_is_empty(parcel):
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-08-13T15:00:00+02:00"
    assert parcel["pickup"] is True


def test_a_parcel_that_never_reached_a_point_is_not_pickup():
    assert normalize_parcel(delivered_sample())["pickup"] is False


def test_a_pickup_parcel_that_moves_on_stays_pickup():
    sample = pickup_sample()
    sample["result"]["origin"]["items"].append(
        event("2071", "OutForDelivery", "2026-08-13 08:00:00", "+02:00")
    )
    parcel = normalize_parcel(sample)
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["pickup"] is True


def test_pickup_lookup_does_not_warn_for_unmapped_events(caplog):
    sample = active_sample()
    sample["result"]["origin"]["items"].append(
        event("9999", "Unseen", "2000-01-01 10:00:00", "+08:00")
    )
    parcel = normalize_parcel(sample)
    assert parcel["status"] is ParcelStatus.IN_TRANSIT
    assert "Unrecognised SunYou status" not in caplog.text


def test_is_pickup_route_flag_wins():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.IN_TRANSIT) is False


def test_planned_for_status_empties_done_states():
    for status in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert planned_for_status(status, "a", "b") == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, "a", "b") == ("a", "b")


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=UTC)
    assert start == "2099-04-29T13:00:00Z"
    assert datetime.fromisoformat(end) == datetime(2099, 4, 29, 23, 59, 59, tzinfo=UTC)


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=UTC)
    assert datetime.fromisoformat(start) == datetime(2099, 4, 29, tzinfo=UTC)
    assert datetime.fromisoformat(end) == datetime(2099, 4, 29, 23, 59, 59, tzinfo=UTC)
