"""Binary sensor: whether the bike is currently present/connectable (advertising)."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
	BinarySensorDeviceClass,
	BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import SurronCoordinator
from .entity import SurronEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
	hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
	"""Set up the presence binary sensor."""
	coordinator: SurronCoordinator = hass.data[DOMAIN][entry.entry_id]
	async_add_entities([SurronPresenceBinarySensor(coordinator)])


class SurronPresenceBinarySensor(SurronEntity, BinarySensorEntity):
	"""On when the bike is advertising (powered on and in range of an adapter/proxy)."""

	_attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
	_attr_translation_key = "present"

	def __init__(self, coordinator: SurronCoordinator) -> None:
		super().__init__(coordinator, "present")

	@property
	def is_on(self) -> bool:
		return self.coordinator.available

	@property
	def available(self) -> bool:
		return True
