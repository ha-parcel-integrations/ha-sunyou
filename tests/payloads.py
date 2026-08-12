"""Sample SunYou API payloads shared by the test modules.

``delivered_sample`` is the **real** payload captured for ``SYAE006809461`` —
the tracking number published in
[uw0s/parcel-tracker](https://github.com/uw0s/parcel-tracker)'s test suite,
trimmed to the two events documented in
``carrier-research/api/sunyou/tracking.md`` — the capture that turned
SunYou's payload from reconstructed to confirmed.

The in-flight and delivery-fail samples are **not** captures: they reuse the
identical envelope shape and field names, with a synthetic event list built
strictly from the 17-code vocabulary that same capture established. No field
name is invented anywhere in this module.
"""
from __future__ import annotations

import json

DELIVERED_CODE = "SYAE006809461"
ACTIVE_CODE = "SYAE100000001"
FAILED_CODE = "SYAE100000002"
PICKUP_CODE = "SYAE100000003"
RETURNING_CODE = "SYAE100000004"
NOT_FOUND_CODE = "SY999999999"


def event(
    status: str,
    event_code: str,
    create_time: str,
    time_zone: str,
    *,
    content: str | None = None,
    office: str | None = None,
) -> dict:
    """One entry of a ``result`` leg's ``items`` list."""
    entry = {
        "content": content or event_code,
        "createTime": create_time,
        "eventCode": event_code,
        "status": status,
        "timeZone": time_zone,
    }
    if office is not None:
        entry["office"] = office
    return entry


def not_found_sample(order_no: str = NOT_FOUND_CODE) -> dict:
    """The single ``data[0]`` entry SunYou returns for an unknown number.

    HTTP 200, ``has: false`` — SunYou never uses a non-2xx status or an
    envelope-level failure for "not found".
    """
    return {"displayStatus": "0", "has": False, "orderNo": order_no}


def delivered_sample(order_no: str = DELIVERED_CODE) -> dict:
    """The real, captured payload for SYAE006809461 (trimmed to 2 events).

    ``trackingNumber`` (``SU014952148MI``) is the last-mile carrier's own
    number for the handoff — *not* the barcode, which is ``orderNo``.
    ``displayStatus: "4"`` is present exactly as SunYou sends it, to prove the
    code never reads it: all 16 delivered parcels in the research capture
    carried this same coarse-bucket value.
    """
    return {
        "orderNo": order_no,
        "has": True,
        "displayStatus": "4",
        "lastContent": "Delivered",
        "lastEventCode": "Delivered",
        "lastStatus": "209",
        "lastOffice": "1622",
        "lastUpdate": "2021-07-20 11:30:00",
        "orgCountry": "CN",
        "dstCountry": "GR",
        "trackingNumber": "SU014952148MI",
        "transitDays": 14,
        "carrierName": "meest",
        "carrierPhone": "+302100000000",
        "carrierWebsite": "http://meest-group.com/",
        "result": {
            "origin": {
                "items": [
                    event(
                        "101",
                        "PreAlert",
                        "2021-07-05 16:14:54",
                        "+08:00",
                        content="Pre-Shipment Info Sent To Greece",
                    ),
                    event(
                        "209",
                        "Delivered",
                        "2021-07-20 11:30:00",
                        "+08:00",
                        content="Delivered",
                        office="1622",
                    ),
                ]
            }
        },
    }


def active_sample(order_no: str = ACTIVE_CODE) -> dict:
    """An in-flight parcel, newest event ``206 DestinationAirPortArrival``.

    Synthetic, but every field name and every status/eventCode pair is one
    observed in the research capture. Maps to ``in_transit``.
    """
    return {
        "orderNo": order_no,
        "has": True,
        "displayStatus": "1",
        "lastContent": "Arrived At Destination Airport",
        "lastEventCode": "DestinationAirPortArrival",
        "lastStatus": "206",
        "lastUpdate": "2026-07-25 09:12:00",
        "orgCountry": "CN",
        "dstCountry": "NL",
        "trackingNumber": None,
        "transitDays": None,
        "result": {
            "origin": {
                "items": [
                    event("101", "PreAlert", "2026-07-18 10:00:00", "+08:00"),
                    event("102", "InboundScan", "2026-07-19 08:30:00", "+08:00"),
                    event("105", "Dispatch", "2026-07-20 03:10:00", "+08:00"),
                    event("106", "PortDeparture", "2026-07-21 14:20:00", "+08:00"),
                    event(
                        "206",
                        "DestinationAirPortArrival",
                        "2026-07-25 09:12:00",
                        "+02:00",
                    ),
                ]
            }
        },
    }


