"""The Purina Petivity integration."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PetivityClient
from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN
from .coordinator import PetivityConfigEntry, PetivityCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PetivityConfigEntry) -> bool:
    """Set up Petivity from a config entry."""
    client = PetivityClient(
        async_get_clientsession(hass), entry.data[CONF_ID_TOKEN], entry.data[CONF_REFRESH_TOKEN]
    )
    coordinator = PetivityCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PetivityConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
