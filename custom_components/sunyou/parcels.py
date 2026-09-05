"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping apart from the coordinator (which is nearly
identical everywhere), and it makes the mapping trivially unit-testable
without spinning up HA.

SunYou-specific parts: :data:`_STATUS_MAP`, :func:`_collect_events`,
:func:`_newest`, :func:`build_history` and :func:`normalize_parcel`. The
timestamp combination (:func:`_event_timestamp`) is SunYou-specific too —
its naive ``createTime`` plus a sibling per-event ``timeZone`` is not the
epoch-ms/ISO shape the suite usually sees. The sort contract and the
delivered filter at the bottom are suite-wide machinery, kept identical
across carriers on purpose.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet, or a payload shape we have
# not seen before. Must point at this carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-sunyou/issues/new"
    "?template=unrecognised_status.yml"
)

# SunYou's per-event ``status`` (numeric, as a string) -> canonical
# ParcelStatus. ``eventCode`` is the more legible sibling of the same pair
# (kept as ``raw_status``), but the map is keyed on ``status`` because it is
# the one that will still match if SunYou ever retitles the English label.
#
# **Never map from ``displayStatus``.** It is a coarse three-value UI bucket,
# not a status — on the wire, all 16 delivered parcels in the research capture
# returned ``displayStatus: "4"`` (the value a reconstructed table had read as
# "undelivered / problem") and ``"5"`` never appeared once. `displayStatus`
# does not even reach ``raw`` (see :func:`normalize_parcel`) so nobody is
# tempted to resurrect that reading.
#
# The numbering is systematic (``2xx`` = destination-country events, ``20899``
# a "_Second" retry of ``208``, ``2071``/``2072`` a first/second delivery
# attempt) which implies unseen siblings — most plausibly more failure reasons
# and a return code. An unrecognised pair still surfaces as ``unknown`` plus a
# one-shot warning rather than guessing.
#
# ``at_pickup_point`` (``207``/``ReadyForPickup``), the ``206xx`` clearance
# sibling ``20622``/``ClearanceInspect``, a low ``200``/``InTransit`` and
# ``210``/``Returned`` were all confirmed live on 2026-08-13, from a user's
# WARNING log rather than the 21-parcel research capture. Two things the doc
# only speculated about are now settled: the earlier "probably no waiting
# state" reading is superseded (Cainiao's ``GTMS_STA_SIGNED`` rule applies —
# a pickup code is ``at_pickup_point``, never ``delivered``), and ``210`` is
# the "return code" sibling the ``208``/``20899`` numbering implied — mapped
# to ``RETURNING``, not ``PROBLEM``, since the enum has a dedicated member for
# "failed delivery, going back to sender" and ``208`` already covers the
# failure event itself.
#
# The same session also confirmed two ``209`` delivery-location siblings
# (``20903``/``Delivered_Mailbox``, ``20906``/``Delivered_Doorstep`` ->
# ``DELIVERED``, same shape as ``20899``/``DeliveryFail_SecondOther``) plus
# four more ``208xx`` delivery-fail *reasons* (``20801``/``Retry``,
# ``20803``/``Contact``, ``20806``/``NoService``, ``20810``/``Return``) and a
# bare ``220``/``Exception``. All five map to ``PROBLEM``, not ``RETURNING``
# — each is still the *attempt-failed* event (``DeliveryFail_*`` / a generic
# exception), not the "now in motion back to sender" event that
# ``210``/``Returned`` is. Do not read ``DeliveryFail_Return`` as a ``210``
# synonym: it is the failure reason, not the return leg.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "101": ParcelStatus.REGISTERED,        # PreAlert
    "102": ParcelStatus.REGISTERED,        # InboundScan
    "105": ParcelStatus.IN_TRANSIT,        # Dispatch
    "106": ParcelStatus.IN_TRANSIT,        # PortDeparture
    "200": ParcelStatus.IN_TRANSIT,        # InTransit
    "201": ParcelStatus.IN_TRANSIT,        # TransitCountryArrival
    "205": ParcelStatus.IN_TRANSIT,        # TransitCountryDeparted
    "206": ParcelStatus.IN_TRANSIT,        # DestinationAirPortArrival
    "20622": ParcelStatus.IN_TRANSIT,      # ClearanceInspect
    "2061": ParcelStatus.IN_TRANSIT,       # HandoverLastMile
    "2062": ParcelStatus.IN_TRANSIT,       # ClearanceProcess
    "2064": ParcelStatus.IN_TRANSIT,       # ClearanceSuccessed (SunYou's own spelling)
    "2067": ParcelStatus.IN_TRANSIT,       # DeliveryStationArrival
    "2068": ParcelStatus.OUT_FOR_DELIVERY, # DeliveryStationDepart
    "207": ParcelStatus.AT_PICKUP_POINT,   # ReadyForPickup
    "2071": ParcelStatus.OUT_FOR_DELIVERY, # OutForDelivery
    "2072": ParcelStatus.OUT_FOR_DELIVERY, # OutForDelivery_Second
    "208": ParcelStatus.PROBLEM,           # DeliveryFail_Other
    "20801": ParcelStatus.PROBLEM,         # DeliveryFail_Retry
    "20802": ParcelStatus.PROBLEM,         # DeliveryFail_Address
    "20803": ParcelStatus.PROBLEM,         # DeliveryFail_Contact
    "20806": ParcelStatus.PROBLEM,         # DeliveryFail_NoService
    "20810": ParcelStatus.PROBLEM,         # DeliveryFail_Return (the reason, not the return leg)
    "20899": ParcelStatus.PROBLEM,         # DeliveryFail_SecondOther
    "209": ParcelStatus.DELIVERED,         # Delivered
    "20903": ParcelStatus.DELIVERED,       # Delivered_Mailbox
    "20906": ParcelStatus.DELIVERED,       # Delivered_Doorstep
    "210": ParcelStatus.RETURNING,         # Returned
    "220": ParcelStatus.PROBLEM,           # Exception
}

