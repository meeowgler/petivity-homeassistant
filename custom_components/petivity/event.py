"""Visit events for Petivity."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PetivityConfigEntry, PetivityCoordinator, event_start
from .entity import PetivityMachineEntity

EVENT_VISIT = "visit"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetivityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one visit event entity per monitor."""
    coordinator = entry.runtime_data
    async_add_entities(
        PetivityVisitEvent(coordinator, machine_id) for machine_id in coordinator.data.machines
    )


class PetivityVisitEvent(PetivityMachineEntity, EventEntity):
    """Fires once for every new cat visit a monitor uploads."""

    _attr_translation_key = "visit"
    _attr_event_types = [EVENT_VISIT]

    def __init__(self, coordinator: PetivityCoordinator, machine_id: str) -> None:
        super().__init__(coordinator, machine_id, "visit")

    def _attributes(self, event: dict[str, Any]) -> dict[str, Any]:
        cls = event.get("normalisedClassification") or {}
        cat_id = (cls.get("cat") or {}).get("id")
        cat = self.coordinator.data.cats.get(cat_id) if cat_id else None
        grams = cls.get("catWeight")
        started = event_start(event)
        elim = (cls.get("elimType") or "").lower() if cls.get("isElimination") else "none"
        return {
            "cat": cat.get("name") if cat else None,
            "visit_type": elim or None,
            "weight_kg": None if grams is None or cls.get("isWeightOutlier") else round(grams / 1000, 3),
            "duration_s": cls.get("visitDuration"),
            "started": started.isoformat() if started else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        # Write state after each event so every visit in one poll is recorded,
        # not just the last.
        for event in self.coordinator.data.new_visits:
            if (event.get("machine") or {}).get("id") == self.machine_id:
                self._trigger_event(EVENT_VISIT, self._attributes(event))
                self.async_write_ha_state()
        super()._handle_coordinator_update()
