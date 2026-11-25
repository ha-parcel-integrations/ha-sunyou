"""Tests for the parcel contents field, its store and the update_contents action."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunyou.const import (
    CONF_CONTENTS,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)
from custom_components.sunyou.contents import (
    CONTENTS_MAX_LEN,
    CONTENTS_STORE_MAX_CODES,
    ContentsStore,
    clean_contents,
    join_contents,
    normalize_code,
)

from .payloads import active_sample

CODE = "EXAMPLE999999"
FETCH = "custom_components.sunyou.api.SunYouApiClient.async_get_parcel"


async def _fetch(self, code):
    return active_sample(code)


async def _setup(hass, codes=(CODE,), stored=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: [{CONF_TRACKING_CODE: c} for c in codes]},
    )
    entry.add_to_hass(hass)
    if stored is not None:
        with (
            patch(
                "homeassistant.helpers.storage.Store.async_load",
                new=AsyncMock(return_value={"by_code": stored}),
            ),
            patch(FETCH, new=_fetch),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id)
    else:
        with patch(FETCH, new=_fetch):
            assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entry.runtime_data.coordinator


def _contents(coordinator):
    return {p["barcode"]: p["contents"] for p in coordinator.data}


async def _call(hass, service, data):
    await hass.services.async_call(DOMAIN, service, data, blocking=True)
    await hass.async_block_till_done()


def test_clean_trims_collapses_and_caps():
    assert clean_contents("  Running \n shoes  ") == "Running shoes"
    assert clean_contents("   ") is None
    assert clean_contents(5) is None
    assert clean_contents("x" * 500) == "x" * CONTENTS_MAX_LEN
    assert clean_contents("n/a", frozenset({"n/a"})) is None


def test_join_drops_empties_and_repeats():
    assert join_contents(["Shoes", " ", "Shoes", "Socks"]) == "Shoes; Socks"
    assert join_contents([None, ""]) is None


def test_normalize_code():
    assert normalize_code(" ab1 ") == "AB1"


def test_store_set_replace_clear_and_cap(hass):
    store = ContentsStore(hass, "e1")
    assert store.async_set("a", "One") is True
    assert store.async_set("A", "One") is False
    assert store.async_set("a", "Two") is True
    assert store.get(" a ") == "Two"
    assert store.async_set("a", "  ") is True
    assert store.get("a") is None
    assert store.async_set("a", None) is False
    for index in range(CONTENTS_STORE_MAX_CODES + 1):
        store.async_set(f"c{index}", "x")
    assert store.get("c0") is None
    assert store.get("c1") == "x"
    assert store.get(f"c{CONTENTS_STORE_MAX_CODES}") == "x"


async def test_store_load_tolerates_missing_and_bad_data(hass):
    store = ContentsStore(hass, "e1")
    await store.async_load()
    assert store.get("a") is None
    for data in ({"by_code": "nope"}, {"by_code": {"a": 5, "b": " Hat "}}):
        with patch(
            "homeassistant.helpers.storage.Store.async_load",
            new=AsyncMock(return_value=data),
        ):
            await store.async_load()
    assert store.get("a") is None
    assert store.get("b") == "Hat"


async def test_remove_clears_store(hass):
    store = ContentsStore(hass, "e1")
    store.async_set("a", "x")
    await store.async_remove()
    assert store.get("a") is None


async def test_old_entry_without_a_store_works(hass):
    _, coordinator = await _setup(hass)
    assert _contents(coordinator) == {CODE: None}


async def test_stored_value_overlays_after_setup(hass):
    _, coordinator = await _setup(hass, stored={CODE: "Running shoes"})
    assert _contents(coordinator) == {CODE: "Running shoes"}


async def test_remove_entry_deletes_the_store(hass):
    entry, _ = await _setup(hass)
    with patch(
        "homeassistant.helpers.storage.Store.async_remove", new=AsyncMock()
    ) as remove:
        await hass.config_entries.async_remove(entry.entry_id)
    remove.assert_awaited_once()


async def test_update_contents_set_replace_clear_without_refresh(hass):
    _, coordinator = await _setup(hass)
    with (
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
        patch(FETCH, new=AsyncMock()) as fetch,
        patch.object(coordinator, "async_update_listeners") as listeners,
    ):
        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: " Hat "}
        )
        assert _contents(coordinator) == {CODE: "Hat"}
        assert listeners.call_count == 1

        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: "Hat"}
        )
        assert listeners.call_count == 1

        await _call(
            hass, "update_contents", {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: "Scarf"}
        )
        assert _contents(coordinator) == {CODE: "Scarf"}
        assert listeners.call_count == 2

        await _call(hass, "update_contents", {CONF_TRACKING_CODE: CODE})
        assert _contents(coordinator) == {CODE: None}
        assert listeners.call_count == 3

        await _call(hass, "update_contents", {CONF_TRACKING_CODE: CODE})
        assert listeners.call_count == 3
    refresh.assert_not_called()
    fetch.assert_not_called()


async def test_update_contents_on_delivered_parcel(hass):
    _, coordinator = await _setup(hass)
    coordinator.delivered = [{"barcode": "EXAMPLE111111", "contents": None}]
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: "EXAMPLE111111", CONF_CONTENTS: "Book"},
    )
    assert coordinator.delivered[0]["contents"] == "Book"


async def test_update_contents_for_untracked_code(hass):
    entry, coordinator = await _setup(hass)
    await _call(
        hass,
        "update_contents",
        {CONF_TRACKING_CODE: "example-000 000", CONF_CONTENTS: "Lamp"},
    )
    assert coordinator.contents_store.get("EXAMPLE000000") == "Lamp"
    assert len(entry.options[CONF_PARCELS]) == 1


async def test_update_contents_rejects_invalid_code(hass):
    await _setup(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "update_contents",
            {CONF_TRACKING_CODE: "", CONF_CONTENTS: "x"},
            blocking=True,
        )


async def test_actions_need_a_loaded_entry(hass):
    entry, _ = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    for service, data in (
        ("update_contents", {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: "x"}),
        ("track_parcel", {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: "x"}),
    ):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, service, data, blocking=True)
    # untrack still works on an entry that is not loaded
    await hass.services.async_call(
        DOMAIN, "untrack_parcel", {CONF_TRACKING_CODE: CODE}, blocking=True
    )
    await hass.async_block_till_done()
    entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_track_parcel_with_contents_on_tracked_code_does_not_refresh(hass):
    _, coordinator = await _setup(hass)
    with (
        patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh,
        patch(FETCH, new=AsyncMock()) as fetch,
        patch.object(coordinator, "async_update_listeners") as listeners,
    ):
        data = {CONF_TRACKING_CODE: CODE, CONF_CONTENTS: "Hat"}
        await _call(hass, "track_parcel", data)
        await _call(hass, "track_parcel", data)
        assert listeners.call_count == 1
    refresh.assert_not_called()
    fetch.assert_not_called()
    assert _contents(coordinator) == {CODE: "Hat"}


async def test_track_new_code_with_contents_refreshes_via_options(hass):
    entry, coordinator = await _setup(hass)
    with patch.object(coordinator, "async_request_refresh", new=AsyncMock()) as refresh:
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE222222", CONF_CONTENTS: "Lamp"},
        )
    refresh.assert_awaited_once()
    assert coordinator.contents_store.get("EXAMPLE222222") == "Lamp"
    assert len(entry.options[CONF_PARCELS]) == 2


async def test_new_code_picks_up_stored_contents_on_refresh(hass):
    _, coordinator = await _setup(hass)
    with patch(FETCH, new=_fetch):
        await _call(
            hass,
            "track_parcel",
            {CONF_TRACKING_CODE: "EXAMPLE222222", CONF_CONTENTS: "Lamp"},
        )
    assert _contents(coordinator)["EXAMPLE222222"] == "Lamp"


async def test_track_without_contents_leaves_store_alone(hass):
    _, coordinator = await _setup(hass, stored={CODE: "Hat"})
    await _call(hass, "track_parcel", {CONF_TRACKING_CODE: CODE})
    assert coordinator.contents_store.get(CODE) == "Hat"


async def test_untrack_drops_contents(hass):
    _, coordinator = await _setup(hass, stored={CODE: "Hat"})
    await _call(hass, "untrack_parcel", {CONF_TRACKING_CODE: CODE})
    assert coordinator.contents_store.get(CODE) is None
