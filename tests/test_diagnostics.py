"""Tests for SunYou diagnostics."""
from unittest.mock import MagicMock

from custom_components.sunyou.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may
    survive. Covers the plan's exact redaction list: ``orderNo``,
    ``trackingNumber`` (the last-mile carrier's own public-lookup number,
    exactly like Cainiao's ``realMailNo``), the ``carrier*`` trio and the
    depot ids."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "SYAE006809461"}]}
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "SYAE006809461",
            "sender": None,
            "receiver": None,
            "status": "out_for_delivery",
            "raw": {
                "orderNo": "SYAE006809461",
                "trackingNumber": "SU014952148MI",
                "carrierName": "meest",
                "carrierPhone": "+302100000000",
                "carrierWebsite": "http://meest-group.com/",
                "lastOffice": "1622",
                "orgCountry": "CN",
                "dstCountry": "GR",
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {"incoming_active": 1, "delivered": 0}
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["orderNo"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackingNumber"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["carrierName"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["carrierPhone"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["carrierWebsite"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["lastOffice"] == "**REDACTED**"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"
    assert result["incoming"][0]["raw"]["orgCountry"] == "CN"
    assert result["incoming"][0]["raw"]["dstCountry"] == "GR"
