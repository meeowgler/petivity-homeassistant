"""Diagnostics must not identify the account."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.petivity.diagnostics import async_get_config_entry_diagnostics

from .conftest import CAT_TOM, CAT_ZOE, HOUSEHOLD, MACHINE_A, MACHINE_B, household, visit


async def test_diagnostics_are_anonymous(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    # The visit must fall on "today" for the daily counts checked below.
    freezer.move_to("2026-09-27T16:00:00+00:00")
    await hass.config.async_set_time_zone("UTC")
    events = [visit("RXZlbnQ6c2VjcmV0", "2026-09-27T14:00:00", MACHINE_A, CAT_TOM)]
    mock_client.async_get_household.return_value = household({CAT_TOM: events})
    mock_client.async_get_events.return_value = events
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(diag)

    for secret in (
        HOUSEHOLD["id"], MACHINE_A, MACHINE_B, CAT_TOM, CAT_ZOE, "RXZlbnQ6c2VjcmV0",
        "VC00LT1AAAA0001", "Office", "Hallway", "Tom", "Zoe", "id.tok.en", "refresh-token",
    ):
        assert secret not in text, secret

    data = diag["data"]
    assert data["household_id"] == "household"
    assert set(data["machines"]) == {"monitor_1", "monitor_2"}
    assert set(data["cats"]) == {"cat_1", "cat_2"}
    # Relationships survive: the cat's visit points at the same aliases.
    cat = data["cats"]["cat_1"]
    node = cat["latestEvents"]["edges"][0]["node"]
    assert node["machine"]["id"] == "monitor_1"
    assert node["normalisedClassification"]["cat"]["id"] == "cat_1"
    assert node["id"].startswith("id_")
    # Useful values are kept.
    assert data["cat_today"]["cat_1"]["visits"] == 1
    assert diag["entry"]["id_token"] == "**REDACTED**"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
