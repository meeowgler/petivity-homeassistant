"""Base entities for Petivity."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MACHINE_MODEL, MANUFACTURER
from .coordinator import PetivityCoordinator


class PetivityMachineEntity(CoordinatorEntity[PetivityCoordinator]):
    """An entity that belongs to one litter box monitor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetivityCoordinator, machine_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.machine_id = machine_id
        self._attr_unique_id = f"{machine_id}_{key}"
        machine = self.machine
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, machine_id)},
            manufacturer=MANUFACTURER,
            model=MACHINE_MODEL,
            name=machine.get("name") or f"Petivity {machine.get('sn')}",
            serial_number=machine.get("sn"),
            sw_version=machine.get("espFirmwareRevision"),
            hw_version=machine.get("hardwareRevision"),
        )

    @property
    def machine(self) -> dict[str, Any]:
        return self.coordinator.data.machines.get(self.machine_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.machine_id in self.coordinator.data.machines


class PetivityCatEntity(CoordinatorEntity[PetivityCoordinator]):
    """An entity that belongs to one cat."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetivityCoordinator, cat_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.cat_id = cat_id
        self._attr_unique_id = f"{cat_id}_{key}"
        # Cats often exist in other pet integrations too (litter robots,
        # feeders), so prefix the device name to keep entity IDs distinct.
        # No via_device: a cat's assigned monitor says nothing about which
        # boxes it actually uses.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, cat_id)},
            manufacturer=MANUFACTURER,
            model="Cat",
            name=f"Petivity {self.cat.get('name') or 'cat'}",
        )

    @property
    def cat(self) -> dict[str, Any]:
        return self.coordinator.data.cats.get(self.cat_id, {})

    @property
    def available(self) -> bool:
        return super().available and self.cat_id in self.coordinator.data.cats
