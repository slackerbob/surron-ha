"""Decoding of Sur-Ron OEM controller telemetry, plus the shared SOC estimate.

Pure standard-library code: no Home Assistant, no Bluetooth, so it is unit-testable and
runnable standalone (mirrors ebmx-ha). All multi-byte numeric values are little-endian.

The field map (scale/offset/type per state_code) was derived from live captures validated
against the bike's own dashboard readings; see FINDINGS.md. Battery voltage, SOC, SOH and
per-cell voltages are confirmed against physical reality; a few fields (temperature offset,
mileage scale, current sign) are best-effort pending a second labelled capture and are
marked below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from . import protocol


class FieldDef(NamedTuple):
	"""How to decode one state_code's data bytes."""

	kind: str  # "u" unsigned-LE int, "s" signed-LE int, "str" ASCII, "fault" bytes
	scale: float = 1.0
	offset: float = 0.0
	unit: str = ""


# Decoding rules keyed by state_code. Only the fields we understand are listed; anything
# else is retained as raw hex so nothing is lost.
FIELDS: dict[str, FieldDef] = {
	# --- battery (validated) ---
	"soc": FieldDef("u", 100.0, 0.0, "%"),
	"soh": FieldDef("u", 100.0, 0.0, "%"),
	"voltage-of-battery": FieldDef("u", 10.0, 0.0, "V"),
	"controller-bus-voltage": FieldDef("u", 1.0, 0.0, "V"),
	"controller-nominal-voltage": FieldDef("u", 1.0, 0.0, ""),  # small code, kept raw-ish
	"max-monomer-voltage": FieldDef("u", 1000.0, 0.0, "V"),
	"min-monomer-voltage": FieldDef("u", 1000.0, 0.0, "V"),
	"max-monomer-voltage-num": FieldDef("u", 1.0, 0.0, ""),
	"min-monomer-voltage-num": FieldDef("u", 1.0, 0.0, ""),
	"charging-voltage": FieldDef("u", 10.0, 0.0, "V"),
	"bms-current": FieldDef("s", 10.0, 0.0, "A"),  # scale/sign TODO: needs nonzero sample
	"recharging-current": FieldDef("s", 10.0, 0.0, "A"),  # TODO
	"charge-and-discharge-times": FieldDef("u", 1.0, 0.0, "cycles"),
	# --- motion ---
	"current-speed": FieldDef("u", 1.0, 0.0, "km/h"),
	"motor-speed": FieldDef("u", 1.0, 0.0, "rpm"),
	"gear": FieldDef("u", 1.0, 0.0, ""),
	"handle-voltage": FieldDef("u", 1000.0, 0.0, "V"),
	"maximum-speed-subtotal": FieldDef("u", 1.0, 0.0, "km/h"),  # scale TODO
	# --- temperatures (offset -40 is the standard encoding; confirm with a 2nd capture) ---
	"controller-temperature": FieldDef("u", 1.0, -40.0, "°C"),
	"max-temperature": FieldDef("u", 1.0, -40.0, "°C"),
	"min-temperature": FieldDef("u", 1.0, -40.0, "°C"),
	"motor-temperature": FieldDef("u", 1.0, -40.0, "°C"),  # 0xFF => no sensor (handled)
	"max-temperature-num": FieldDef("u", 1.0, 0.0, ""),
	"min-temperature-num": FieldDef("u", 1.0, 0.0, ""),
	# --- mileage (scale /10 tentative; confirm against dash / after a ride) ---
	"total-mileage": FieldDef("u", 10.0, 0.0, "km"),
	"subtotal-mileage": FieldDef("u", 10.0, 0.0, "km"),
	"estimated-mileage": FieldDef("u", 1.0, 0.0, "km"),
	# --- statuses ---
	"discharge-relay-status": FieldDef("u", 1.0, 0.0, ""),
	"charging-relay-status": FieldDef("u", 1.0, 0.0, ""),
	"activation-status": FieldDef("u", 1.0, 0.0, ""),
	# --- identifiers / strings ---
	"gps-sn": FieldDef("str"),
	"bms-sn": FieldDef("str"),
	"mcu-sn": FieldDef("str"),
	"imei-number-of-gps": FieldDef("str"),
	"qccid": FieldDef("str"),
	"current-sim-card-imsi-number": FieldDef("str"),
	"apn": FieldDef("str"),
	"gps-hardware-version": FieldDef("str"),
	"gps-software-version": FieldDef("str"),
	"mcu-hardware-version": FieldDef("str"),
	"mcu-software-version": FieldDef("str"),
	"bms-hardware-version": FieldDef("str"),
	"bms-software-version": FieldDef("str"),
	# --- faults (kept as hex; all-zero means OK) ---
	"abs-fault-code": FieldDef("fault"),
	"mcu-error-code": FieldDef("fault"),
	"bms-3-leave-error-code": FieldDef("fault"),
	"bms-1-2-leave-error-code": FieldDef("fault"),
	"instrument-fault-code": FieldDef("fault"),
}

