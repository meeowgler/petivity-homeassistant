"""Sensors for Petivity."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfMass,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import DailyCounts, PetivityConfigEntry, PetivityCoordinator
from .entity import PetivityCatEntity, PetivityMachineEntity

ELIMINATION_TYPES = ["urination", "defecation", "combo", "none"]


def _time(value: Any) -> datetime | None:
    """Parse an API timestamp. They carry no offset but are UTC."""
    if not isinstance(value, str) or (parsed := dt_util.parse_datetime(value)) is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt_util.UTC)


def _weight_kg(event: dict[str, Any] | None) -> float | None:
    """Return the cat's weight in kilograms (the API reports grams)."""
    cls = _cls(event)
    grams = cls.get("catWeight")
    if grams is None or cls.get("isWeightOutlier"):
        return None
    return round(grams / 1000, 3)


def _cls(event: dict[str, Any] | None) -> dict[str, Any]:
    return (event or {}).get("normalisedClassification") or {}


def _elim(event: dict[str, Any] | None) -> str | None:
    if event is None:
        return None
    cls = _cls(event)
    if not cls.get("isElimination"):
        return "none"
    elim = (cls.get("elimType") or "").lower()
    return elim if elim in ELIMINATION_TYPES else None


@dataclass(frozen=True, kw_only=True)
class MachineSensorDescription(SensorEntityDescription):
    """Describes a monitor sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class CountSensorDescription(SensorEntityDescription):
    """Describes a daily visit counter."""

    value_fn: Callable[[DailyCounts], int]


@dataclass(frozen=True, kw_only=True)
class LatestSensorDescription(SensorEntityDescription):
    """Describes a sensor read from a cat's or monitor's latest visit."""

    value_fn: Callable[[dict[str, Any] | None], Any]


MACHINE_SENSORS: tuple[MachineSensorDescription, ...] = (
    MachineSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.get("batteryPercentage"),
    ),
    MachineSensorDescription(
        key="last_upload",
        translation_key="last_upload",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda m: _time(m.get("mostRecentUploadAt")),
    ),
    MachineSensorDescription(
        key="power_mode",
        translation_key="power_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.get("powerMode"),
    ),
    MachineSensorDescription(
        key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.get("wifiRssi"),
    ),
    MachineSensorDescription(
        key="st_firmware",
        translation_key="st_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.get("stFirmwareRevision"),
    ),
    MachineSensorDescription(
        key="esp_firmware",
        translation_key="esp_firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.get("espFirmwareRevision"),
    ),
)

COUNT_SENSORS: tuple[CountSensorDescription, ...] = (
    CountSensorDescription(
        key="visits_today",
        translation_key="visits_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.visits,
    ),
    CountSensorDescription(
        key="urinations_today",
        translation_key="urinations_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.urinations,
    ),
    CountSensorDescription(
        key="defecations_today",
        translation_key="defecations_today",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda c: c.defecations,
    ),
)

LATEST_SENSORS: tuple[LatestSensorDescription, ...] = (
    LatestSensorDescription(
        key="last_visit",
        translation_key="last_visit",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda e: _time((e or {}).get("startTime")),
    ),
    LatestSensorDescription(
        key="last_visit_type",
        translation_key="last_visit_type",
        device_class=SensorDeviceClass.ENUM,
        options=ELIMINATION_TYPES,
        value_fn=_elim,
    ),
    LatestSensorDescription(
        key="last_visit_duration",
        translation_key="last_visit_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda e: _cls(e).get("visitDuration"),
    ),
)

CAT_WEIGHT = LatestSensorDescription(
    key="weight",
    device_class=SensorDeviceClass.WEIGHT,
    native_unit_of_measurement=UnitOfMass.KILOGRAMS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=2,
    value_fn=_weight_kg,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PetivityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Petivity sensors."""
    coordinator = entry.runtime_data
    data = coordinator.data
    entities: list[SensorEntity] = []
    for machine_id in data.machines:
        entities += [MachineSensor(coordinator, machine_id, d) for d in MACHINE_SENSORS]
        entities += [MachineCountSensor(coordinator, machine_id, d) for d in COUNT_SENSORS]
        entities.append(MachineLatestSensor(coordinator, machine_id, LATEST_SENSORS[0]))
    for cat_id in data.cats:
        entities += [CatCountSensor(coordinator, cat_id, d) for d in COUNT_SENSORS]
        entities += [CatLatestSensor(coordinator, cat_id, d) for d in (*LATEST_SENSORS, CAT_WEIGHT)]
    async_add_entities(entities)


class MachineSensor(PetivityMachineEntity, SensorEntity):
    """A monitor property."""

    entity_description: MachineSensorDescription

    def __init__(
        self, coordinator: PetivityCoordinator, machine_id: str, description: MachineSensorDescription
    ) -> None:
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.machine)


class MachineCountSensor(PetivityMachineEntity, SensorEntity):
    """Visits to one monitor since local midnight, all cats."""

    entity_description: CountSensorDescription

    def __init__(
        self, coordinator: PetivityCoordinator, machine_id: str, description: CountSensorDescription
    ) -> None:
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        counts = self.coordinator.data.machine_today.get(self.machine_id, DailyCounts())
        return self.entity_description.value_fn(counts)


class MachineLatestSensor(PetivityMachineEntity, SensorEntity):
    """The latest cat visit to one monitor today."""

    entity_description: LatestSensorDescription

    def __init__(
        self, coordinator: PetivityCoordinator, machine_id: str, description: LatestSensorDescription
    ) -> None:
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(
            self.coordinator.data.machine_latest.get(self.machine_id)
        )


class CatCountSensor(PetivityCatEntity, SensorEntity):
    """One cat's visits since local midnight."""

    entity_description: CountSensorDescription

    def __init__(
        self, coordinator: PetivityCoordinator, cat_id: str, description: CountSensorDescription
    ) -> None:
        super().__init__(coordinator, cat_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int:
        counts = self.coordinator.data.cat_today.get(self.cat_id, DailyCounts())
        return self.entity_description.value_fn(counts)


class CatLatestSensor(PetivityCatEntity, SensorEntity):
    """A value from one cat's latest visit."""

    entity_description: LatestSensorDescription

    def __init__(
        self, coordinator: PetivityCoordinator, cat_id: str, description: LatestSensorDescription
    ) -> None:
        super().__init__(coordinator, cat_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        # Weight comes from the latest visit that has one, so a visit the
        # scale could not weigh does not blank the sensor.
        source = data.cat_weighed if self.entity_description is CAT_WEIGHT else data.cat_latest
        return self.entity_description.value_fn(source.get(self.cat_id))
