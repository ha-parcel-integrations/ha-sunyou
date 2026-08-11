"""Constants for the SunYou parcel tracker integration."""
from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "sunyou"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"               # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"               # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"   # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"     # Ready to collect at a pickup location
    DELIVERED = "delivered"                 # Handed over
    RETURNING = "returning"                 # Failed delivery, going back to sender
    PROBLEM = "problem"                     # Carrier reports an exception/issue
    UNKNOWN = "unknown"                     # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping this carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Which optional contract fields this carrier's API actually populates — feeds
# the comparison table on the docs site. Keep in lockstep with
# normalize_parcel() in parcels.py: everything not listed here comes back as a
# literal None there. SunYou exposes nothing about the last leg — no
# sender/receiver/delivery window/pickup point/weight/dimensions.
CAPABILITIES = frozenset({"url", "history"})

# SunYou's public tracking endpoint (SYPost) — the one sypost.net's own
# consumer tracking page calls. Confirmed live against 21 real parcels
# (2026-08-05). Full mechanics: carrier-research/api/sunyou/tracking.md.
#
# * Keyless, **JSONP** — the body is wrapped in ``searchCallback(...)``, not
#   bare JSON. ``toLanguage=en_US`` fixes the human-readable event text so we
#   never map on it (it localises; the codes underneath do not).
# * **No batching.** ``data`` is a list but is always length 1; a
#   comma-separated ``trackNumber`` comes back as one literal not-found string.
#   One request per parcel.
# * HTTP 200 always — even for an unknown number. Branch on ``data[0].has``,
#   never on the HTTP status or the ``status: 1`` / ``message: "success"``
#   envelope, which says nothing about the parcel.
TRACKING_API_URL = (
    "https://www.sypost.net/queryTrack?trackNumber={tracking_code}&toLanguage=en_US"
)
TRACKING_URL = "https://sypost.net/search?orderNo={tracking_code}"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Refresh interval (minutes) controls how often the coordinator polls SunYou.
#
# ``configurable`` (not ``fixed``) is a deliberate choice, not the template
# default: ~70 probe requests in a few minutes drew no 429, no block and no
# captcha (rate_limit: none-observed). That is *unlike* Cainiao, which is
# generated ``--interval fixed`` because Alibaba soft-bans unusual traffic and
# an IP ban there costs the user every AliExpress service — that reasoning
# does not transfer to SunYou, a standalone operator.
#
# The default still sits on the conservative side of the options, though:
# SunYou has **no batching** (one HTTP request per tracked parcel per poll,
# where Cainiao does one request for ten), so a household tracking several
# parcels fans out more traffic per poll here than anywhere else in the suite.
# If a user ever reports a 429, ``--interval fixed`` is the documented
# fallback and Cainiao (``custom_components/cainiao/const.py``) is the worked
# example of how to regenerate with it.
CONF_REFRESH_INTERVAL = "refresh_interval"
REFRESH_INTERVAL_OPTIONS = (15, 30, 60, 120, 240)
DEFAULT_REFRESH_INTERVAL = 60

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
