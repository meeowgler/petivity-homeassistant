"""Diagnostics for Petivity."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN
from .coordinator import PetivityConfigEntry

TO_REDACT = {CONF_ID_TOKEN, CONF_REFRESH_TOKEN, "sn", "name"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PetivityConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "data": async_redact_data(asdict(entry.runtime_data.data), TO_REDACT),
    }
