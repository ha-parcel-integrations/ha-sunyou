"""SunYou (SYPost) public tracking API client.

One endpoint, keyless, **JSONP** — SunYou's ``queryTrack`` wraps its response
in ``searchCallback(...)`` with a hardcoded callback name, so there is no way
to ask for bare JSON. The not-found signal lives in the body (``data[0].has``),
never in the HTTP status or the ``status: 1`` / ``message: "success"``
envelope, which is present on every response including a bogus tracking
number.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .const import TRACKING_API_URL

_LOGGER = logging.getLogger(__name__)


class SunYouApiError(Exception):
    """Raised when a SunYou API call returns an unexpected response."""

    def __init__(self, detail: str) -> None:
        """Store the status code that triggered the error."""
        super().__init__(f"SunYou API request failed: {detail}")
        self.detail = detail


class SunYouApiClient:
    """Client for the public SunYou (SYPost) tracking endpoint.

    No authentication: the endpoint is keyed on the tracking code alone. It
    always answers HTTP 200 with a JSONP body::

        searchCallback({"data":[{"has":true,"orderNo":"...", ...}],
                        "message":"success","status":1})

    or, for an unknown/not-yet-scanned code::

        searchCallback({"data":[{"has":false,"orderNo":"...", "displayStatus":"0"}],
                        "message":"success","status":1})

    ``data`` is a list but is always length 1 — SunYou has no batching, and a
    comma-separated ``trackNumber`` comes back as one literal not-found string
    rather than being split. One request per parcel.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's tracking details.

        Returns the ``data[0]`` dict for a known parcel, or ``None`` when
        ``has`` is false — which is also what a not-yet-scanned parcel gets,
        so it is a normal, expected state and never an error. Any other
        malformed envelope raises :class:`SunYouApiError`; network errors
        propagate as ``aiohttp.ClientError``.
        """
        url = TRACKING_API_URL.format(tracking_code=tracking_code)
        async with self._session.get(url) as response:
            if response.status != 200:
                raise SunYouApiError(f"HTTP {response.status}")
            try:
                # Served as text/plain, not application/json — this is JSONP,
                # not JSON, so we read the raw body ourselves rather than
                # asking aiohttp to decode it.
                body = await response.text()
            except (aiohttp.ClientError, UnicodeDecodeError) as err:
                raise SunYouApiError(f"unreadable body ({err})") from err

        payload = _unwrap_jsonp(body)

        if not isinstance(payload, dict):
            raise SunYouApiError("unexpected body (not a JSON object)")

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            raise SunYouApiError("unexpected body (no data list)")

        parcel = data[0]
        if not isinstance(parcel, dict):
            raise SunYouApiError("unexpected body (data[0] is not an object)")

        # ``has`` is the only reliable not-found signal — ``status: 1`` /
        # ``message: "success"`` is the envelope and says nothing about
        # whether SunYou actually knows this number.
        if not parcel.get("has"):
            return None
        return parcel


def _unwrap_jsonp(body: str) -> Any:
    """Strip SunYou's hardcoded ``searchCallback(...)`` wrapper and parse it.

    Unwraps **by index**, not by ``str.replace``: one of the OSS clients this
    endpoint was reverse-engineered from does
    ``.replace("searchCallback(", "").replace("})", "}")``, which corrupts any
    body whose JSON ends in a nested object — SunYou's own payload does,
    thanks to the trailing ``result`` object. Slicing between the first ``(``
    and the last ``)`` has no such trap.
    """
    try:
        inner = body[body.index("(") + 1 : body.rindex(")")]
        return json.loads(inner)
    except (ValueError, json.JSONDecodeError) as err:
        raise SunYouApiError(f"unparseable JSONP body ({err})") from err
