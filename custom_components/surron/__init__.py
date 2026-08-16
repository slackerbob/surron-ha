"""The Sur-Ron OEM Bluetooth integration.

Only the protocol/decoding modules (protocol, telemetry, client, const, models) are
import-safe without Home Assistant, keeping the library unit-testable and runnable
standalone. HA wiring is imported lazily inside the setup functions. Ports ebmx-ha.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .const import DOMAIN  # noqa: F401  (re-exported for convenience)

PLATFORMS = ["sensor", "binary_sensor"]

_LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
	from homeassistant.config_entries import ConfigEntry
	from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
	"""Set up a bike from a config entry."""
	from .coordinator import SurronCoordinator

	coordinator = SurronCoordinator(hass, entry)
	hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

	unsubscribe = coordinator.async_start()
	entry.async_on_unload(unsubscribe)

	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

	# Advertisement-driven polling only fires on the next advertisement after HA is
	# running; kick off one poll once HA has started (or immediately if already running).
	from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
	from homeassistant.core import CoreState

	async def _kickoff_initial_poll(_event=None) -> None:
		await coordinator.async_poll_now()

	if hass.state is CoreState.running:
		entry.async_create_background_task(
			hass, _kickoff_initial_poll(), f"surron_initial_poll_{entry.entry_id}"
		)
	else:
		entry.async_on_unload(
			hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _kickoff_initial_poll)
		)

	return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
	"""Unload a config entry."""
	unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	if unloaded:
		hass.data[DOMAIN].pop(entry.entry_id, None)
	return unloaded


async def _async_reload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
	"""Reload when options (e.g. cell-count override) change."""
	await hass.config_entries.async_reload(entry.entry_id)
