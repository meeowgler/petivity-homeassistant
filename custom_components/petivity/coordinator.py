"""Data coordinator for Petivity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import PetivityAuthError, PetivityClient, PetivityError
from .const import CONF_ID_TOKEN, CONF_REFRESH_TOKEN, DOMAIN, EVENT_LOOKBACK, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type PetivityConfigEntry = ConfigEntry[PetivityCoordinator]


@dataclass
class DailyCounts:
    """Visit counts for one cat or one monitor since local midnight."""

    visits: int = 0
    urinations: int = 0
    defecations: int = 0


@dataclass
class PetivityData:
    """Everything the entities read."""

    household_id: str
    machines: dict[str, dict[str, Any]]
    cats: dict[str, dict[str, Any]]
    cat_latest: dict[str, dict[str, Any] | None]
    cat_weighed: dict[str, dict[str, Any] | None]
    cat_today: dict[str, DailyCounts] = field(default_factory=dict)
    machine_today: dict[str, DailyCounts] = field(default_factory=dict)
    machine_latest: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    new_visits: list[dict[str, Any]] = field(default_factory=list)


def _add(counts: DailyCounts, cls: dict[str, Any]) -> None:
    counts.visits += 1
    elim = (cls.get("elimType") or "").lower()
    if elim in ("urination", "combo"):
        counts.urinations += 1
    if elim in ("defecation", "combo"):
        counts.defecations += 1


def parse_time(value: Any) -> datetime | None:
    """Parse an API timestamp. They carry no offset but are UTC."""
    if not isinstance(value, str) or (parsed := dt_util.parse_datetime(value)) is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


def event_start(event: dict[str, Any] | None) -> datetime | None:
    """Return when a visit started."""
    return parse_time((event or {}).get("startTime"))


def is_cat_visit(event: dict[str, Any] | None) -> bool:
    """Return True for a real cat visit (not a false trigger or scooping)."""
    cls = (event or {}).get("normalisedClassification") or {}
    return bool(cls.get("isCat")) and not cls.get("isMaintenance") and not cls.get("isScooping")


def _is_weighed(event: dict[str, Any]) -> bool:
    cls = event.get("normalisedClassification") or {}
    return cls.get("catWeight") is not None and not cls.get("isWeightOutlier")


def _recent(owner: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a cat's or a monitor's most recent real visits, newest first."""
    edges = (owner.get("latestEvents") or {}).get("edges") or []
    return [edge["node"] for edge in edges if is_cat_visit(edge.get("node"))]


class PetivityCoordinator(DataUpdateCoordinator[PetivityData]):
    """Poll the Petivity cloud."""

    config_entry: PetivityConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: PetivityConfigEntry, client: PetivityClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self._seen: set[str] | None = None

    async def _async_update_data(self) -> PetivityData:
        midnight = dt_util.start_of_local_day()
        try:
            household = await self.client.async_get_household()
            # Look back past midnight so a visit uploaded just after midnight
            # still fires its event; only today's visits are counted.
            events = await self.client.async_get_events(
                midnight - EVENT_LOOKBACK, dt_util.utcnow()
            )
        except PetivityAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PetivityError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._persist_session()

        machines = {m["id"]: m for m in household.get("machines") or []}
        cats = {c["id"]: c for c in household.get("cats") or []}
        data = PetivityData(
            household_id=household["id"],
            machines=machines,
            cats=cats,
            cat_latest={cat_id: next(iter(_recent(cat)), None) for cat_id, cat in cats.items()},
            cat_weighed={
                cat_id: next((e for e in _recent(cat) if _is_weighed(e)), None)
                for cat_id, cat in cats.items()
            },
            cat_today={cat_id: DailyCounts() for cat_id in cats},
            machine_today={machine_id: DailyCounts() for machine_id in machines},
            # From the monitor's own newest events, not just today's, so the
            # last visit does not go blank at midnight.
            machine_latest={
                machine_id: next(iter(_recent(machine)), None)
                for machine_id, machine in machines.items()
            },
        )

        visits = [event for event in events if is_cat_visit(event)]

        # Events arrive newest first. A visit fetched here can be newer than the
        # household query's latest events, so keep whichever is newer.
        for event in visits:
            started = event_start(event)
            if started is None or started < midnight:
                continue
            cls = event["normalisedClassification"]
            machine_id = (event.get("machine") or {}).get("id")
            if machine_id in data.machine_today:
                _add(data.machine_today[machine_id], cls)
                latest_start = event_start(data.machine_latest.get(machine_id))
                if latest_start is None or started > latest_start:
                    data.machine_latest[machine_id] = event
            cat_id = (cls.get("cat") or {}).get("id")
            if cat_id in data.cat_today:
                _add(data.cat_today[cat_id], cls)

        # New visits since the last poll, oldest first. The first poll after
        # startup only records what exists, so a restart does not replay them.
        ids = {event["id"] for event in visits}
        if self._seen is not None:
            data.new_visits = [event for event in reversed(visits) if event["id"] not in self._seen]
        self._seen = ids
        return data

    def _persist_session(self) -> None:
        """Save renewed session cookies so a restart does not lose them."""
        entry = self.config_entry
        session = {
            CONF_ID_TOKEN: self.client.id_token,
            CONF_REFRESH_TOKEN: self.client.refresh_token,
        }
        if any(entry.data.get(key) != value for key, value in session.items()):
            self.hass.config_entries.async_update_entry(entry, data={**entry.data, **session})
