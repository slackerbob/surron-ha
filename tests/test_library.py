"""Pure-library tests (no Home Assistant, no Bluetooth): framing/checksum, the decoder
against real captured frames, the SOC estimate, and the register-correlating client driven
by a fake GATT client.
"""

import asyncio

from custom_components.surron import protocol
from custom_components.surron.client import SurronBleClient
from custom_components.surron.telemetry import (
	Telemetry,
	decode_frame,
	estimate_soc_percent,
	infer_cells,
)

# Real numeric frames captured from a bike at 100% charge, parked. These carry only
# battery/telemetry numbers — no identifying data.
SOC = ("6BB601895109022D", "6BB602625409F22616")  # 99.7 %
VOLTAGE = ("6BB6018951050221", "6BB602625405480385")  # 84.0 V
MAX_CELL = ("6BB6018951110235", "6BB6026254117A10B0")  # 4.218 V
CTRL_TEMP = ("6BB60189511C013B", "6BB60162541C4397")  # 27 C (raw 0x43, offset -40)
MOTOR_TEMP = ("6BB60189511D013A", "6BB60162541DFF2A")  # 0xFF -> no sensor

# The gps-sn field returns an ASCII serial. We never embed a real one: build a synthetic
# frame from a fake serial so the string-decode path is tested without any real identifier.
GPS_SN_REG = 0x7D
FAKE_SERIAL = "SN-TESTBIKE-0001"
FAKE_GPS_SN_HEX = protocol.build_response(GPS_SN_REG, FAKE_SERIAL.encode("ascii")).hex().upper()


def test_checksum_parse_and_build_roundtrip():
	frame = protocol.parse_response(bytes.fromhex(SOC[1]))
	assert frame is not None
	assert frame.reg == 0x09 and frame.data == bytes.fromhex("F226")
	# checksum: ~XOR(bytes after magic, excluding cksum)
	body = bytes.fromhex(SOC[1])[2:-1]
	assert protocol.checksum(body) == bytes.fromhex(SOC[1])[-1]
	# build_response round-trips to the exact captured bytes
	rebuilt = protocol.build_response(frame.reg, frame.data)
	assert rebuilt.hex().upper() == SOC[1]
	# a single flipped byte fails validation
	corrupt = bytearray(bytes.fromhex(SOC[1]))
	corrupt[6] ^= 0xFF
	assert protocol.parse_response(bytes(corrupt)) is None


def test_request_register_extraction():
	assert protocol.request_register(SOC[0]) == 0x09
	assert protocol.request_register(VOLTAGE[0]) == 0x05
	assert protocol.request_register("00") is None


def test_packetizer_reassembles_and_resyncs():
	pkt = protocol.SurronPacketizer()
	# leading junk + two concatenated frames in one chunk
	blob = bytes.fromhex("AABB") + bytes.fromhex(SOC[1]) + bytes.fromhex(VOLTAGE[1])
	frames = list(pkt.feed(blob))
	assert [f.reg for f in frames] == [0x09, 0x05]
	# a frame split across two chunks
	raw = bytes.fromhex(MAX_CELL[1])
	assert list(pkt.feed(raw[:4])) == []
	out = list(pkt.feed(raw[4:]))
	assert len(out) == 1 and out[0].reg == 0x11


def test_decode_real_frames():
	assert decode_frame("soc", SOC[1]) == {"soc": 99.7}
	assert decode_frame("voltage-of-battery", VOLTAGE[1]) == {"voltage-of-battery": 84.0}
	assert decode_frame("max-monomer-voltage", MAX_CELL[1]) == {"max-monomer-voltage": 4.218}
	assert decode_frame("controller-temperature", CTRL_TEMP[1]) == {"controller-temperature": 27.0}
	# 0xFF motor temp = no sensor -> nothing decoded
	assert decode_frame("motor-temperature", MOTOR_TEMP[1]) == {}
	# string decode via a synthetic gps-sn frame (build_response -> parse -> decode)
	assert decode_frame("gps-sn", FAKE_GPS_SN_HEX) == {"gps-sn": FAKE_SERIAL}


def test_telemetry_curated_accessors_and_cell_inference():
	values = {}
	for state, (_cmd, resp) in {
		"soc": SOC,
		"voltage-of-battery": VOLTAGE,
		"max-monomer-voltage": MAX_CELL,
	}.items():
		values.update(decode_frame(state, resp))
	tel = Telemetry(values=values, raw={})
	assert tel.pack_voltage == 84.0
	assert tel.controller_battery_percent == 100  # 99.7 rounds to 100
	assert tel.max_cell_voltage == 4.218
	assert infer_cells(tel.pack_voltage, tel.max_cell_voltage) == 20


def test_soc_estimate_matches_ebmx_behaviour():
	soc = estimate_soc_percent(75.0, 20)
	expected = (75.0 / 20 - 3.30) / (4.20 - 3.30) * 100.0
	assert soc is not None and abs(soc - expected) < 1e-6
	assert estimate_soc_percent(None, 20) is None
	assert estimate_soc_percent(75.0, 0) is None
	assert estimate_soc_percent(20.0, 20) == 0.0
	assert estimate_soc_percent(90.0, 20) == 100.0


class _FakeClient:
	"""BleakClient-like fake: when a command is written, deliver its canned frame(s)."""

	def __init__(self, responses):
		self._responses = responses  # command_hex -> list[bytes]
		self._cb = None

	async def start_notify(self, _char, callback):
		self._cb = callback

	async def stop_notify(self, _char):
		self._cb = None

	async def write_gatt_char(self, _char, data, response=True):
		key = bytes(data).hex().upper()
		for frame in self._responses.get(key, []):
			if self._cb is not None:
				asyncio.get_running_loop().call_soon(self._cb, None, bytearray(frame))


def test_client_returns_canonical_frame():
	async def _run():
		fake = _FakeClient({SOC[0]: [bytes.fromhex(SOC[1])]})
		client = SurronBleClient(fake, write_char="w", notify_char="n")
		await client.start()
		out = await client.send_command(SOC[0], timeout=1.0)
		assert out == SOC[1]
		await client.stop()

	asyncio.run(_run())


def test_client_matches_reply_by_register_ignoring_strays():
	async def _run():
		# Deliver a stray voltage reply first, then the correct SOC reply.
		fake = _FakeClient({SOC[0]: [bytes.fromhex(VOLTAGE[1]), bytes.fromhex(SOC[1])]})
		client = SurronBleClient(fake, write_char="w", notify_char="n")
		await client.start()
		out = await client.send_command(SOC[0], timeout=1.0)
		assert out == SOC[1]  # matched by register 0x09, not the stray 0x05
		await client.stop()

	asyncio.run(_run())


def test_client_read_all_decodes_and_retains_raw():
	async def _run():
		fake = _FakeClient(
			{
				SOC[0]: [bytes.fromhex(SOC[1])],
				VOLTAGE[0]: [bytes.fromhex(VOLTAGE[1])],
			}
		)
		client = SurronBleClient(fake, write_char="w", notify_char="n")
		await client.start()
		tel = await client.read_all(
			[("soc", SOC[0]), ("voltage-of-battery", VOLTAGE[0])], timeout=1.0
		)
		assert tel.pack_voltage == 84.0
		assert tel.controller_battery_percent == 100
		assert tel.raw == {"soc": SOC[1], "voltage-of-battery": VOLTAGE[1]}
		await client.stop()

	asyncio.run(_run())
