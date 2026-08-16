"""Sur-Ron OEM controller wire protocol: framing, checksum, and packet reassembly.

Dependency-free (standard library only) so it can be unit-tested and reused with no Home
Assistant or Bluetooth stack present — same design as ebmx-ha's protocol module.

The controller speaks a simple request/response protocol over the Nordic UART Service.
A frame is::

    6B B6 | LEN | TYPE | CMD | REG | DATA[LEN] | CKSUM

* ``6B B6`` is a fixed magic prefix.
* ``LEN`` is the number of DATA bytes.
* ``TYPE`` is 0x89 for app->controller requests, 0x62 for controller->app responses.
* ``CMD`` is 0x51 for a read request, 0x54 for a read reply.
* ``REG`` identifies the field being read; the reply echoes the request's REG.
* ``CKSUM`` is the bitwise-NOT of the XOR of every byte after the magic up to and
  including the last data byte, i.e. ``(~XOR(bytes[2:-1])) & 0xFF``.

Requests are issued verbatim from the server-provided command list, so we only need to
*parse* (not build) frames here; the checksum is used to validate responses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from functools import reduce
from typing import NamedTuple

_LOGGER = logging.getLogger(__name__)

MAGIC0 = 0x6B
MAGIC1 = 0xB6
TYPE_REQUEST = 0x89
TYPE_RESPONSE = 0x62
CMD_READ = 0x51
CMD_READ_REPLY = 0x54

# Guard against a corrupt length byte making us buffer without bound.
_MAX_BUFFER = 4096


def checksum(body: bytes) -> int:
	"""Frame checksum: ``(~XOR(body)) & 0xFF`` where ``body`` is LEN..last-data byte."""
	return (~reduce(lambda a, b: a ^ b, body, 0)) & 0xFF


class Frame(NamedTuple):
	"""A decoded controller response frame."""

	reg: int
	data: bytes


def parse_response(frame: bytes) -> Frame | None:
	"""Validate and parse a single complete response frame, or None if invalid."""
	if len(frame) < 7:
		return None
	if frame[0] != MAGIC0 or frame[1] != MAGIC1:
		return None
	length = frame[2]
	if len(frame) != 7 + length:
		return None
	if frame[3] != TYPE_RESPONSE or frame[4] != CMD_READ_REPLY:
		return None
	body = frame[2 : 6 + length]  # LEN, TYPE, CMD, REG, DATA...
	if checksum(body) != frame[6 + length]:
		_LOGGER.debug("Checksum mismatch on frame %s", frame.hex())
		return None
	return Frame(reg=frame[5], data=bytes(frame[6 : 6 + length]))


def build_response(reg: int, data: bytes) -> bytes:
	"""Reconstruct a complete, checksummed response frame from a register + data.

	Used to serialise a reassembled :class:`Frame` back to canonical hex for storage and
	re-decoding; the output round-trips through :func:`parse_response`.
	"""
	body = bytes([len(data), TYPE_RESPONSE, CMD_READ_REPLY, reg]) + bytes(data)
	return bytes([MAGIC0, MAGIC1]) + body + bytes([checksum(body)])


def request_register(request_hex: str) -> int | None:
	"""Extract the REG byte from a request command hex (``6B B6 01 89 51 REG ...``)."""
	raw = bytes.fromhex(request_hex)
	if len(raw) >= 6 and raw[0] == MAGIC0 and raw[1] == MAGIC1:
		return raw[5]
	return None


class SurronPacketizer:
	"""Reassembles complete controller frames from an arbitrarily chunked byte stream.

	With a negotiated MTU of 517 each reply arrives in a single notification, but this
	buffers and resynchronises anyway so fragmentation or junk can't wedge it. ``feed``
	yields each complete, checksum-verified :class:`Frame`.
	"""

	def __init__(self) -> None:
		self._buffer = bytearray()

	def reset(self) -> None:
		self._buffer.clear()

	def feed(self, incoming: bytes) -> Iterator[Frame]:
		self._buffer.extend(incoming)
		if len(self._buffer) > _MAX_BUFFER:
			_LOGGER.warning("Surron buffer exceeded %d bytes; clearing to resync", _MAX_BUFFER)
			self._buffer.clear()
			return
		while True:
			frame = self._extract_one()
			if frame is None:
				return
			yield frame

	def _extract_one(self) -> Frame | None:
		buf = self._buffer
		while True:
			# Seek the magic prefix.
			while len(buf) >= 2 and not (buf[0] == MAGIC0 and buf[1] == MAGIC1):
				del buf[0]
			if len(buf) < 3:
				return None
			length = buf[2]
			total = 7 + length
			if len(buf) < total:
				return None
			candidate = bytes(buf[:total])
			frame = parse_response(candidate)
			if frame is None:
				del buf[0]  # bad frame; drop one byte and resync
				continue
			del buf[:total]
			return frame
