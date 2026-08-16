"""Config flow for the Sur-Ron OEM integration.

Each bike becomes its own config entry (and HA device), identified by its **serial number**,
which the bike advertises as its BLE name. That serial is stable; the MAC address is random
per power-cycle, so it is never used as identity (only stored as a hint). Bikes can be added
automatically when Home Assistant sees one advertising the Nordic UART service, picked from a
list of currently-visible named devices, or entered by serial manually.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
	BluetoothServiceInfoBleak,
	async_discovered_service_info,
)
from homeassistant.config_entries import (
	ConfigEntry,
	ConfigFlow,
	ConfigFlowResult,
	OptionsFlow,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

from .const import CONF_ADDRESS, CONF_CELLS, CONF_SERIAL, DOMAIN, NUS_SERVICE_UUID

_LOGGER = logging.getLogger(__name__)


def _title(info: BluetoothServiceInfoBleak) -> str:
	return info.name or f"Sur-Ron {info.address}"


def _looks_like_surron(info: BluetoothServiceInfoBleak) -> bool:
	"""Match by advertised Nordic UART service if present (best-effort)."""
	uuids = {u.lower() for u in info.service_uuids}
	return NUS_SERVICE_UUID.lower() in uuids


class SurronConfigFlow(ConfigFlow, domain=DOMAIN):
	"""Handle a config flow for Sur-Ron bikes (identified by serial / BLE name)."""

	VERSION = 1

	def __init__(self) -> None:
		self._discovery: BluetoothServiceInfoBleak | None = None
		# serial -> most-recent advertisement for that serial
		self._discovered: dict[str, BluetoothServiceInfoBleak] = {}

	async def async_step_bluetooth(
		self, discovery_info: BluetoothServiceInfoBleak
	) -> ConfigFlowResult:
		"""Handle a bike discovered over Bluetooth."""
		# A device with no name can't be tracked across power-cycles; ignore it.
		if not discovery_info.name:
			return self.async_abort(reason="no_name")
		await self.async_set_unique_id(discovery_info.name)
		self._abort_if_unique_id_configured(
			updates={CONF_ADDRESS: discovery_info.address}
		)
		self._discovery = discovery_info
		self.context["title_placeholders"] = {"name": _title(discovery_info)}
		return await self.async_step_bluetooth_confirm()

	async def async_step_bluetooth_confirm(
		self, user_input: dict[str, Any] | None = None
	) -> ConfigFlowResult:
		"""Confirm adding a discovered bike."""
		assert self._discovery is not None
		if user_input is not None:
			return self._create(self._discovery)
		self._set_confirm_only()
		return self.async_show_form(
			step_id="bluetooth_confirm",
			description_placeholders={"name": _title(self._discovery)},
		)

	async def async_step_user(
		self, user_input: dict[str, Any] | None = None
	) -> ConfigFlowResult:
		"""Add a bike by picking from currently-visible devices, or switch to manual."""
		if user_input is not None:
			if user_input.get("use_manual"):
				return await self.async_step_manual()
			serial = user_input[CONF_SERIAL]
			await self.async_set_unique_id(serial, raise_on_progress=False)
			self._abort_if_unique_id_configured()
			return self._create(self._discovered[serial])

		configured = set(self._async_current_ids())
		for info in async_discovered_service_info(self.hass, connectable=True):
			if not info.name or info.name in configured:
				continue
			# Prefer NUS-advertising devices, but keep the newest advert per serial.
			if _looks_like_surron(info) or info.name in self._discovered:
				self._discovered[info.name] = info

		if not self._discovered:
			return await self.async_step_manual()

		return self.async_show_form(
			step_id="user",
			data_schema=vol.Schema(
				{
					vol.Required(CONF_SERIAL): vol.In(
						{serial: _title(info) for serial, info in self._discovered.items()}
					),
					vol.Optional("use_manual", default=False): bool,
				}
			),
		)

	async def async_step_manual(
		self, user_input: dict[str, Any] | None = None
	) -> ConfigFlowResult:
		"""Add a bike by manually entering its serial number (its BLE name)."""
		if user_input is not None:
			serial = user_input[CONF_SERIAL].strip()
			await self.async_set_unique_id(serial, raise_on_progress=False)
			self._abort_if_unique_id_configured()
			return self.async_create_entry(
				title=serial, data={CONF_SERIAL: serial}
			)

		return self.async_show_form(
			step_id="manual",
			data_schema=vol.Schema({vol.Required(CONF_SERIAL): str}),
		)

	@callback
	def _create(self, info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
		"""Create an entry keyed by serial, storing the current address as a hint."""
		return self.async_create_entry(
			title=_title(info),
			data={CONF_SERIAL: info.name, CONF_ADDRESS: info.address},
		)

	@staticmethod
	@callback
	def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
		return SurronOptionsFlow()


class SurronOptionsFlow(OptionsFlow):
	"""Options: override the series cell count used for the SOC estimate."""

	async def async_step_init(
		self, user_input: dict[str, Any] | None = None
	) -> ConfigFlowResult:
		if user_input is not None:
			data: dict[str, Any] = {}
			if user_input.get(CONF_CELLS):
				data[CONF_CELLS] = int(user_input[CONF_CELLS])
			return self.async_create_entry(title="", data=data)

		current = self.config_entry.options.get(CONF_CELLS, "")
		return self.async_show_form(
			step_id="init",
			data_schema=vol.Schema(
				{
					vol.Optional(
						CONF_CELLS, description={"suggested_value": current}
					): cv.positive_int,
				}
			),
		)
