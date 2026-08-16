"""Coordinator that polls one Sur-Ron bike over Home Assistant's Bluetooth stack.

The bike's MAC address is random per power-cycle, but its advertised BLE name is its
serial number and is stable. So instead of binding to a fixed address, this registers a
Bluetooth callback matched by *name* and follows whatever address the bike currently
advertises. Each matching advertisement (delivered transparently through any Bluetooth
proxy) updates the tracked address, refreshes presence, and — debounced to ~10 s — triggers
a connect-and-read. When the bike is off there are no advertisements, so we don't poll and
presence lapses after a short timeout.

Connect/read uses ``bleak_retry_connector.establish_connection`` which routes through the
best adapter/proxy. The controller's write/notify characteristics are selected by property
(write + notify) after connecting, because the app doesn't hard-code their UUIDs and neither
do we; in practice this resolves to the Nordic UART Service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
	BluetoothCallbackMatcher,
	BluetoothChange,
	BluetoothScanningMode,
	BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .client import SurronBleClient
from .const import (
	COMMANDS,
	CONF_ADDRESS,
	CONF_CELLS,
	CONF_SERIAL,
	DEFAULT_POLL_STATE_CODES,
	DOMAIN,
	POLL_INTERVAL_SECONDS,
	PRESENCE_CHECK_INTERVAL_SECONDS,
	PRESENCE_TIMEOUT_SECONDS,
)
from .models import SurronData
from .telemetry import estimate_soc_percent, infer_cells

_LOGGER = logging.getLogger(__name__)


# Generic SIG services that never carry telemetry.
_SKIP_SERVICES = {
	"00001800-0000-1000-8000-00805f9b34fb",
	"00001801-0000-1000-8000-00805f9b34fb",
	"0000180a-0000-1000-8000-00805f9b34fb",
}


def _select_characteristics(client) -> tuple[object | None, object | None]:
	"""Pick the (write, notify) characteristics like the app: the first non-generic service
	that has both a write-capable char and a notify-capable char. A char with the real
	"notify" property is preferred over "indicate"; a plain "write" over write-without-
	response. On these bikes this resolves to the Nordic UART Service.
	"""
	for service in client.services:
		if service.uuid.lower() in _SKIP_SERVICES:
			continue
		write_char = None
		notify_char = None
		for char in service.characteristics:
			props = set(char.properties)
			if "notify" in props and notify_char is None:
				notify_char = char
			elif "indicate" in props and notify_char is None:
				notify_char = char
			if "write" in props and (write_char is None or "write" not in set(write_char.properties)):
				write_char = char
			elif "write-without-response" in props and write_char is None:
				write_char = char
		if write_char is not None and notify_char is not None:
			return write_char, notify_char
	return None, None


class SurronCoordinator(DataUpdateCoordinator[SurronData]):
	"""Follows one bike by serial/name and shares the latest :class:`SurronData`.

	Not time-interval driven: updates are pushed from Bluetooth advertisements. We extend
	:class:`DataUpdateCoordinator` only for its listener plumbing (so entities can use
	``CoordinatorEntity``); polling and presence are managed here.
	"""

	def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
		super().__init__(hass, _LOGGER, name=entry.title, update_interval=None)
		self.config_entry = entry
		self.serial: str = entry.unique_id or entry.data.get(CONF_SERIAL) or entry.title
		self.title: str = entry.title
		self._cells_override: int | None = entry.options.get(CONF_CELLS)

		# The real per-bike command set, restricted to the fields worth polling every 10 s.
		poll_set = set(DEFAULT_POLL_STATE_CODES)
		self._commands: list[tuple[str, str]] = [
			(state_code, request_hex)
			for state_code, request_hex in COMMANDS
			if state_code in poll_set
		]

		# Dynamically tracked from advertisements (address changes per power-cycle).
		self._address: str | None = entry.data.get(CONF_ADDRESS)
		self._ble_device = None
		self._last_seen_monotonic: float | None = None
		self._last_poll_monotonic: float | None = None
		self._present = False
		self._poll_lock = asyncio.Lock()
		self._cancels: list[CALLBACK_TYPE] = []
		self.last_success_time = None

	# --- lifecycle --------------------------------------------------------------------
	@callback
	def async_start(self) -> CALLBACK_TYPE:
		"""Begin following the bike by name; returns an unsubscribe callback."""
		matcher = BluetoothCallbackMatcher(connectable=True, local_name=self.serial)
		self._cancels.append(
			bluetooth.async_register_callback(
				self.hass,
				self._async_on_advertisement,
				matcher,
				BluetoothScanningMode.PASSIVE,
			)
		)
		self._cancels.append(
			async_track_time_interval(
				self.hass,
				self._async_check_presence,
				timedelta(seconds=PRESENCE_CHECK_INTERVAL_SECONDS),
			)
		)
		# Seed from anything already in range so we don't wait a whole advertisement cycle.
		for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
			if info.name == self.serial:
				self._absorb_advertisement(info)
				break
		return self._async_stop

	@callback
	def _async_stop(self) -> None:
		while self._cancels:
			self._cancels.pop()()

	# --- advertisement handling -------------------------------------------------------
	@callback
	def _async_on_advertisement(
		self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
	) -> None:
		"""A matching advertisement arrived: update address/presence and maybe poll."""
		self._absorb_advertisement(service_info)
		if self.hass.state is CoreState.running and self._poll_due():
			self.config_entry.async_create_background_task(
				self.hass, self._async_poll_guarded(), f"surron_poll_{self.serial}"
			)

	@callback
	def _absorb_advertisement(self, service_info: BluetoothServiceInfoBleak) -> None:
		if service_info.address != self._address:
			_LOGGER.debug(
				"%s: address changed %s -> %s", self.serial, self._address, service_info.address
			)
		self._address = service_info.address
		self._ble_device = service_info.device
		self._last_seen_monotonic = monotonic()
		if not self._present:
			self._present = True
			self.async_update_listeners()

	@callback
	def _async_check_presence(self, _now) -> None:
		"""Lapse presence if we haven't heard an advertisement recently."""
		if (
			self._present
			and self._last_seen_monotonic is not None
			and monotonic() - self._last_seen_monotonic > PRESENCE_TIMEOUT_SECONDS
		):
			self._present = False
			_LOGGER.debug("%s: presence lapsed (no advertisements)", self.serial)
			self.async_update_listeners()

	def _poll_due(self) -> bool:
		return (
			self._last_poll_monotonic is None
			or monotonic() - self._last_poll_monotonic >= POLL_INTERVAL_SECONDS
		)

	# --- polling ----------------------------------------------------------------------
	async def async_poll_now(self) -> None:
		"""Force one poll now if the bike is currently present (used at startup/reload)."""
		if self._ble_device is None:
			for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
				if info.name == self.serial:
					self._absorb_advertisement(info)
					break
		if self._ble_device is None:
			_LOGGER.debug("%s: async_poll_now skipped; bike not present", self.serial)
			return
		await self._async_poll_guarded()

	async def _async_poll_guarded(self) -> None:
		"""Run a poll unless one is already in flight; never raises."""
		if self._poll_lock.locked():
			return
		async with self._poll_lock:
			self._last_poll_monotonic = monotonic()
			try:
				await self._async_poll()
			except Exception:  # noqa: BLE001
				_LOGGER.debug("%s: poll failed", self.serial, exc_info=True)

	async def _async_poll(self) -> None:
		"""Connect (via proxy if needed), select characteristics, run the command set."""
		ble_device = self._ble_device
		if ble_device is None and self._address:
			ble_device = bluetooth.async_ble_device_from_address(
				self.hass, self._address, connectable=True
			)
		if ble_device is None:
			raise RuntimeError(f"{self.serial}: no connectable BLE device available")

		_LOGGER.debug("%s: connecting to %s", self.serial, ble_device.address)
		client = await establish_connection(
			BleakClientWithServiceCache, ble_device, self.serial
		)
		try:
			write_char, notify_char = _select_characteristics(client)
			if write_char is None or notify_char is None:
				raise RuntimeError(f"{self.serial}: no write+notify characteristics found")

			surron = SurronBleClient(client, write_char, notify_char)
			await surron.start()
			try:
				telemetry = await surron.read_all(self._commands)
			finally:
				await surron.stop()
		finally:
			await client.disconnect()

		# Prefer an explicit cell-count override; otherwise infer from pack ÷ cell voltage.
		cells = self._cells_override or infer_cells(
			telemetry.pack_voltage, telemetry.max_cell_voltage
		)
		soc = estimate_soc_percent(telemetry.pack_voltage, cells)
		self.last_success_time = dt_util.utcnow()
		data = SurronData(telemetry=telemetry, raw=telemetry.raw or {}, soc_estimate=soc)
		self.async_set_updated_data(data)  # sets self.data and notifies listeners
		_LOGGER.debug("%s: poll complete raw_keys=%s", self.serial, list(data.raw))

	# --- entity-facing helpers --------------------------------------------------------
	@property
	def available(self) -> bool:
		"""True while the bike is advertising (used by the presence binary sensor)."""
		return self._present

	@property
	def cells(self) -> int:
		"""Series cell count override for the SOC estimate (0 = infer at poll time)."""
		return self._cells_override or 0

	@property
	def device_info(self) -> DeviceInfo:
		"""One HA device per bike, keyed by the stable serial (not the changing MAC)."""
		return DeviceInfo(
			identifiers={(DOMAIN, self.serial)},
			name=self.title,
			manufacturer="Sur-Ron",
			model="OEM controller",
			serial_number=self.serial,
		)
