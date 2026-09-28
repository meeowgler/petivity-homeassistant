"""Setup, sensor and event tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.petivity.api import PetivityAuthError
from custom_components.petivity.const import CONF_ID_TOKEN, SCAN_INTERVAL

from .conftest import CAT_TOM, CAT_ZOE, MACHINE_A, MACHINE_B, household, visit

NOW = "2026-09-27T16:00:00+00:00"


def _entity(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, "petivity", unique_id)
    assert entity_id, unique_id
    return entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_sensors(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("UTC")
    tom_events = [
        visit("e3", "2026-09-27T15:00:00", MACHINE_A, CAT_TOM, elim=None, grams=None),
        visit("e2", "2026-09-27T14:00:00", MACHINE_A, CAT_TOM, elim="combo", grams=5702.0),
    ]
    mock_client.async_get_household.return_value = household({CAT_TOM: tom_events})
    mock_client.async_get_events.return_value = [
        *tom_events,
        visit("e1", "2026-09-27T13:00:00", MACHINE_A, None, elim="defecation"),
        visit("fx", "2026-09-27T12:30:00", MACHINE_A, None, is_cat=False, elim=None, grams=None),
        # Before local midnight: fetched for events, not counted.
        visit("e0", "2026-09-26T23:00:00", MACHINE_A, CAT_TOM),
    ]
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    def state(domain: str, key: str) -> str:
        return hass.states.get(_entity(hass, domain, key)).state

    # Monitor A: 3 cat visits today (e3, e2, e1); the false trigger and
    # yesterday's visit are not counted. combo counts as both.
    assert state("sensor", f"{MACHINE_A}_visits_today") == "3"
    assert state("sensor", f"{MACHINE_A}_urinations_today") == "1"
    assert state("sensor", f"{MACHINE_A}_defecations_today") == "2"
    assert state("sensor", f"{MACHINE_A}_last_visit") == "2026-09-27T15:00:00+00:00"
    assert state("sensor", f"{MACHINE_A}_last_upload") == "2026-09-27T14:29:21+00:00"
    assert state("sensor", f"{MACHINE_A}_battery") == "unknown"
    assert state("binary_sensor", f"{MACHINE_A}_firmware_update") == "on"
    assert state("sensor", f"{MACHINE_B}_visits_today") == "0"
    assert state("sensor", f"{MACHINE_B}_battery") == "80"

    # Tom: two visits today; the latest has no weight, so weight comes from
    # the latest weighed one (grams in, kilograms out on a metric system).
    assert state("sensor", f"{CAT_TOM}_visits_today") == "2"
    assert state("sensor", f"{CAT_TOM}_last_visit_type") == "none"
    assert state("sensor", f"{CAT_TOM}_weight") == "5.702"
    assert state("sensor", f"{CAT_ZOE}_visits_today") == "0"
    assert state("sensor", f"{CAT_ZOE}_weight") == "unknown"

    # Cat devices are prefixed so they do not collide with other integrations.
    assert hass.states.get(_entity(hass, "sensor", f"{CAT_TOM}_weight")).name == "Petivity Tom Weight"


async def test_monitor_last_visit_survives_midnight(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("UTC")
    yesterday = visit("y1", "2026-09-26T20:11:03", MACHINE_B, CAT_ZOE)
    mock_client.async_get_household.return_value = household(
        events_by_machine={
            # The monitor's newest event is an automatic cleaning, not a cat.
            MACHINE_B: [
                visit("c1", "2026-09-26T21:00:00", MACHINE_B, is_cat=False, elim=None, grams=None),
                yesterday,
            ],
            MACHINE_A: [visit("a0", "2026-09-27T10:00:00", MACHINE_A, CAT_TOM)],
        }
    )
    # Today's fetch has a newer visit to monitor A than the household query saw.
    mock_client.async_get_events.return_value = [
        visit("a1", "2026-09-27T15:30:00", MACHINE_A, CAT_TOM),
    ]
    await _setup(hass, config_entry)

    def state(key: str) -> str:
        return hass.states.get(_entity(hass, "sensor", key)).state

    # No visits to monitor B today, but its last visit is still yesterday's.
    assert state(f"{MACHINE_B}_visits_today") == "0"
    assert state(f"{MACHINE_B}_last_visit") == "2026-09-26T20:11:03+00:00"
    # Monitor A takes the newer of the two sources.
    assert state(f"{MACHINE_A}_last_visit") == "2026-09-27T15:30:00+00:00"


async def test_visit_event_fires_only_for_new_visits(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await hass.config.async_set_time_zone("UTC")
    old = visit("e1", "2026-09-27T13:00:00", MACHINE_A, CAT_TOM)
    mock_client.async_get_events.return_value = [old]
    await _setup(hass, config_entry)

    event_a = _entity(hass, "event", f"{MACHINE_A}_visit")
    # The first poll only records what exists: no replay after a restart.
    assert hass.states.get(event_a).state == "unknown"
    changes = async_capture_events(hass, "state_changed")

    new1 = visit("e2", "2026-09-27T15:00:00", MACHINE_A, CAT_TOM, elim="defecation", grams=3850.0)
    new2 = visit("e3", "2026-09-27T15:10:00", MACHINE_A, None, elim=None, grams=None)
    other = visit("e4", "2026-09-27T15:05:00", MACHINE_B, CAT_ZOE)
    mock_client.async_get_events.return_value = [new2, other, new1, old]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    fired = [
        c.data["new_state"].attributes
        for c in changes
        if c.data["entity_id"] == event_a and c.data["new_state"].attributes.get("event_type") == "visit"
    ]
    # Both new visits on monitor A fire, oldest first; B's visit does not.
    assert [f["started"] for f in fired] == ["2026-09-27T15:00:00+00:00", "2026-09-27T15:10:00+00:00"]
    assert fired[0]["cat"] == "Tom"
    assert fired[0]["visit_type"] == "defecation"
    assert fired[0]["weight_kg"] == 3.85
    assert fired[1]["cat"] is None
    assert fired[1]["visit_type"] == "none"

    # Nothing new on the next poll: no further events.
    changes.clear()
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert not [c for c in changes if c.data["entity_id"] == event_a]


async def test_renewed_session_is_saved(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    mock_client.id_token = "renewed.id.token"
    await _setup(hass, config_entry)
    assert config_entry.data[CONF_ID_TOKEN] == "renewed.id.token"


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    mock_client.async_get_household.side_effect = PetivityAuthError("expired")
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_unload(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_weight_in_pounds_on_us_customary(
    hass: HomeAssistant, mock_client: MagicMock, config_entry: MockConfigEntry
) -> None:
    from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

    hass.config.units = US_CUSTOMARY_SYSTEM
    tom = [visit("e1", "2026-09-27T14:00:00", MACHINE_A, CAT_TOM, grams=4536.0)]
    mock_client.async_get_household.return_value = household({CAT_TOM: tom})
    await _setup(hass, config_entry)
    st = hass.states.get(_entity(hass, "sensor", f"{CAT_TOM}_weight"))
    assert st.attributes["unit_of_measurement"] == "lb"
    assert abs(float(st.state) - 10.0) < 0.01
