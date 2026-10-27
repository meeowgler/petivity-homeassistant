"""The Purina Petivity integration."""

from __future__ import annotations

import aiohttp

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PetivityClient
from .const import (
    CONF_DPOP_KEY,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
)
from .coordinator import PetivityConfigEntry, PetivityCoordinator
from .purina_auth import PurinaAuth

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: PetivityConfigEntry) -> bool:
    """Set up Petivity from a config entry."""
    if CONF_EMAIL not in entry.data:
        # An entry from before 0.3.0 held only pasted cookies. Ask for the
        # email and password so the integration can sign in and renew itself.
        raise ConfigEntryAuthFailed("Petivity now signs in with your email and password")

    # The GraphQL session carries no cookies; the auth provider runs sign-in on
    # its own cookie-jar session. A dedicated session (empty jar) keeps those
    # concerns apart. Home Assistant closes this session when the entry unloads.
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    auth = PurinaAuth(
        hass,
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        id_token=entry.data.get(CONF_ID_TOKEN),
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        dpop_key_pem=entry.data.get(CONF_DPOP_KEY),
    )
    coordinator = PetivityCoordinator(hass, entry, PetivityClient(session, auth), auth)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PetivityConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
