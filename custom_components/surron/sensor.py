"""Sensor platform for the Sur-Ron OEM integration.

Mirrors ebmx-ha: measurement sensors keep displaying their last value when the bike is
away (RestoreSensor), and a Last updated timestamp shows liveness. The field set starts
with the battery-related sensors (your main goal); more are added as the decoder grows.

Every sensor's ``value_fn`` reads from decoded Telemetry, so each will simply start
reporting once ``telemetry.decode_frame`` is completed from captures — no wiring changes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
	RestoreSensor,
	SensorDeviceClass,
	SensorEntity,
	SensorEntityDescription,
	SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
	PERCENTAGE,
	REVOLUTIONS_PER_MINUTE,
	EntityCategory,
	UnitOfElectricCurrent,
	UnitOfElectricPotential,
	UnitOfLength,
	UnitOfSpeed,
	UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SurronCoordinator
from .entity import SurronEntity
from .models import SurronData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class SurronSensorDescription(SensorEntityDescription):
	"""Sensor description with a function to pull the value out of SurronData."""

	value_fn: Callable[[SurronData], float | int | None]


SENSORS: tuple[SurronSensorDescription, ...] = (
	SurronSensorDescription(
		key="battery_voltage",
		translation_key="battery_voltage",
		device_class=SensorDeviceClass.VOLTAGE,
		native_unit_of_measurement=UnitOfElectricPotential.VOLT,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=1,
		value_fn=lambda d: d.telemetry.pack_voltage,
	),
	SurronSensorDescription(
		key="battery_soc_controller",
		translation_key="battery_soc_controller",
		device_class=SensorDeviceClass.BATTERY,
		native_unit_of_measurement=PERCENTAGE,
		state_class=SensorStateClass.MEASUREMENT,
		value_fn=lambda d: d.telemetry.controller_battery_percent,
	),
	SurronSensorDescription(
		key="battery_soc_estimate",
		translation_key="battery_soc_estimate",
		native_unit_of_measurement=PERCENTAGE,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.soc_estimate,
	),
	SurronSensorDescription(
		key="battery_soh",
		translation_key="battery_soh",
		native_unit_of_measurement=PERCENTAGE,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		entity_category=EntityCategory.DIAGNOSTIC,
		value_fn=lambda d: d.telemetry.soh_percent,
	),
	SurronSensorDescription(
		key="bus_voltage",
		translation_key="bus_voltage",
		device_class=SensorDeviceClass.VOLTAGE,
		native_unit_of_measurement=UnitOfElectricPotential.VOLT,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		entity_category=EntityCategory.DIAGNOSTIC,
		value_fn=lambda d: d.telemetry.bus_voltage,
	),
	SurronSensorDescription(
		key="max_cell_voltage",
		translation_key="max_cell_voltage",
		device_class=SensorDeviceClass.VOLTAGE,
		native_unit_of_measurement=UnitOfElectricPotential.VOLT,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=3,
		entity_category=EntityCategory.DIAGNOSTIC,
		value_fn=lambda d: d.telemetry.max_cell_voltage,
	),
	SurronSensorDescription(
		key="min_cell_voltage",
		translation_key="min_cell_voltage",
		device_class=SensorDeviceClass.VOLTAGE,
		native_unit_of_measurement=UnitOfElectricPotential.VOLT,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=3,
		entity_category=EntityCategory.DIAGNOSTIC,
		value_fn=lambda d: d.telemetry.min_cell_voltage,
	),
	SurronSensorDescription(
		key="bms_current",
		translation_key="bms_current",
		device_class=SensorDeviceClass.CURRENT,
		native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=1,
		value_fn=lambda d: d.telemetry.bms_current,
	),
	SurronSensorDescription(
		key="speed",
		translation_key="speed",
		device_class=SensorDeviceClass.SPEED,
		native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.speed_kph,
	),
	SurronSensorDescription(
		key="motor_rpm",
		translation_key="motor_rpm",
		native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.motor_rpm,
	),
	SurronSensorDescription(
		key="gear",
		translation_key="gear",
		state_class=SensorStateClass.MEASUREMENT,
		value_fn=lambda d: d.telemetry.gear,
	),
	SurronSensorDescription(
		key="controller_temperature",
		translation_key="controller_temperature",
		device_class=SensorDeviceClass.TEMPERATURE,
		native_unit_of_measurement=UnitOfTemperature.CELSIUS,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.controller_temp,
	),
	SurronSensorDescription(
		key="motor_temperature",
		translation_key="motor_temperature",
		device_class=SensorDeviceClass.TEMPERATURE,
		native_unit_of_measurement=UnitOfTemperature.CELSIUS,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.motor_temp,
	),
	SurronSensorDescription(
		key="battery_max_temperature",
		translation_key="battery_max_temperature",
		device_class=SensorDeviceClass.TEMPERATURE,
		native_unit_of_measurement=UnitOfTemperature.CELSIUS,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.battery_max_temp,
	),
	SurronSensorDescription(
		key="battery_min_temperature",
		translation_key="battery_min_temperature",
		device_class=SensorDeviceClass.TEMPERATURE,
		native_unit_of_measurement=UnitOfTemperature.CELSIUS,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=0,
		value_fn=lambda d: d.telemetry.battery_min_temp,
	),
	SurronSensorDescription(
		key="odometer",
		translation_key="odometer",
		device_class=SensorDeviceClass.DISTANCE,
		native_unit_of_measurement=UnitOfLength.KILOMETERS,
		state_class=SensorStateClass.TOTAL_INCREASING,
		suggested_display_precision=1,
		value_fn=lambda d: d.telemetry.odometer_km,
	),
	SurronSensorDescription(
		key="trip",
		translation_key="trip",
		device_class=SensorDeviceClass.DISTANCE,
		native_unit_of_measurement=UnitOfLength.KILOMETERS,
		state_class=SensorStateClass.MEASUREMENT,
		suggested_display_precision=1,
		value_fn=lambda d: d.telemetry.trip_km,
	),
	SurronSensorDescription(
		key="charge_cycles",
		translation_key="charge_cycles",
		state_class=SensorStateClass.MEASUREMENT,
		entity_category=EntityCategory.DIAGNOSTIC,
		value_fn=lambda d: d.telemetry.cycle_count,
	),
)


async def async_setup_entry(
	hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
	"""Set up Sur-Ron sensors for a bike."""
	coordinator: SurronCoordinator = hass.data[DOMAIN][entry.entry_id]
	entities: list[SensorEntity] = [SurronSensor(coordinator, desc) for desc in SENSORS]
	entities.append(SurronLastUpdatedSensor(coordinator))
	async_add_entities(entities)


class SurronSensor(SurronEntity, RestoreSensor):
	"""A telemetry sensor that keeps showing its last value when the bike is away."""

	entity_description: SurronSensorDescription

	def __init__(self, coordinator: SurronCoordinator, description: SurronSensorDescription) -> None:
		super().__init__(coordinator, description.key)
		self.entity_description = description
		self._restored_value: float | int | None = None

	async def async_added_to_hass(self) -> None:
		await super().async_added_to_hass()
		if (last := await self.async_get_last_sensor_data()) is not None:
			self._restored_value = last.native_value

	@property
	def native_value(self) -> float | int | None:
		if self.coordinator.data is not None:
			return self.entity_description.value_fn(self.coordinator.data)
		return self._restored_value

	@property
	def available(self) -> bool:
		# Keep displaying cached/restored values even when the bike isn't present.
		return self.coordinator.data is not None or self._restored_value is not None

	@callback
	def _handle_coordinator_update(self) -> None:
		self._restored_value = None  # a fresh reading supersedes any restored value
		super()._handle_coordinator_update()


class SurronLastUpdatedSensor(SurronEntity, SensorEntity):
	"""When the bike's telemetry was last successfully read (clearest liveness signal)."""

	_attr_device_class = SensorDeviceClass.TIMESTAMP
	_attr_translation_key = "last_updated"
	_attr_entity_category = EntityCategory.DIAGNOSTIC

	def __init__(self, coordinator: SurronCoordinator) -> None:
		super().__init__(coordinator, "last_updated")

	@property
	def native_value(self):
		return self.coordinator.last_success_time

	@property
	def available(self) -> bool:
		return self.coordinator.last_success_time is not None
