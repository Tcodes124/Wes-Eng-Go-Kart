# Pit Wall dashboard

The race engineer's live dashboard (architecture.md section 8). A small Python
server on the pit laptop reads telemetry from the pit LoRa board over USB, and
the dashboard opens in any browser. **No internet needed.**

## Run it

You need Python 3.9 or newer and pyserial (`python3 -m pip install pyserial`).
From this `pit` folder:

```bash
python3 server.py --simulate
```

Then open http://localhost:8000.

| Command | What it does |
|---|---|
| `python3 server.py --simulate` | Fake kart, real time. Use it to try the UI with no hardware |
| `python3 server.py --simulate --sim-speed 5` | Fake kart, 5x faster, so laps and battery drain show up quickly |
| `python3 server.py --port /dev/cu.usbserial-0001` | Real pit LoRa board. Find the port with `python3 -m serial.tools.list_ports` |
| add `--share` | Teammates on the same WiFi can open `http://<laptop IP>:8000` on their phones |

Run the tests with `python3 -m unittest -v`.

## What's on the screen

- **Top bar:** LIVE / NO SIGNAL, packets per second, % of packets lost.
- **Alerts:** red banners for "won't finish", overheating, lost signal, and so on.
- **Lap:** current lap time, last lap, best lap, speed.
- **Battery:** charge %, voltage, current, power, and temperatures against their limits.
- **Energy strategy:** Wh per lap (average of the last 3 full laps), laps the
  battery can still do, laps left in the race, and a verdict (ON TARGET / TIGHT / WON'T FINISH).
  Change race laps and pack size right on the page.
- **Track map:** GPS trail colored by speed, with the kart's current position.
- **G-force:** recent peak cornering and braking/accel G.
- **Message the driver:** sends a preset message. It shows *sending*, then
  *on the driver's screen*, then *acknowledged* when the driver presses the button.
  Messages are resent every 0.5 s until they arrive (gives up after 15 s).
- **Laps:** every lap with gap to best, energy used, battery % used, top speed.

Every packet received is also saved to `logs/session-<date>-<time>.csv` (not committed to git).

## Settings

`config.json` holds the race length, pack size, and alert limits. **These are
placeholders.** Replace them with real numbers from our pack and datasheets.

## Files

| File | Purpose |
|---|---|
| `telemetry.py` | Packet byte layouts, checksum, and USB line format. Must match the kart firmware |
| `simulator.py` | Fake kart with rough physics, for testing without hardware |
| `server.py` | Reads packets, works out laps, energy and alerts, and serves the page |
| `static/` | The dashboard page (HTML, CSS, JavaScript, no libraries) |
| `test_telemetry.py` | Tests for the packet code and simulator |

## What the pit LoRa board firmware must do

The ESP32 + LoRa board in the pit just passes packets between the radio and the laptop:

- **Radio → laptop:** for every packet received, print one line: `T:` + the packet
  as hex + newline. Example: `T:0101002a...`
- **Laptop → radio:** read lines from USB. For each `M:<hex>` line, send those
  bytes to the kart in its listen window right after the next uplink.
- Baud rate **115200**. Any other lines it prints (debug messages) are ignored.

## Firmware details that must match `telemetry.py`

- Uplink is the 46-byte packet in architecture.md 7.2, little-endian.
- Checksum is **CRC-16/CCITT-FALSE** (poly 0x1021, start 0xFFFF) over every byte before
  the CRC. Test it: the CRC of the ASCII text `123456789` must be `0x29B1`.
- **`flags` byte:** bit 0 = GPS fix, bit 1 = SD logging OK, bit 2 = driver acknowledged
  the message, and **bits 4–7 = the low 4 bits of the last `msg_id` the kart received.**
  That echo is how the pit knows a message arrived. Because it's 4 bits, `msg_id` cycles 1–15.
- Driver message codes: 1 PUSH, 2 SAVE BATT, 3 PIT, 4 TEMP HIGH, 5 GOOD LAP,
  6/7/8 = losing time in sector 1/2/3.
- `last_lap_ms` / `best_lap_ms` are 0 until the first lap is completed.
