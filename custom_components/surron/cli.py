"""Standalone command-line runner — talk to a bike without Home Assistant.

Uses bleak directly and shares the exact protocol/client/decoder code the integration
uses, so it's ideal for testing on a laptop and for validating the decoder. For gathering
raw captures, use tools/capture.py.

    python -m custom_components.surron.cli --scan
    python -m custom_components.surron.cli --name "QL-XXXXXXXXXXXXX" [--json] [--once]
    python -m custom_components.surron.cli --address AA:BB:CC:DD:EE:FF [--all] [--cells 20]

The bike's MAC is random per power-cycle, so --name (its advertised serial) is the stable
way to find it; --address still works within a single power session.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
from datetime import datetime, timezone

from .client import SurronBleClient
from .const import COMMANDS, DEFAULT_POLL_STATE_CODES, NUS_SERVICE_UUID
from .telemetry import estimate_soc_percent, infer_cells

_LOGGER = logging.getLogger("surron.cli")


_SKIP_SERVICES = {
	"00001800-0000-1000-8000-00805f9b34fb",
	"00001801-0000-1000-8000-00805f9b34fb",
	"0000180a-0000-1000-8000-00805f9b34fb",
}


def _select_characteristics(client):
	"""Pick (write, notify) chars like the app: first non-generic service with both."""
	for service in client.services:
		if service.uuid.lower() in _SKIP_SERVICES:
			continue
		write_char = notify_char = None
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


def _commands(read_all_fields: bool) -> list[tuple[str, str]]:
	"""The command set to run: the 10 s poll subset by default, or every command."""
	if read_all_fields:
		return list(COMMANDS)
	poll = set(DEFAULT_POLL_STATE_CODES)
	return [(state_code, req) for state_code, req in COMMANDS if state_code in poll]


async def _resolve_address(name: str, timeout: float) -> str | None:
	from bleak import BleakScanner

	print(f"Scanning for a bike named {name!r}...", file=sys.stderr)
	found = await BleakScanner.discover(timeout=timeout, return_adv=True)
	for dev, _adv in found.values():
		if dev.name == name:
			print(f"Found {name!r} at {dev.address}", file=sys.stderr)
			return dev.address
	return None


async def _scan() -> int:
	from bleak import BleakScanner

	print("Scanning 10 s for BLE devices...", file=sys.stderr)
	devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
	for dev, adv in devices.values():
		uuids = [u.lower() for u in (adv.service_uuids or [])]
		tag = "  <-- Nordic UART" if NUS_SERVICE_UUID.lower() in uuids else ""
		print(f"{dev.address}  {dev.name or '(unnamed)'}  rssi={adv.rssi}{tag}")
	return 0


async def _run(args: argparse.Namespace) -> int:
	from bleak import BleakClient

	address = args.address
	if not address and args.name:
		address = await _resolve_address(args.name, args.scan_timeout)
		if not address:
			_LOGGER.error("could not find a bike named %r", args.name)
			return 1

	commands = _commands(args.all)
	while True:
		try:
			async with BleakClient(address, timeout=20.0) as client:
				write_char, notify_char = _select_characteristics(client)
				if write_char is None or notify_char is None:
					_LOGGER.error("no write+notify characteristics found")
					return 2
				surron = SurronBleClient(client, write_char, notify_char)
				await surron.start()
				while True:
					telemetry = await surron.read_all(commands)
					cells = args.cells or infer_cells(
						telemetry.pack_voltage, telemetry.max_cell_voltage
					)
					soc = estimate_soc_percent(telemetry.pack_voltage, cells)
					if args.json:
						print(
							json.dumps(
								{
									"timestamp": datetime.now(timezone.utc).isoformat(),
									"voltage": telemetry.pack_voltage,
									"socController": telemetry.controller_battery_percent,
									"socEstimate": round(soc, 1) if soc is not None else None,
									"cells": cells,
									"values": telemetry.values,
									"raw": telemetry.raw,
								}
							)
						)
					else:
						_LOGGER.info(
							"V=%s SOC(ctrl)=%s%% SOC(est)=%s SOH=%s%% | %s km, %s cycles, "
							"ctrlT=%s°C, speed=%s km/h, cells=%s",
							telemetry.pack_voltage,
							telemetry.controller_battery_percent,
							f"{soc:.0f}" if soc is not None else "n/a",
							telemetry.soh_percent,
							telemetry.odometer_km,
							telemetry.cycle_count,
							telemetry.controller_temp,
							telemetry.speed_kph,
							cells,
						)
					if args.once:
						return 0
					await asyncio.sleep(args.interval)
		except asyncio.CancelledError:
			return 0
		except Exception as exc:  # noqa: BLE001
			if args.once:
				_LOGGER.error("Failed: %s", exc)
				return 1
			_LOGGER.warning("Connection lost/failed (%s); retrying in 2 s...", exc)
			await asyncio.sleep(2)


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Sur-Ron OEM standalone reader")
	parser.add_argument("--address", help="Bike Bluetooth MAC (valid within one power session)")
	parser.add_argument("--name", help="Bike advertised name / serial (stable identifier)")
	parser.add_argument("--scan", action="store_true", help="Scan for devices and exit")
	parser.add_argument("--all", action="store_true", help="Read every command, not just the poll subset")
	parser.add_argument("--json", action="store_true", help="Emit JSON lines on stdout")
	parser.add_argument("--once", action="store_true", help="Read one sample and exit")
	parser.add_argument("--interval", type=float, default=10.0, help="Poll interval (s)")
	parser.add_argument("--cells", type=int, default=0, help="Series cell-count override")
	parser.add_argument("--scan-timeout", type=float, default=10.0, help="Name-resolve scan timeout")
	parser.add_argument("--verbose", action="store_true", help="Debug logging")
	args = parser.parse_args(argv)

	logging.basicConfig(
		level=logging.DEBUG if args.verbose else logging.INFO,
		format="%(asctime)s %(levelname)s %(message)s",
		stream=sys.stderr,
	)

	if args.scan:
		return asyncio.run(_scan())
	if not args.address and not args.name:
		parser.error("give --scan, --name <serial>, or --address <MAC>")
	try:
		return asyncio.run(_run(args))
	except KeyboardInterrupt:
		return 0


if __name__ == "__main__":
	with contextlib.suppress(KeyboardInterrupt):
		sys.exit(main())
