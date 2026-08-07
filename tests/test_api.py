"""Tests for the SunYou API client — keyless JSONP, has:false is not-found."""
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.sunyou.api import SunYouApiClient, SunYouApiError

from .payloads import (
    delivered_sample,
    jsonp_envelope,
    not_found_sample,
)

CODE = "SYAE006809461"


def _session_returning(status: int, text: str) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.text = AsyncMock(return_value=text)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_parcel_on_success():
    session = _session_returning(200, jsonp_envelope(delivered_sample(CODE)))
    client = SunYouApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["orderNo"] == CODE
    assert parcel["has"] is True
    # the tracking code ends up in the URL
    assert CODE in session.get.call_args[0][0]


async def test_get_parcel_returns_none_when_not_found():
    """``has: false`` is a normal state, not an error — HTTP 200 either way."""
    client = SunYouApiClient(
        _session_returning(200, jsonp_envelope(not_found_sample()))
    )
    assert await client.async_get_parcel("SY000000000") is None


async def test_get_parcel_raises_when_body_is_unreadable():
    response = AsyncMock()
    response.status = 200
    response.text = AsyncMock(side_effect=UnicodeDecodeError("utf-8", b"", 0, 1, "boom"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    client = SunYouApiClient(session)
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = SunYouApiClient(_session_returning(500, ""))
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_unparseable_jsonp():
    """Missing the ``searchCallback(...)`` wrapper entirely is not valid JSONP."""
    client = SunYouApiClient(_session_returning(200, "not jsonp at all"))
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_malformed_json_inside_wrapper():
    client = SunYouApiClient(_session_returning(200, "searchCallback({not json})"))
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = SunYouApiClient(_session_returning(200, "searchCallback([1, 2, 3])"))
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_missing_data_list():
    client = SunYouApiClient(
        _session_returning(200, 'searchCallback({"message":"success","status":1})')
    )
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_empty_data_list():
    client = SunYouApiClient(
        _session_returning(
            200, 'searchCallback({"data":[],"message":"success","status":1})'
        )
    )
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_when_data_entry_is_not_an_object():
    client = SunYouApiClient(
        _session_returning(
            200, 'searchCallback({"data":["oops"],"message":"success","status":1})'
        )
    )
    with pytest.raises(SunYouApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_ignores_envelope_success_flag():
    """``status: 1`` / ``message: "success"`` says nothing about the parcel —
    only ``data[0].has`` does. A success envelope wrapping ``has: false`` is
    still "not found", not a crash."""
    client = SunYouApiClient(
        _session_returning(200, jsonp_envelope(not_found_sample()))
    )
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_unwraps_by_index_not_by_replace():
    """The trailing ``result`` object means the body ends in ``})`` twice
    over — a ``.replace("})", "}")`` unwrap (the trap one OSS client falls
    into) would corrupt this body. Index-based slicing must not."""
    session = _session_returning(200, jsonp_envelope(delivered_sample(CODE)))
    client = SunYouApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["result"]["origin"]["items"][-1]["eventCode"] == "Delivered"


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = SunYouApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