# ---------------------------------------------------------------------------
# One-shot pre-1.0 warnings (status_vocab is not provably closed — see
# tracking.md). Each condition warns exactly once per HA session, not once
# per occurrence, so a chatty parcel doesn't flood the log.
# ---------------------------------------------------------------------------

_unmapped_statuses_logged: set[str] = set()
_unexpected_legs_logged: set[str] = set()
_create_time_parse_failure_logged = False
_missing_timezone_logged = False


def _warn_unmapped_status(status: str, event_code: str | None) -> None:
    """Log an unmapped ``status``/``eventCode`` pair once."""
    if status in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(status)
    _LOGGER.warning(
        "Unrecognised SunYou status — help us map it. Open an issue and "
        "paste this line: %s\n  status=%s eventCode=%s -> reported as 'unknown'",
        NEW_ISSUE_URL,
        status,
        event_code,
    )


def _warn_unexpected_leg(leg_name: str, item_count: int) -> None:
    """Log a ``result`` leg other than ``origin`` once per leg name.

    Only ``origin`` has ever been observed (across 21 research parcels to 13
    destination countries) — ``destination`` is read by a source client but
    was never seen, so this is genuinely new territory worth a report even
    though the code already handles it safely.
    """
    if leg_name in _unexpected_legs_logged:
        return
    _unexpected_legs_logged.add(leg_name)
    _LOGGER.warning(
        "SunYou parcel carries a `result` leg other than 'origin' — please "
        "help us confirm this shape. Open an issue and paste this line: "
        "%s\n  leg=%s item_count=%d",
        NEW_ISSUE_URL,
        leg_name,
        item_count,
    )


def _warn_create_time_parse_failure(value: str) -> None:
    """Log a ``createTime`` that fails the expected format, once."""
    global _create_time_parse_failure_logged
    if _create_time_parse_failure_logged:
        return
    _create_time_parse_failure_logged = True
    _LOGGER.warning(
        "SunYou event createTime did not match '%%Y-%%m-%%d %%H:%%M:%%S' "
        "(value=%r) — the event is dropped rather than guessed at. Open an "
        "issue and paste this line: %s",
        value,
        NEW_ISSUE_URL,
    )


