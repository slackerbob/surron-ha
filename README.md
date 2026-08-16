# surron-ha — Sur-Ron OEM controller for Home Assistant

A local Home Assistant integration that reads telemetry — battery level, voltage, health,
temperatures, speed, odometer and more — from a Sur-Ron OEM motor controller over Bluetooth
Low Energy. No cloud, no account needed at runtime. Ships with a standalone command-line
reader so you can use it without Home Assistant too.

Built as a sibling to `ebmx-ha` and sharing its design: one Home Assistant device per bike,
proxy-aware polling, and cached values that survive restarts.

> **Status:** the BLE protocol is fully reverse-engineered and the decoder is implemented
> and validated against a real bike. Battery voltage, state of charge, state of health and
> per-cell voltages are confirmed. A few secondary scales (temperature offset, odometer
> scale, current sign) are best-effort pending a second labelled capture and don't affect
> the battery readout. See [`FINDINGS.md`](FINDINGS.md) for the full reverse-engineering
> write-up.

## Features

- Battery **state of charge** (from the controller) and an independent **voltage-based
  estimate**, plus **state of health** and pack/cell voltages.
- Speed, motor RPM, gear, controller and battery temperatures, odometer, trip, charge
  cycles, and pack current.
- **Presence** binary sensor (on while the bike is advertising) and a **Last updated**
  timestamp.
- Values are cached across Home Assistant restarts, so entities keep their last reading
  even when the bike is powered off and out of range.
- Works through [Bluetooth proxies](https://esphome.io/components/bluetooth_proxy.html)
  transparently.

## How it works

The OEM controller speaks a simple request/response protocol over the **Nordic UART
Service**. The integration writes the per-bike command set (baked in) and decodes the
replies locally — the official app decodes on Sur-Ron's server, but this integration does
it on-device so nothing leaves your network at runtime.

The bike's Bluetooth MAC address is **randomised on every power-cycle**, so the integration
identifies a bike by its **serial number**, which the bike advertises as its BLE name (the
decoded on-board serial matches the advertised name). The coordinator follows whatever
address the bike currently advertises, so power-cycling reconnects it as the same Home
Assistant device.

## Requirements

- Home Assistant 2024.8 or newer with the Bluetooth integration set up (a local adapter or
  an ESPHome/Shelly Bluetooth proxy in range of the bike).
- The bike powered on to be discovered or polled.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a **custom repository** (category: *Integration*).
2. Install **Sur-Ron (OEM)**, then restart Home Assistant.

### Manual

Copy `custom_components/surron/` into your Home Assistant `config/custom_components/`
directory and restart Home Assistant.

## Configuration

Add it from **Settings → Devices & Services → Add Integration → Sur-Ron (OEM)**. A powered-on
bike in range is usually discovered automatically; otherwise pick it from the list of visible
devices, or enter its serial number manually (you can add it before it's in range — it will
connect once seen).

**Options:** you can set the battery's series **cell count** to tune the voltage-based SOC
estimate. If left blank, it's inferred from the pack and per-cell voltages.

## Entities

| Entity | Notes |
|---|---|
| Battery (controller) | SOC reported by the controller (`%`) |
| Battery (estimate) | Voltage-based SOC estimate (`%`) |
| Battery voltage | Pack voltage |
| Battery health | State of health |
| Bus voltage, Max/Min cell voltage | Diagnostic |
| Battery current | Pack current |
| Speed, Motor RPM, Gear | Motion |
| Controller / Motor / Battery temperatures | `°C` |
| Odometer, Trip | Distance |
| Charge cycles | Diagnostic |
| Present | On while advertising |
| Last updated | Timestamp of last successful read |

## Standalone CLI (no Home Assistant)

The same protocol/decoder code runs from the command line — handy for testing on a laptop.
Requires `bleak` (`pip install bleak`).

```bash
python -m custom_components.surron.cli --scan                       # list nearby devices
python -m custom_components.surron.cli --name "QL-XXXXXXXXXXXXX" --once
python -m custom_components.surron.cli --name "QL-XXXXXXXXXXXXX" --all --json
```

Use `--name` (the bike's serial / advertised name) rather than a MAC, since the MAC changes
each power-cycle.

## Development & tests

The `protocol`, `telemetry`, `client`, `const` and `models` modules import neither Home
Assistant nor `bleak`, so the core is unit-testable and runnable standalone.

```bash
pip install -r requirements_test.txt
pytest -q
```

CI (GitHub Actions) runs `hassfest`, HACS validation, and these tests on every push and PR.

### Reverse-engineering tools

Under `tools/` (standalone; used to build and extend the decoder):

- `fetch_commands.py` — one-time, standard-library-only fetch of the per-bike command list
  from your Sur-Ron account. Needed only to (re)generate the command set; not used at
  runtime.
- `capture.py` — probe the controller and record raw responses (label them with the
  dashboard battery %) to validate or extend the decoder.

Both write files (`surron_cloud*.json`, `surron_capture_*.jsonl`) that contain your bike's
serial/IMEI/etc. — these are git-ignored; **don't commit them**.

## Before you publish this repo

Replace `OWNER` in `custom_components/surron/manifest.json` (`documentation` and
`issue_tracker` URLs) with your GitHub org/user. To pass the HACS `brands` check, register
the `surron` domain in [home-assistant/brands](https://github.com/home-assistant/brands),
then remove the `ignore: brands` line in `.github/workflows/ci.yml`.

## Privacy

Everything runs locally; at runtime the integration never contacts Sur-Ron's servers. The
`tools/` scripts talk to Sur-Ron only when you explicitly run them, using your own
credentials. Capture/command dumps contain identifiers (serial, IMEI, SIM) and are
git-ignored by default.

## Disclaimer

Not affiliated with or endorsed by Sur-Ron. Use at your own risk. Reverse-engineered for
interoperability with hardware you own.

## License

No license is included yet — add one of your choice (for example MIT) before publishing;
without a license, others have no rights to reuse the code.