# A value of 0xFF in a single-byte temperature means "no sensor fitted".
_TEMP_NO_SENSOR = 0xFF


def decode_value(state_code: str, data: bytes) -> float | int | str | None:
	"""Decode one field's data bytes into a Python value, or None if not decodable."""
	spec = FIELDS.get(state_code)
	if spec is None:
		return None
	if not data:
		return None
	if spec.kind == "str":
		return data.split(b"\x00", 1)[0].decode("ascii", "replace").strip() or None
	if spec.kind == "fault":
		return None if set(data) == {0} else data.hex().upper()
	raw = int.from_bytes(data, "little", signed=(spec.kind == "s"))
	if state_code == "motor-temperature" and len(data) == 1 and raw == _TEMP_NO_SENSOR:
		return None
	value = raw / spec.scale + spec.offset if (spec.scale != 1.0 or spec.offset) else raw
	return value


def decode_frame(state_code: str, response_hex: str) -> dict[str, float | int | str]:
	"""Decode one raw response into ``{state_code: value}`` (empty if undecodable)."""
	if not response_hex:
		return {}
	frame = protocol.parse_response(bytes.fromhex(response_hex))
	if frame is None:
		return {}
	value = decode_value(state_code, frame.data)
	return {} if value is None else {state_code: value}


@dataclass(frozen=True)
class Telemetry:
	"""A decoded telemetry sample: every decoded field by state_code, plus curated helpers.

	``values`` holds decoded numbers/strings keyed by state_code; ``raw`` holds the original
	hex per state_code so nothing is lost even for fields we don't yet decode.
	"""

	values: dict[str, float | int | str] = field(default_factory=dict)
	raw: dict[str, str] = field(default_factory=dict)

	def get(self, state_code: str) -> float | int | str | None:
		return self.values.get(state_code)

	def _num(self, state_code: str) -> float | None:
		v = self.values.get(state_code)
		return float(v) if isinstance(v, (int, float)) else None

	# Curated accessors used by the sensor platform.
	@property
	def pack_voltage(self) -> float | None:
		return self._num("voltage-of-battery")

	@property
	def controller_battery_percent(self) -> int | None:
		v = self._num("soc")
		return round(v) if v is not None else None

	@property
	def soh_percent(self) -> float | None:
		return self._num("soh")

	@property
	def bus_voltage(self) -> float | None:
		return self._num("controller-bus-voltage")

	@property
	def max_cell_voltage(self) -> float | None:
		return self._num("max-monomer-voltage")

	@property
	def min_cell_voltage(self) -> float | None:
		return self._num("min-monomer-voltage")

	@property
	def speed_kph(self) -> float | None:
		return self._num("current-speed")

	@property
	def motor_rpm(self) -> int | None:
		v = self._num("motor-speed")
		return int(v) if v is not None else None

	@property
	def gear(self) -> int | None:
		v = self._num("gear")
		return int(v) if v is not None else None

	@property
	def controller_temp(self) -> float | None:
		return self._num("controller-temperature")

	@property
	def motor_temp(self) -> float | None:
		return self._num("motor-temperature")

	@property
	def battery_max_temp(self) -> float | None:
		return self._num("max-temperature")

	@property
	def battery_min_temp(self) -> float | None:
		return self._num("min-temperature")

	@property
	def odometer_km(self) -> float | None:
		return self._num("total-mileage")

	@property
	def trip_km(self) -> float | None:
		return self._num("subtotal-mileage")

	@property
	def cycle_count(self) -> int | None:
		v = self._num("charge-and-discharge-times")
		return int(v) if v is not None else None

	@property
	def bms_current(self) -> float | None:
		return self._num("bms-current")


# --- SOC estimate (ported verbatim from ebmx-ha) --------------------------------------
_FULL_VPC = 4.20
_DEFAULT_EMPTY_VPC = 3.30


def infer_cells(pack_voltage: float | None, max_cell_voltage: float | None) -> int:
	"""Infer the series cell count from pack ÷ per-cell voltage (robust when charged)."""
	if pack_voltage and max_cell_voltage and max_cell_voltage > 0:
		return max(1, round(pack_voltage / max_cell_voltage))
	if pack_voltage:
		return max(1, round(pack_voltage / _FULL_VPC))  # assume near-full as a fallback
	return 0


def estimate_soc_percent(
	pack_voltage: float | None, cells: int, empty_vpc: float | None = None
) -> float | None:
	"""Rough, transparent voltage-based SOC (0-100), or None. Same estimator as ebmx-ha."""
	if not pack_voltage or pack_voltage <= 0 or cells <= 0:
		return None
	v_cell = pack_voltage / cells
	empty = _DEFAULT_EMPTY_VPC if empty_vpc is None else min(max(empty_vpc, 2.8), 3.6)
	pct = (v_cell - empty) / (_FULL_VPC - empty) * 100.0
	return max(0.0, min(100.0, pct))
