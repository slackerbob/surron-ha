# surron-ha — reverse-engineering findings

Analysis of the official Sur-Ron **oversea** app (`com.surron.oversea`) and comparison
with `ebmx-ha`, to plan a local Home Assistant integration for the OEM controller.

## The good news: the BLE transport is fully recovered

Everything needed to *talk* to the controller is in the app and is confirmed:

- **Service:** vendor GATT service `0xFA01` (`0000fa01-0000-1000-8000-00805f9b34fb`).
- **Characteristics:** not hard-coded. The app discovers the service and picks the
  characteristic with the **write** property (commands) and the one with the **notify**
  property (responses). We do the same.
- **Discovery:** the bike advertises with its **BLE name equal to the vehicle serial
  number (SN)**; the app matches a scanned device by `name == SN`. (So we can identify a
  bike by name/SN, and by MAC once known — same as ebmx-ha's manual mode.)
- **Wire format:** a command is a hex string written **verbatim** to the write char (no
  added framing/checksum by the app). A response arrives as one or more notifications;
  the app treats any notification **longer than 5 bytes** as the valid reply.
- **Known commands (seeds):** the app's own BLE self-test hard-codes these real commands:
  `6BB6018954CC01EB`, `6BB6018955CC00EB`, `6BB6018954CC02EB`, `6BB6018954CC03EB`,
  `6BB60189522D01`, `6BB60189512D0209`. They share a `6BB60189…` header; note the
  framing differs per family (some end `EB`, others don't), i.e. they're opaque blobs.

## The blocker: the app contains **no decoder**

This is the decisive difference from the EBMX X-9000, and it changes the plan.

The EBMX is a VESC-derived controller: ebmx-ha decodes `COMM_GET_VALUES` locally from a
known field map, so it needs no cloud. The Sur-Ron OEM app is **not** like that. It is a
uni-app-x build that acts as a **dumb pipe to Sur-Ron's cloud**:

1. `GET motor-state/loadAllStateBtCommand` → the server returns the list of commands to
   send (`{stateCode, btReadCommand}`), per bike.
2. The app writes each `btReadCommand`, collects the raw notify bytes as hex.
3. `POST motor-state/saveOrUpdateState` → the app **uploads the raw hex** to the server.
4. `GET motor-state/loadStateListByType` → the server returns **already-decoded** display
   items (`{name, value, unit, …}`). The app only formats precision/units client-side.

I verified there is **no byte-offset/scale logic and no bundled command list anywhere**
in the app or its assets — the mapping from raw controller bytes → voltage/SOC/temp/etc.
lives on `osgateway.sur-ron.com`, not on the device. So it can't be lifted from the APK.

## What this means for the two goals

- Reusing your **voltage-based SOC estimate** from ebmx-ha: no problem — it's ported
  verbatim (`telemetry.estimate_soc_percent`). It only needs pack voltage + cell count.
- Getting **battery level locally**: we must recover the response byte layout ourselves,
  because it isn't in the app. That requires a handful of request/response captures off a
  real bike, labelled with what the dash shows (this is exactly how ebmx-ha was validated
  against "real captured frames"). The `tools/capture.py` script does this.

## Two ways forward

**A. Local reverse-engineering (recommended — matches ebmx-ha's offline design).**
You run `tools/capture.py` against the bike a few times at different battery levels; I use
those labelled captures to identify which command returns voltage/SOC and at what
offset/scale, fill in `telemetry.decode_frame`, and then the rest of the stack (coordinator
via HA Bluetooth proxy, 10 s polling, per-bike devices, caching, the SOC estimate) is a
straight port of ebmx-ha. Fully local, no Sur-Ron account, no cloud.

**B. Cloud-assisted.** Reuse Sur-Ron's endpoints (needs your account/token): fetch the
command list, drive BLE locally, upload raw bytes, read back decoded values. Faster to
first light and always matches the app, but depends on Sur-Ron's cloud and login, so it
is not local/offline and is brittle if their API changes. Contradicts the ebmx-ha spirit.

## Already scaffolded here

- `custom_components/surron/const.py` — service `0xFA01`, characteristic-by-property
  selection, 10 s poll, `>5 byte` validity rule, the candidate command seeds.
- `custom_components/surron/telemetry.py` — the ebmx-ha SOC estimate (verbatim) + a
  documented `decode_frame` stub awaiting captures.
- `tools/capture.py` — standalone probe/capture tool (bleak only).

## Correction (after a live GATT dump)

A capture attempt showed the bike does **not** expose `0xFA01`. Re-reading the app, the
`FA01` match belongs only to the separate BLE **bike-key/unlock** feature. The **telemetry**
path matches **no fixed UUID** — it walks the services and commits to the first one that has
both a **write** and a **notify** characteristic. On a real bike that is the **Nordic UART
Service** (`6e400001…`, write `6e400002`, notify `6e400003`) — the *same transport the EBMX
uses*, carrying the OEM's `6BB60189…` protocol instead of VESC.

The integration and tools now select characteristics by property (write + notify), matching
the app, and default to / advertise NUS. Everything else in the plan is unchanged: the
response decoder still has to be built from labelled captures.

## Correction 2 (after silent captures + your decision to use the cloud)

Two seed-command captures came back completely silent (zero notifications on every
channel/write-mode). Re-reading the app confirmed why: the six seed commands live only in a
developer test function; the shipping app ALWAYS downloads its command set per-bike from the
server and sends those. There is no handshake/auth on the telemetry path (the only auth is
the unrelated bike-key feature), and the sole connect-time extra is raising MTU to 512.

Auth/endpoints (reconstructed for the one-time cloud fetch, all under
https://osgateway.sur-ron.com/):
- login: POST md-app-common/loginByPassword  body {email, password: MD5(pw).upper()}
  -> {code:200, data:{token, userId, bindBikeCount}}
- authed GETs send a `token` header; envelope is {code, data, msg}
- md-app/motor-state/loadAllStateBtCommand -> [{stateCode, btReadCommand}]  (the real commands)
- md-app/motor-state/loadStateTypeList, motor/myMotorList, motor/myActiveMotor (context)
- (later, for decoding) md-app/motor-state/saveOrUpdateState (POST raw) +
  motor-state/loadStateListByType (GET decoded) — the server-side decoder we can diff against.

Plan: fetch_commands.py gets the real commands once; capture.py --commands probes them and
records raw responses at known battery %; then the decoder is built from those (optionally
cross-checked against the server's own decode). Runtime stays fully offline.

Also confirmed operationally: the bike's MAC is dynamic per power-cycle, so we key on the
BLE name (= serial), which is stable. The dash shows battery % but not voltage, so battery %
is our decode ground-truth.

## RESULT: protocol fully decoded and validated (live bike, 100% charge)

The real command set (66 commands from the cloud) got 47 answers on the Nordic UART
channel at MTU 517. Frame format and checksum are confirmed on all frames:

    6B B6 | LEN | TYPE | CMD | REG | DATA[LEN] | CKSUM
    request  TYPE=0x89 CMD=0x51    response TYPE=0x62 CMD=0x54
    CKSUM = (~XOR(bytes after the 6BB6 magic, excluding CKSUM)) & 0xFF
    numeric fields are little-endian; the reply echoes the request's REG

Decoded at a dashboard-confirmed 100% charge (self-validating: gps-sn == the connected
BLE name/serial):

    soc                     99.7 %       (u16 LE / 100)
    soh                     92.0 %       (u16 LE / 100)
    voltage-of-battery      84.0 V       (u16 LE / 10)      <- primary battery goal
    controller-bus-voltage  84   V       (u8)
    max/min cell voltage    4.218/4.184  (u16 LE / 1000)
    cells inferred          20           (pack / max-cell)
    controller temperature  27 °C        (u8, offset -40)
    battery max/min temp    25/24 °C     (u8, offset -40)
    motor temperature       none         (0xFF sentinel)
    speed / motor rpm/ gear 0 / 0 / 0    (parked)
    charge cycles           37
    total / trip mileage    2497.8 / 498.0 km  (LE / 10)
    gps-sn / bms-sn / mcu-sn / imei / apn / versions   ASCII strings

Confirmed vs pending: battery voltage, SOC, SOH and per-cell voltages are physically
consistent and trusted. Still best-effort until a second labelled capture: the -40
temperature offset, the /10 mileage scale, and the current sign/scale (all currents read
0 while parked). A capture at a clearly different SOC, after a short ride, and during
charging will pin those down; nothing about the battery result depends on them.

The request checksum uses the same formula as responses (verified on all 66), so requests
could be built from scratch if ever needed — but we replay the server bytes verbatim.

## HA integration: identity model (name/serial, not MAC)

Because the bike's MAC is random per power-cycle, the integration identifies a bike by its
**serial number**, which the bike advertises as its BLE name (confirmed: the decoded
`gps-sn` equals the advertised name). Design:

* The config entry's `unique_id`, the HA device identifier, and every entity `unique_id`
  are the serial — so a bike survives power-cycles and reconnects as the same device.
* The coordinator does not bind to a fixed address. It registers a Bluetooth callback
  matched by name (`BluetoothCallbackMatcher(local_name=serial)`), and each matching
  advertisement updates the tracked address, refreshes presence, and (debounced to ~10 s)
  triggers a connect-and-read via `establish_connection` on the advert's BLEDevice.
* Presence is advertisement-based: present if a matching advert was seen within
  `PRESENCE_TIMEOUT_SECONDS`; it lapses shortly after the bike powers off.
* The last-seen MAC is stored only as a hint (`CONF_ADDRESS`), never as identity.

The manifest still lists the Nordic UART service UUID as a discovery matcher (best-effort);
the primary add paths are the visible-device picker (by name) and manual serial entry.
