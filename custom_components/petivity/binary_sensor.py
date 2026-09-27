"""Binary sensors for Petivity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PetivityConfigEntry, PetivityCoordinator
from .entity import PetivityMachineEntity


@dataclass(frozen=True, kw_only=True)
class MachineBinaryDescription(BinarySensorEntityDescription):
    """Describes a monitor binary sensor."""

    value_fn: Callable[[dict[str, Any]], bool | None]


MACHINE_BINARY_SENSORS: tuple[MachineBinaryDescription, ...] = (
    MachineBinaryDescription(
        key="battery_warning",
        translation_key="battery_warning",
        device_class=BinarySensorDeviceClass.BATTERY,
        value_fn=lambda m: m.get("showBatteryWarning"),
    ),
    MachineBinaryDescription(
        key="upload_warning",
        translation_key="upload_warning",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.get("mostRecentUploadWarning"),
    ),
    MachineBinaryDescription(
        key="firmware_update",
        translation_key="firmware_update",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: bool(
            m.get("stFirmwareUpgradeAvailable") or m.get("espFirmwareUpgradeAvailable")
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetivityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Petivity binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        MachineBinarySensor(coordinator, machine_id, description)
        for machine_id in coordinator.data.machines
        for description in MACHINE_BINARY_SENSORS
    )


class MachineBinarySensor(PetivityMachineEntity, BinarySensorEntity):
    """A monitor flag."""

    entity_description: MachineBinaryDescription

    def __init__(
        self, coordinator: PetivityCoordinator, machine_id: str, description: MachineBinaryDescription
    ) -> None:
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self.entity_description.value_fn(self.machine)
        return None if value is None else bool(value)