def _warn_missing_timezone(create_time: str) -> None:
    """Log an event with no ``timeZone`` sibling, once."""
    global _missing_timezone_logged
    if _missing_timezone_logged:
        return
    _missing_timezone_logged = True
    _LOGGER.warning(
        "SunYou event has no timeZone sibling (createTime=%r) — falling back "
        "to UTC, which can put the timestamp up to 8 hours out. Open an "
        "issue and paste this line: %s",
        create_time,
        NEW_ISSUE_URL,
    )


def _lookup(status: str | None, event_code: str | None) -> ParcelStatus | None:
    """Look ``status`` up in :data:`_STATUS_MAP`, warning once if unknown."""
    if not status:
        return None
    key = str(status).strip()
    mapped = _STATUS_MAP.get(key)
    if mapped is None:
        _warn_unmapped_status(key, event_code)
    return mapped


def map_parcel_status(status: str | None, event_code: str | None = None) -> ParcelStatus:
    """Map a ``status`` code to a canonical :class:`ParcelStatus`.

    ``None`` (no events at all — the normal state for a just-registered
    parcel that has not been scanned yet) reports ``unknown`` silently; an
    unrecognised code reports ``unknown`` with a one-shot warning.
    """
    return _lookup(status, event_code) or ParcelStatus.UNKNOWN


def map_event_status(status: str | None, event_code: str | None = None) -> ParcelStatus | None:
    """Map a history entry's ``status`` code to a canonical status, or ``None``.

    Unmapped codes keep ``status: null`` on the history entry (rather than
    ``unknown``, so a consumer can tell "no mapping" from "mapped to
    unknown") and warn once, reusing the parcel-status one-shot set.
    """
    return _lookup(status, event_code)


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Event timestamps — SunYou's own naive-datetime-plus-sibling-offset shape.
# ---------------------------------------------------------------------------

_TIMEZONE_RE = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def _parse_timezone_offset(value: str | None) -> timezone | None:
    """Parse one event's ``timeZone`` (e.g. ``"+08:00"``) into a ``timezone``."""
    if not value:
        return None
    match = _TIMEZONE_RE.match(value)
    if not match:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(-delta if sign == "-" else delta)


def _event_timestamp(raw_event: dict) -> str | None:
    """Combine one event's naive ``createTime`` with its own ``timeZone``.

    ``createTime`` is ``"YYYY-MM-DD HH:MM:SS"`` with **no offset in the
    string** — each event carries its own ``timeZone`` instead (origin-leg
    events read ``+08:00``; destination-country events carry the local
    offset). ``lastUpdate`` has no ``timeZone`` sibling at all, which is why
    this only ever works event-by-event, never from a top-level field plus a
    constant — parsing ``createTime`` as UTC would put a delivery up to 8
    hours out.

    Returns ``None`` (and warns once) when ``createTime`` fails to parse.
    Returns a UTC-anchored timestamp (and warns once) when ``timeZone`` is
    missing, rather than dropping the event outright.
    """
    create_time = raw_event.get("createTime")
    if not create_time:
        return None
    try:
        naive = datetime.strptime(str(create_time), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        _warn_create_time_parse_failure(str(create_time))
        return None

    offset = _parse_timezone_offset(raw_event.get("timeZone"))
    if offset is None:
        _warn_missing_timezone(str(create_time))
        offset = timezone.utc
    return naive.replace(tzinfo=offset).isoformat()


def _collect_events(raw: dict) -> list[dict]:
    """Concatenate every leg's ``items`` under ``result``.

    Iterates ``result``'s keys and concatenates — **never** indexes
    ``result["origin"]`` directly. ``origin`` is the only leg ever observed
    (21 parcels, 13 destination countries) and already carries the whole
    timeline through customs, out-for-delivery and delivery; ``destination``
    is read by a source client but was never seen. Any leg name other than
    ``origin`` is logged once via :func:`_warn_unexpected_leg` — the
    concatenation itself is just a cheap guard, not evidence such a leg
    exists.
    """
    result = raw.get("result")
    if not isinstance(result, dict):
        return []
    events: list[dict] = []
    for leg_name, leg in result.items():
        items = leg.get("items") if isinstance(leg, dict) else None
        if not isinstance(items, list):
            continue
        leg_events = [item for item in items if isinstance(item, dict)]
        if leg_name != "origin":
            _warn_unexpected_leg(leg_name, len(leg_events))
        events.extend(leg_events)
    return events


def _newest(events: list[dict]) -> tuple[dict, str] | None:
    """Return the ``(event, iso_timestamp)`` pair with the latest parsed time.

    Events with an unparseable timestamp are skipped for this purpose (they
    already triggered a one-shot warning in :func:`_event_timestamp`) rather
    than being treated as "newest" by list position.
    """
    best: tuple[datetime, dict, str] | None = None
    for raw_event in events:
        timestamp = _event_timestamp(raw_event)
        if timestamp is None:
            continue
        parsed = parse_iso(timestamp)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, raw_event, timestamp)
    if best is None:
        return None
    return best[1], best[2]


