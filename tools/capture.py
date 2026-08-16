"""Standalone Sur-Ron OEM controller probe/capture tool (no Home Assistant needed).

v3 changes (after a silent v2 capture):
- Logs the negotiated ATT MTU (the official app raises MTU to 512 after connecting; if the
  MTU is small here that may be why the controller stays silent).
- Waits after connecting and after subscribing before writing (mimics the app's timing).
- Tries BOTH write types per command (with-response, then without-response).
- Longer reply window and an optional trailing "hold" listen for delayed/streamed frames.
- Still auto-discovers every (service, write-char, notify-char) channel and probes each.

Usage
-----
    pip install bleak
    python capture.py --name "QL-XXXXXXXXXXXXX" --soc 100 --note "level 1"

Read battery % off the bike's dash and pass it as --soc (the dash has no voltage, that's
fine). Run at DIFFERENT battery levels, then send back the surron_capture_*.jsonl files.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from datetime import datetime, timezone

try:
	from bleak import BleakClient, BleakScanner
	from bleak.backends.characteristic import BleakGATTCharacteristic
except ImportError:  # pragma: no cover
	print("This tool needs bleak. Install it with:  pip install bleak", file=sys.stderr)
	raise

MIN_VALID_RESPONSE_BYTES = 5

CANDIDATE_COMMANDS: tuple[str, ...] = (
	"6BB6018954CC01EB",
	"6BB6018955CC00EB",
	"6BB6018954CC02EB",
	"6BB6018954CC03EB",
	"6BB60189522D01",
	"6BB60189512D0209",
)

_SKIP_SERVICES = {
	"00001800-0000-1000-8000-00805f9b34fb",
	"00001801-0000-1000-8000-00805f9b34fb",
	"0000180a-0000-1000-8000-00805f9b34fb",
}


def _load_commands_file(path: str) -> list[str]:
	"""Pull btReadCommand hex strings out of a fetch_commands.py output file.

	Accepts either the full cloud dump ({loadAllStateBtCommand:{data:[...]}}) or a bare list
	of {btReadCommand} / hex strings.
	"""
	with open(path, encoding="utf-8") as handle:
		doc = json.load(handle)
	items = doc
	if isinstance(doc, dict):
		items = (doc.get("loadAllStateBtCommand") or {}).get("data") or doc.get("data") or []
	commands: list[str] = []
	for item in items:
		if isinstance(item, str):
			commands.append(item.upper())
		elif isinstance(item, dict) and item.get("btReadCommand"):
			commands.append(str(item["btReadCommand"]).upper())
	return commands


def _now() -> str:
	return datetime.now(timezone.utc).isoformat()


def _hex(data: bytes) -> str:
	return data.hex().upper()


def _has(char: BleakGATTCharacteristic, *props: str) -> bool:
	have = set(char.properties)
	return any(p in have for p in props)


async def scan(timeout: float = 10.0) -> int:
	print(f"Scanning {timeout:.0f}s for BLE devices...", file=sys.stderr)
	found = await BleakScanner.discover(timeout=timeout, return_adv=True)
	if not found:
		print("No BLE devices seen.", file=sys.stderr)
		return 0
	for dev, adv in found.values():
		uuids = ",".join(adv.service_uuids or []) or "(none advertised)"
		print(f"{dev.address}  name={dev.name!r}  rssi={adv.rssi}  adv_services=[{uuids}]")
	return 0


async def _resolve_address(args: argparse.Namespace) -> str | None:
	if args.address:
		return args.address
	print(f"Scanning for a device named {args.name!r}...", file=sys.stderr)
	found = await BleakScanner.discover(timeout=args.scan_timeout, return_adv=True)
	for dev, _adv in found.values():
		if dev.name and dev.name == args.name:
			print(f"Found {args.name!r} at {dev.address}", file=sys.stderr)
			return dev.address
	print(f"Could not find a device named {args.name!r}.", file=sys.stderr)
	return None


def _discover_channels(client: BleakClient) -> list[dict]:
	channels: list[dict] = []
	for service in client.services:
		if service.uuid.lower() in _SKIP_SERVICES:
			continue
		writes = [c for c in service.characteristics if _has(c, "write", "write-without-response")]
		notifies = [c for c in service.characteristics if _has(c, "notify", "indicate")]
		for nchar in notifies:
			for wchar in writes:
				channels.append(
					{
						"service": service.uuid,
						"write_uuid": wchar.uuid,
						"write_char": wchar,
						"write_props": list(wchar.properties),
						"notify_uuid": nchar.uuid,
						"notify_char": nchar,
						"notify_props": list(nchar.properties),
						"_rank": (0 if _has(nchar, "notify") else 1, 0 if _has(wchar, "write") else 1),
					}
				)
	channels.sort(key=lambda c: c["_rank"])
	return channels


async def _try_raise_mtu(client: BleakClient) -> int | None:
	"""Best-effort: report MTU, and on BlueZ attempt to force an exchange. Returns mtu size."""
	mtu = None
	with contextlib.suppress(Exception):
		mtu = client.mtu_size
	# BlueZ-only private hook; harmless elsewhere.
	backend = getattr(client, "_backend", None)
	acquire = getattr(backend, "_acquire_mtu", None)
	if acquire is not None and (mtu is None or mtu <= 23):
		with contextlib.suppress(Exception):
			await acquire()
			mtu = client.mtu_size
	return mtu


async def run_capture(args: argparse.Namespace) -> int:
	address = await _resolve_address(args)
	if not address:
		return 1

	labels = {
		"pack_voltage_v": args.volts,
		"battery_soc_percent": args.soc,
		"odometer_km": args.odo,
		"note": args.note,
	}
	out_path = args.out or f"surron_capture_{address.replace(':', '')}_{int(datetime.now().timestamp())}.jsonl"
	channel_records: list[dict] = []

	print(f"Connecting to {address} ...", file=sys.stderr)
	async with BleakClient(address, timeout=20.0) as client:
		mtu = await _try_raise_mtu(client)
		print(f"Negotiated ATT MTU: {mtu}", file=sys.stderr)
		if mtu is not None and mtu <= 23:
			print("  WARNING: MTU is at the 23-byte minimum; the app uses 512. This may be "
				  "why the controller stays silent.", file=sys.stderr)

		if args.post_connect_delay > 0:
			print(f"Waiting {args.post_connect_delay:.0f}s after connect (like the app)...", file=sys.stderr)
			await asyncio.sleep(args.post_connect_delay)

		channels = _discover_channels(client)
		if not channels:
			print("No writable+notify channels found. Full GATT table:", file=sys.stderr)
			for service in client.services:
				print(f"  service {service.uuid}")
				for char in service.characteristics:
					print(f"    char {char.uuid}  props={list(char.properties)}")
			return 2

		print(f"\nFound {len(channels)} candidate channel(s).", file=sys.stderr)
		if args.command:
			commands = [args.command.upper()]
		elif args.commands:
			commands = _load_commands_file(args.commands)
			print(f"Loaded {len(commands)} real commands from {args.commands}", file=sys.stderr)
		else:
			commands = list(CANDIDATE_COMMANDS)

		for ch in channels:
			inflight: list[dict] = []

			def on_notify(_char, data: bytearray, _bucket=inflight) -> None:
				payload = bytes(data)
				entry = {"ts": _now(), "len": len(payload), "hex": _hex(payload)}
				_bucket.append(entry)
				tag = "valid" if len(payload) > MIN_VALID_RESPONSE_BYTES else "short"
				print(f"    <- NOTIFY [{tag}] {len(payload)}B  {entry['hex']}")

			print(
				f"\n=== Channel: write {ch['write_uuid']} ({ch['write_props']}) / "
				f"notify {ch['notify_uuid']} ({ch['notify_props']}) ===",
				file=sys.stderr,
			)
			probes: list[dict] = []
			try:
				await client.start_notify(ch["notify_char"], on_notify)
			except Exception as exc:  # noqa: BLE001
				print(f"    (cannot subscribe here: {exc}) — skipping channel", file=sys.stderr)
				channel_records.append({**{k: ch[k] for k in ("service", "write_uuid", "notify_uuid")},
										"error": str(exc), "probes": []})
				continue

			if args.post_subscribe_delay > 0:
				await asyncio.sleep(args.post_subscribe_delay)

			# Passive: does it stream once notify is on (and MTU raised)?
			if args.passive > 0:
				inflight.clear()
				print(f"    passive listen {args.passive:.0f}s...", file=sys.stderr)
				await asyncio.sleep(args.passive)
				if inflight:
					probes.append({"command_hex": None, "phase": "passive",
								   "notifications": list(inflight)})

			write_modes = [("with_response", True), ("without_response", False)]
			# only try write-without-response if the char supports it
			if "write-without-response" not in ch["write_props"]:
				write_modes = [("with_response", True)]
			if "write" not in ch["write_props"]:
				write_modes = [("without_response", False)]

			for cmd_hex in commands:
				for mode_name, use_resp in write_modes:
					inflight.clear()
					frame = bytes.fromhex(cmd_hex)
					print(f"\n-> WRITE {cmd_hex} [{mode_name}] on {ch['write_uuid']}")
					try:
						await client.write_gatt_char(ch["write_char"], frame, response=use_resp)
					except Exception as exc:  # noqa: BLE001
						print(f"    (write failed: {exc})", file=sys.stderr)
						probes.append({"command_hex": cmd_hex, "write_mode": mode_name,
									   "write_error": str(exc), "notifications": []})
						continue
					await asyncio.sleep(args.window)
					probes.append({
						"command_hex": cmd_hex,
						"write_mode": mode_name,
						"notifications": list(inflight),
						"combined_hex": "".join(n["hex"] for n in inflight),
					})

			# Trailing hold in case responses are delayed.
			if args.hold > 0:
				inflight.clear()
				print(f"    holding {args.hold:.0f}s for any delayed frames...", file=sys.stderr)
				await asyncio.sleep(args.hold)
				if inflight:
					probes.append({"command_hex": None, "phase": "hold",
								   "notifications": list(inflight)})

			with contextlib.suppress(Exception):
				await client.stop_notify(ch["notify_char"])

			channel_records.append({
				"service": ch["service"],
				"write_uuid": ch["write_uuid"], "write_props": ch["write_props"],
				"notify_uuid": ch["notify_uuid"], "notify_props": ch["notify_props"],
				"probes": probes,
			})

	capture = {
		"schema": "surron-capture/3",
		"captured_at": _now(),
		"address": address,
		"mtu": mtu,
		"labels": labels,
		"channels": channel_records,
	}
	with open(out_path, "w", encoding="utf-8") as handle:
		json.dump(capture, handle, indent=2)
	print(f"\nSaved capture to {out_path}", file=sys.stderr)
	any_notif = any(
		p.get("notifications") for ch in channel_records for p in ch.get("probes", [])
	)
	if not any_notif:
		print(
			"\nStill no responses on any channel/write-mode. That strongly suggests the seed "
			"commands aren't valid for this controller — the real command set is downloaded "
			"per-bike from Sur-Ron's server. See the chat for the two reliable ways to get the "
			"real commands.", file=sys.stderr,
		)
	return 0


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Sur-Ron OEM controller BLE probe/capture (v3)")
	parser.add_argument("--scan", action="store_true", help="Scan for devices and exit")
	parser.add_argument("--address", help="Connect directly to this MAC address")
	parser.add_argument("--name", help="Match the bike by advertised BLE name (= serial)")
	parser.add_argument("--command", help="Send only this one hex command")
	parser.add_argument("--commands", help="Path to fetch_commands.py output; probe those real commands")
	parser.add_argument("--window", type=float, default=2.0, help="Seconds to collect replies per command/mode")
	parser.add_argument("--passive", type=float, default=4.0, help="Passive-listen seconds after subscribe")
	parser.add_argument("--hold", type=float, default=4.0, help="Trailing listen seconds for delayed frames")
	parser.add_argument("--post-connect-delay", type=float, default=3.0, help="Delay after connect before probing")
	parser.add_argument("--post-subscribe-delay", type=float, default=1.0, help="Delay after enabling notify")
	parser.add_argument("--scan-timeout", type=float, default=10.0, help="Scan timeout when resolving --name")
	parser.add_argument("--out", help="Output JSONL path (default: auto)")
	parser.add_argument("--volts", type=float, help="Observed pack voltage (V), if the dash shows it")
	parser.add_argument("--soc", type=float, help="Observed battery percentage (from the dash)")
	parser.add_argument("--odo", type=float, help="Observed odometer (km)")
	parser.add_argument("--note", default="", help="Free-text note")
	args = parser.parse_args(argv)

	if args.scan:
		return asyncio.run(scan(args.scan_timeout))
	if not args.address and not args.name:
		parser.error("give --scan, or --address <MAC>, or --name <serial>")
	return asyncio.run(run_capture(args))


if __name__ == "__main__":
	with contextlib.suppress(KeyboardInterrupt):
		sys.exit(main())
