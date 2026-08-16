"""Shared entity base for Sur-Ron sensors (names/grouping + cached-value availability)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SurronCoordinator


class SurronEntity(CoordinatorEntity[SurronCoordinator]):
	"""Base entity. The coordinator keeps its last SurronData even when the bike is away,
	so entities keep showing the last-known values; presence is a separate binary_sensor.
	"""

	_attr_has_entity_name = True

	def __init__(self, coordinator: SurronCoordinator, key: str) -> None:
		super().__init__(coordinator)
		self._attr_unique_id = f"{coordinator.serial}_{key}"

	@property
	def device_info(self) -> DeviceInfo:
		return self.coordinator.device_info