def build_history(
    events: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from SunYou's event list.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``raw_status`` is ``eventCode`` (the more
    legible sibling of the numeric ``status``). Sorted oldest -> newest and
    capped to the most recent ``max_events``.

    Takes the already-concatenated event list (see :func:`_collect_events`),
    not the raw payload — callers that also need the newest event for
    ``normalize_parcel`` build the list once and reuse it.
    """
    parseable: list[tuple[datetime, dict]] = []
    for raw_event in events or []:
        if not isinstance(raw_event, dict):
            continue
        timestamp = _event_timestamp(raw_event)
        if not timestamp:
            continue
        entry = {
            "timestamp": timestamp,
            "status": map_event_status(raw_event.get("status"), raw_event.get("eventCode")),
            "raw_status": raw_event.get("eventCode"),
        }
        parsed = parse_iso(timestamp)
        if parsed is not None:
            parseable.append((parsed, entry))
    parseable.sort(key=lambda item: item[0])
    return [entry for _, entry in parseable][-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key SunYou does not expose is
    ``None`` — never omitted.

    What SunYou does not give us, and why the ``None``s are intentional:

    * **``sender`` / ``receiver``** — the endpoint is anonymous, keyed on the
      tracking code alone.
    * **``planned_from`` / ``planned_to``** — nothing in the payload is a
      forecast. ``transitDays`` is *elapsed* days on a delivered parcel, not
      an ETA, so it is not read as one.
    * **``pickup_point``** — ``at_pickup_point`` is mapped (see
      :data:`_STATUS_MAP`) but no event ever carries a location string, so the
      field stays ``None`` even while ``status`` is ``at_pickup_point``.
    * **``weight`` / ``dimensions``** — never exposed on this endpoint.

    ``barcode`` is ``orderNo`` — **not** ``trackingNumber``, which is the
    last-mile carrier's own number for the handed-off parcel (kept under
    ``raw`` only). Getting this backwards makes every parcel change identity
    at handoff.

    ``status``/``raw_status``/``delivered``/``delivered_at`` all come from the
    **newest event** across every ``result`` leg (see :func:`_collect_events`
    and :func:`_newest`) — never from the top-level ``displayStatus``, which
    is a coarse UI bucket proven wrong on the wire (see :data:`_STATUS_MAP`).
    ``displayStatus`` is stripped out of ``raw`` entirely, on top of never
    being read, so nobody downstream is tempted to resurrect it either.
    """
    order_no = raw.get("orderNo")
    events = _collect_events(raw)
    newest = _newest(events)

    if newest is not None:
        newest_event, newest_timestamp = newest
        status = map_parcel_status(newest_event.get("status"), newest_event.get("eventCode"))
        raw_status = newest_event.get("eventCode")
    else:
        status = ParcelStatus.UNKNOWN
        raw_status = None
        newest_timestamp = None

    delivered = status is ParcelStatus.DELIVERED

    return {
        "carrier": "SunYou",
        "barcode": order_no,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": raw_status,
        "delivered": delivered,
        "delivered_at": newest_timestamp if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": tracking_url(order_no),
        "weight": None,
        "dimensions": None,
        "history": build_history(events) if include_history else None,
        "raw": {key: value for key, value in raw.items() if key != "displayStatus"},
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
