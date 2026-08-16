"""A thin async client that speaks the Sur-Ron OEM protocol over a connected GATT client.

Written against a *structural* client interface (the subset of bleak's ``BleakClient`` we
use), so it can be driven by a real Bleak client, Home Assistant's proxy-aware client, or
a fake in tests — without importing bleak here. Mirrors ebmx-ha's client design.

The exchange matches the official app: subscribe to the notify characteristic, then for
each command write its bytes and wait for the reply. Every reply echoes the request's
register byte, so responses are correlated by register rather than by arrival order — a
late or spurious frame for another register can't be mis-attributed. Notifications are fed
through a :class:`SurronPacketizer` so fragmentation or junk can't wedge the stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from . import protocol
from .telemetry import Telemetry, decode_frame

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 5.0


class BleakClientLike(Protocol):
	"""The subset of bleak.BleakClient used by :class:`SurronBleClient`."""

	async def start_notify(self, char, callback) -> None: ...  # noqa: D102

	async def stop_notify(self, char) -> None: ...  # noqa: D102

	async def write_gatt_char(self, char, data, response: bool = ...) -> None: ...  # noqa: D102


class SurronBleClient:
	"""Runs the OEM command set over a connected client and returns raw + decoded data.

	``write_char`` and ``notify_char`` are the characteristics selected by property at
	connect time (write / notify), passed in by the caller (coordinator or CLI).
	"""

	def __init__(self, client: BleakClientLike, write_char, notify_char) -> None:
		self._client = client
		self._write_char = write_char
		self._notify_char = notify_char
		self._packetizer = protocol.SurronPacketizer()
		self._queue: asyncio.Queue[protocol.Frame] = asyncio.Queue()
		self._started = False

	async def start(self) -> None:
		"""Subscribe to controller notifications."""
		if self._started:
			return
		_LOGGER.debug("Starting notify on %s", self._notify_char)
		self._packetizer.reset()
		await self._client.start_notify(self._notify_char, self._on_notify)
		self._started = True

	async def stop(self) -> None:
		"""Unsubscribe (best effort)."""
		if not self._started:
			return
		try:
			await self._client.stop_notify(self._notify_char)
		finally:
			self._started = False

	def _on_notify(self, _char, data: bytearray) -> None:
		"""Notification callback: reassemble frames and queue each complete one."""
		payload = bytes(data)
		_LOGGER.debug("RX notify len=%d hex=%s", len(payload), payload.hex())
		for frame in self._packetizer.feed(payload):
			self._queue.put_nowait(frame)

	async def send_command(
		self, command_hex: str, timeout: float = DEFAULT_TIMEOUT
	) -> str:
		"""Write one command and return the raw hex of the reply matching its register.

		Raises :class:`asyncio.TimeoutError` if no matching reply arrives in ``timeout``.
		"""
		if not self._started:
			raise RuntimeError("call start() before sending commands")
		expected_reg = protocol.request_register(command_hex)
		# Drain stale frames so a previous slow reply can't satisfy this request.
		while not self._queue.empty():
			self._queue.get_nowait()

		frame_bytes = bytes.fromhex(command_hex)
		_LOGGER.debug("TX command %s (%d bytes)", command_hex, len(frame_bytes))
		await self._client.write_gatt_char(self._write_char, frame_bytes, response=True)

		async def _await_match() -> protocol.Frame:
			while True:
				frame = await self._queue.get()
				if expected_reg is None or frame.reg == expected_reg:
					return frame
				_LOGGER.debug(
					"Ignoring reply for reg 0x%02X (awaiting 0x%02X)", frame.reg, expected_reg
				)

		frame = await asyncio.wait_for(_await_match(), timeout)
		return protocol.build_response(frame.reg, frame.data).hex().upper()

	async def read_all(
		self,
		commands: list[tuple[str, str]],
		timeout: float = DEFAULT_TIMEOUT,
	) -> Telemetry:
		"""Send every ``(state_code, command_hex)`` and decode the collected responses.

		Failures on individual commands are tolerated (the bike may not answer every one,
		e.g. instrument fields when the dash is asleep); whatever raw responses we gather
		are always retained on the returned :class:`Telemetry`.
		"""
		raw: dict[str, str] = {}
		values: dict[str, float | int | str] = {}
		for state_code, command_hex in commands:
			try:
				response_hex = await self.send_command(command_hex, timeout)
			except (asyncio.TimeoutError, asyncio.CancelledError):
				_LOGGER.debug("No response for %s (%s)", state_code, command_hex)
				continue
			raw[state_code] = response_hex
			values.update(decode_frame(state_code, response_hex))

		return Telemetry(values=values, raw=raw)
