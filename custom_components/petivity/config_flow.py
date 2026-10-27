"""Config flow for Petivity."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PetivityAuthError, PetivityClient, PetivityError
from .const import (
    CONF_DPOP_KEY,
    CONF_EMAIL,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from .purina_auth import PurinaAuth

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_EMAIL, default=defaults.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD): str,
        }
    )


class PetivityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Petivity."""

    VERSION = 1

    async def _validate(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], str | None, dict[str, str]]:
        """Sign in and return (errors, household id, data to store)."""
        # The GraphQL session sends no cookies; the sign-in makes its own
        # cookie-jar session. This one is created without auto cleanup so it
        # can be detached (Home Assistant forbids closing its sessions).
        session = async_create_clientsession(
            self.hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
        )
        auth = PurinaAuth(self.hass, session, user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
        try:
            await auth.async_login()
            household = await PetivityClient(session, auth).async_get_household()
        except PetivityAuthError:
            return {"base": "invalid_auth"}, None, {}
        except PetivityError:
            _LOGGER.exception("Could not reach Petivity")
            return {"base": "cannot_connect"}, None, {}
        finally:
            session.detach()
        data = {
            CONF_EMAIL: user_input[CONF_EMAIL],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
            CONF_ID_TOKEN: auth.id_token,
            CONF_REFRESH_TOKEN: auth.refresh_token,
            CONF_DPOP_KEY: auth.dpop_key_pem,
        }
        return {}, household["id"], data

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, household_id, data = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(household_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Petivity", data=data)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, household_id, data = await self._validate(user_input)
            if not errors:
                await self.async_set_unique_id(household_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_schema(self._get_reauth_entry().data),
            errors=errors,
        )
