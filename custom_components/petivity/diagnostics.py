"""Diagnostics for Petivity.

Diagnostics end up attached to public bug reports, so nothing in them should
identify the account: names, serial numbers and tokens are redacted, and
every Petivity ID (household, monitors, cats, events) is replaced by a
stable alias such as ``monitor_1`` or ``cat_2``. The aliases keep the
relationships readable ("cat_1's visit on monitor_2") without the real IDs,
including where an ID is used as a dictionary key.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN
from .coordinator import PetivityConfigEntry, PetivityData

REDACTED = "**REDACTED**"
TO_REDACT = {CONF_ID_TOKEN, CONF_REFRESH_TOKEN, "sn", "name"}


def _aliases(data: PetivityData) -> dict[str, str]:
    aliases = {data.household_id: "household"}
    aliases.update({mid: f"monitor_{i}" for i, mid in enumerate(data.machines, 1)})
    aliases.update({cid: f"cat_{i}" for i, cid in enumerate(data.cats, 1)})
    return aliases


def anonymize(value: Any, aliases: dict[str, str]) -> Any:
    """Redact sensitive keys and replace every Petivity ID with its alias.

    ``aliases`` is extended in place with any ID not seen before (events).
    """

    def alias(raw: str) -> str:
        if raw not in aliases:
            aliases[raw] = f"id_{sum(a.startswith('id_') for a in aliases.values()) + 1}"
        return aliases[raw]

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            new_key = aliases.get(key, key) if isinstance(key, str) else key
            if key in TO_REDACT:
                out[new_key] = REDACTED if item is not None else None
            elif key == "id" and isinstance(item, str):
                out[new_key] = alias(item)
            else:
                out[new_key] = anonymize(item, aliases)
        return out
    if isinstance(value, (list, tuple, set)):
        return [anonymize(item, aliases) for item in value]
    if isinstance(value, str) and value in aliases:
        return aliases[value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PetivityConfigEntry
) -> dict[str, Any]:
    """Return anonymized diagnostics for a config entry."""
    data = entry.runtime_data.data
    aliases = _aliases(data)
    return {
        "entry": anonymize(dict(entry.data), aliases),
        "data": anonymize(asdict(data), aliases),
    }