def out_for_delivery_sample(order_no: str = ACTIVE_CODE) -> dict:
    """A parcel out for delivery, newest event ``2071 OutForDelivery``.

    Same shape as :func:`active_sample`, one leg further along the pipeline —
    used to exercise a genuine status transition (``in_transit`` ->
    ``out_for_delivery``) in the coordinator tests.
    """
    sample = active_sample(order_no)
    sample["displayStatus"] = "1"
    sample["lastContent"] = "Out For Delivery"
    sample["lastEventCode"] = "OutForDelivery"
    sample["lastStatus"] = "2071"
    sample["lastUpdate"] = "2026-07-26 08:30:00"
    sample["result"]["origin"]["items"].append(
        event("2071", "OutForDelivery", "2026-07-26 08:30:00", "+02:00")
    )
    return sample


def failed_sample(order_no: str = FAILED_CODE) -> dict:
    """A parcel whose last-mile delivery attempt failed (``208 DeliveryFail_Other``).

    Maps to ``problem``.
    """
    return {
        "orderNo": order_no,
        "has": True,
        "displayStatus": "1",
        "lastContent": "Delivery Attempt Failed",
        "lastEventCode": "DeliveryFail_Other",
        "lastStatus": "208",
        "lastUpdate": "2026-07-27 16:45:00",
        "orgCountry": "CN",
        "dstCountry": "NL",
        "trackingNumber": "3SNL000000001",
        "transitDays": None,
        "result": {
            "origin": {
                "items": [
                    event(
                        "2067",
                        "DeliveryStationArrival",
                        "2026-07-26 08:00:00",
                        "+02:00",
                    ),
                    event("2071", "OutForDelivery", "2026-07-27 08:30:00", "+02:00"),
                    event(
                        "208",
                        "DeliveryFail_Other",
                        "2026-07-27 16:45:00",
                        "+02:00",
                        content="Delivery attempt failed - recipient not available",
                    ),
                ]
            }
        },
    }


def pickup_sample(order_no: str = PICKUP_CODE) -> dict:
    """A parcel ready to collect, newest event ``207 ReadyForPickup``.

    Synthetic, built from the pair confirmed live 2026-08-13 (not part of the
    21-parcel research capture). Maps to ``at_pickup_point``.
    """
    return {
        "orderNo": order_no,
        "has": True,
        "displayStatus": "1",
        "lastContent": "Ready For Pickup",
        "lastEventCode": "ReadyForPickup",
        "lastStatus": "207",
        "lastUpdate": "2026-08-12 09:00:00",
        "orgCountry": "CN",
        "dstCountry": "NL",
        "trackingNumber": None,
        "transitDays": None,
        "result": {
            "origin": {
                "items": [
                    event(
                        "2067",
                        "DeliveryStationArrival",
                        "2026-08-11 08:00:00",
                        "+02:00",
                    ),
                    event("207", "ReadyForPickup", "2026-08-12 09:00:00", "+02:00"),
                ]
            }
        },
    }


def returning_sample(order_no: str = RETURNING_CODE) -> dict:
    """A parcel being sent back to sender, newest event ``210 Returned``.

    Synthetic, built from the pair confirmed live 2026-08-13 (not part of the
    21-parcel research capture). Maps to ``returning``, distinct from the
    ``208``/``20899`` failure events that precede it.
    """
    return {
        "orderNo": order_no,
        "has": True,
        "displayStatus": "1",
        "lastContent": "Returned",
        "lastEventCode": "Returned",
        "lastStatus": "210",
        "lastUpdate": "2026-07-28 10:15:00",
        "orgCountry": "CN",
        "dstCountry": "NL",
        "trackingNumber": "3SNL000000001",
        "transitDays": None,
        "result": {
            "origin": {
                "items": [
                    event("2071", "OutForDelivery", "2026-07-27 08:30:00", "+02:00"),
                    event(
                        "208",
                        "DeliveryFail_Other",
                        "2026-07-27 16:45:00",
                        "+02:00",
                    ),
                    event("210", "Returned", "2026-07-28 10:15:00", "+02:00"),
                ]
            }
        },
    }


def jsonp_envelope(parcel: dict) -> str:
    """Wrap one ``data[0]`` entry in SunYou's ``searchCallback(...)`` JSONP body."""
    body = {"data": [parcel], "message": "success", "status": 1}
    return f"searchCallback({json.dumps(body)})"
