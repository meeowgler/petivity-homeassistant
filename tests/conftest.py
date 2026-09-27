"""Shared fixtures for the Petivity tests."""

from __future__ import annotations

from collections.abc import Generator
import copy
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.petivity.const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN, DOMAIN

MACHINE_A = "TWFjaGluZTphYWFh"
MACHINE_B = "TWFjaGluZTpiYmJi"
CAT_TOM = "Q2F0OnRvbQ=="
CAT_ZOE = "Q2F0OnpvZQ=="

HOUSEHOLD: dict[str, Any] = {
    "id": "SG91c2Vob2xkOjE=",
    "machines": [
        {
            "id": MACHINE_A,
            "sn": "VC00LT1AAAA0001",
            "name": "Office",
            "batteryPercentage": None,
            "powerMode": "AC",
            "showBatteryWarning": False,
            "mostRecentUploadAt": "2026-09-27T14:29:21",
            "mostRecentUploadWarning": False,
            "stFirmwareRevision": "9.1.0",
            "stFirmwareUpgradeAvailable": False,
            "espFirmwareRevision": "8.0.1",
            "espFirmwareUpgradeAvailable": True,
            "hardwareRevision": "1.7.0",
            "wifiRssi": -70,
            "isFrozen": False,
        },
        {
            "id": MACHINE_B,
            "sn": "VC00LT1AAAA0002",
            "name": "Hallway",
            "batteryPercentage": 80,
            "powerMode": "BATTERY",
            "showBatteryWarning": False,
            "mostRecentUploadAt": "2026-09-27T12:00:00",
            "mostRecentUploadWarning": False,
            "stFirmwareRevision": "9.1.0",
            "stFirmwareUpgradeAvailable": False,
            "espFirmwareRevision": "8.0.1",
            "espFirmwareUpgradeAvailable": False,
            "hardwareRevision": "1.7.0",
            "wifiRssi": -55,
            "isFrozen": False,
        },
    ],
    "cats": [],
}


def visit(
    event_id: str,
    start: str,
    machine: str,
    cat: str | None = None,
    elim: str | None = "urination",
    grams: float | None = 4200.0,
    is_cat: bool = True,
) -> dict[str, Any]:
    """Build one event in the API's shape."""
    return {
        "id": event_id,
        "startTime": start,
        "machine": {"id": machine},
        "normalisedClassification": {
            "isCat": is_cat,
            "isPartialCat": False,
            "isElimination": elim is not None,
            "elimType": elim,
            "catWeight": grams,
            "isWeightOutlier": False,
            "visitDuration": 55.5 if is_cat else None,
            "isMaintenance": False,
            "isScooping": False,
            "cat": {"id": cat} if cat else None,
        },
    }


def household(events_by_cat: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Return the household with two cats and their latest events."""
    events_by_cat = events_by_cat or {}
    data = copy.deepcopy(HOUSEHOLD)
    data["cats"] = [
        {
            "id": cat_id,
            "name": name,
            "inactiveAt": None,
            "machine": {"id": MACHINE_A},
            "latestEvents": {"edges": [{"node": e} for e in events_by_cat.get(cat_id, [])]},
        }
        for cat_id, name in ((CAT_TOM, "Tom"), (CAT_ZOE, "Zoe"))
    ]
    return data


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/petivity."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Petivity",
        unique_id=HOUSEHOLD["id"],
        data={CONF_ID_TOKEN: "id.tok.en", CONF_REFRESH_TOKEN: "refresh-token"},
    )


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the API client everywhere the integration creates one."""
    client = MagicMock()
    client.id_token = "id.tok.en"
    client.refresh_token = "refresh-token"
    client.async_renew = AsyncMock()
    client.async_get_household = AsyncMock(return_value=household())
    client.async_get_events = AsyncMock(return_value=[])
    with (
        patch("custom_components.petivity.PetivityClient", return_value=client),
        patch("custom_components.petivity.config_flow.PetivityClient", return_value=client),
    ):
        yield client
