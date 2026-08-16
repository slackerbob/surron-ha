"""Constants for the Sur-Ron OEM (osgateway) Bluetooth integration and library.

Established by reverse-engineering the official app AND from live captures off a real bike
(see FINDINGS.md). The command list below is the exact per-bike set the app downloads from
Sur-Ron's cloud; baking it in keeps the integration fully offline at runtime.
"""

from __future__ import annotations

DOMAIN = "surron"

# --- BLE transport --------------------------------------------------------------------
# The controller carries this protocol over the Nordic UART Service. The app selects the
# write + notify characteristics by property; on real hardware that resolves to NUS.
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_WRITE_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_NOTIFY_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# The bike advertises with its BLE name == vehicle serial number (SN); its MAC is random
# per power-cycle, so we identify a bike by name/SN, not address.

# Poll roughly every 10 s while present (matches ebmx-ha).
POLL_INTERVAL_SECONDS = 10

# Config-entry option keys.
CONF_CELLS = "cells"  # optional series-cell override for the voltage-based SOC estimate

# Config-entry data keys.
CONF_SERIAL = "serial"  # the bike's serial number (== its advertised BLE name); stable id
CONF_ADDRESS = "address"  # last-known MAC, stored only as a hint (it changes per power-cycle)

# The bike's MAC is random per power-cycle, so presence is tracked from advertisements by
# name: considered present if a matching advertisement was seen within this window.
PRESENCE_TIMEOUT_SECONDS = 120
PRESENCE_CHECK_INTERVAL_SECONDS = 30

# --- Command list (baked in from the cloud, verified against a live bike) --------------
# Each entry is (state_code, request_hex). Requests are written verbatim; the reply echoes
# the register byte so responses are matched back by register, not by order.
COMMANDS: tuple[tuple[str, str], ...] = (
	("activation-status", "6BB60189512D010A"),
	("subtotal-mileage", "6BB601895128020C"),
	("soh", "6BB60189510D0229"),
	("recharging-current", "6BB6018951260202"),
	("qccid", "6BB60189516D145F"),
	("motor-speed", "6BB60189511B023F"),
	("min-temperature-num", "6BB601895118013F"),
	("min-temperature", "6BB6018951170130"),
	("min-monomer-voltage-num", "6BB6018951140133"),
	("min-monomer-voltage", "6BB6018951130237"),
	("mcu-error-code", "6BB6018951190837"),
	("imei-number-of-gps", "6BB6018951440F6D"),
	("gps-sn", "6BB60189517D104B"),
	("estimated-mileage", "6BB60189512C0208"),
	("discharge-relay-status", "6BB6018951070120"),
	("current-speed", "6BB6018951270100"),
	("current-sim-card-imsi-number", "6BB60189515D0F74"),
	("charging-relay-status", "6BB6018951060121"),
	("bms-current", "6BB601895108022C"),
	("bms-3-leave-error-code", "6BB60189510E052D"),
	("bms-1-2-leave-error-code", "6BB60189510A0529"),
	("abs-fault-code", "6BB6018951230401"),
	("apn", "6BB60189514D147F"),
	("charging-voltage", "6BB6018951250201"),
	("controller-temperature", "6BB60189511C013B"),
	("soc", "6BB601895109022D"),
	("total-mileage", "6BB601895129040B"),
	("motor-temperature", "6BB60189511D013A"),
	("maximum-speed-subtotal", "6BB60189512B010C"),
	("voltage-of-battery", "6BB6018951050221"),
	("gps-hardware-version", "6BB6018951A00D8B"),
	("gear", "6BB60189511E0139"),
	("charge-and-discharge-times", "6BB6018951000127"),
	("bms-sn", "6BB6018951011136"),
	("mcu-sn", "6BB60189511F0C35"),
	("gps-software-version", "6BB6018951A10D8A"),
	("max-monomer-voltage", "6BB6018951110235"),
	("controller-bus-voltage", "6BB60189518501A2"),
	("max-monomer-voltage-num", "6BB6018951120135"),
	("handle-voltage", "6BB60189518702A3"),
	("max-temperature", "6BB6018951150132"),
	("controller-nominal-voltage", "6BB60189518901AE"),
	("mcu-software-version", "6BB6018951A4088A"),
	("max-temperature-num", "6BB6018951160131"),
	("bms-software-version", "6BB6018951A2088C"),
	("mcu-hardware-version", "6BB6018951A5088B"),
	("bms-hardware-version", "6BB6018951A3088D"),
	("motor-temperature2", "6BB6018951B00197"),
	("powertrain-temperature-percent", "6BB6018951B10196"),
	("ready-light-status", "6BB6018951B20195"),
	("controller-power-reduction-level", "6BB6018951B30194"),
	("front-brake-pressed-status", "6BB6018951B40193"),
	("side-stand-signal-status", "6BB6018951B50192"),
	("rear-brake-pressed-status", "6BB6018951B60191"),
	("left-lever-opening-percent", "6BB6018951B70190"),
	("right-handle-opening-percent", "6BB6018951B8019F"),
	("instrument-ambient-temperature", "6BB6018951BA019D"),
	("instrument-acc-signal-status", "6BB6018951BB019C"),
	("bluetooth-acc-signal-status", "6BB6018951BC019B"),
	("instrument-fault-code", "6BB6018951BD019A"),
	("instrument-sn", "6BB6018951BE0E96"),
	("instrument-hardware-version", "6BB6018951BF0891"),
	("instrument-software-version", "6BB6018951E008CE"),
	("battery-request-charge-gear", "6BB6018951E101C6"),
	("battery-estimated-charge-time", "6BB6018951E201C5"),
	("left-lever-function-set-state", "6BB60189519E01B9"),
)

# A small default poll set worth reading every 10 s. The rest (SNs, versions, SIM/APN,
# static config) is read once or on demand; polling all 66 each cycle is wasteful.
DEFAULT_POLL_STATE_CODES: tuple[str, ...] = (
	"soc",
	"soh",
	"voltage-of-battery",
	"controller-bus-voltage",
	"max-monomer-voltage",
	"min-monomer-voltage",
	"current-speed",
	"motor-speed",
	"controller-temperature",
	"motor-temperature",
	"max-temperature",
	"min-temperature",
	"gear",
	"charge-and-discharge-times",
	"total-mileage",
	"subtotal-mileage",
	"bms-current",
	"handle-voltage",
	"discharge-relay-status",
	"charging-relay-status",
)
