# Go-Kart Telemetry System — Architecture

> Electrical team design doc. This is a living document: update it whenever a decision changes.
> Anything marked **TBD** is still open (see [Section 11](#11-open-questions)).

---

## 1. Overview

### Goal
Build a telemetry system for our **electric** go-kart that:
1. **Measures** speed, GPS position, acceleration / G-force, battery voltage, current, charge left, and temperatures.
2. **Shows the driver** current lap, last lap, best lap, the gap to their best lap, and battery %.
3. **Logs everything** to an SD card for analysis after each session.
4. **Sends live data to the race engineer** in the pit over radio, and lets the engineer send short messages back to the driver.

### Scope
This doc covers everything the electrical team builds: sensors, the on-kart computer, the driver display, the radio link, and the pit dashboard.
It does **not** cover the kart's main drive system (motor, motor controller, main battery wiring). We only *measure* those.

### Design principles
- **Logging first.** The SD card saves everything at full rate. The radio only sends a summary. If the radio drops out, no data is lost.
- **Build in stages.** Every phase ends with something that works on the kart (see [Section 10](#10-build-phases)).
- **Built for vibration.** Nothing on a breadboard goes on the kart.
- **Never interferes with the kart.** Telemetry has its own fuse. If it fails, the kart still drives and the kill switch still works.

---

## 2. System Diagram

```
                         ON THE KART
 ┌────────────┐ ┌──────┐ ┌──────┐ ┌──────────────┐ ┌──────────────┐
 │ Hall speed │ │ GPS  │ │ IMU  │ │ Battery V/I  │ │ Temp sensors │
 │  sensor    │ │ M9N  │ │BNO085│ │ shunt+INA228 │ │ batt / motor │
 └─────┬──────┘ └──┬───┘ └──┬───┘ └──────┬───────┘ └──────┬───────┘
   interrupt     UART     SPI/I2C       I2C            1-Wire
       └───────────┴────────┴────────────┴────────────────┘
                              │
                 ┌────────────▼─────────────┐
                 │   ESP32-S3 + LoRa radio  │
                 │   (main kart computer)   │
                 └──┬──────────┬─────────┬──┘
                SPI │      SPI │         │ LoRa 915 MHz
          ┌─────────▼──┐ ┌─────▼────┐    │
          │  Driver    │ │ microSD  │    │
          │  display   │ │ logging  │    │
          │  + alert   │ └──────────┘    │
          │    LED     │                 │
          └────────────┘                 │
                                 ~ ~ ~ wireless ~ ~ ~
                                         │   (both directions)
                         IN THE PIT      │
                 ┌───────────────────────▼──┐   USB   ┌─────────────────────┐
                 │  ESP32 + LoRa (receiver) ├────────►│ Laptop: Python      │
                 └──────────────────────────┘         │ live dashboard      │
                                                      └─────────────────────┘
```

### Power path
```
Main pack (+) ──► Fuse (2 A) ──► Reverse-polarity protection ──► DC-DC (pack V → 5 V) ──► ESP32 board ──► 3.3 V sensors
Main pack (−) ──────────────────────────────────────────────────► Common ground (single star point)
```

---

## 3. Parts List

Prices are rough estimates in USD. Check them before ordering.

| # | Part | Suggested model | Purpose | Qty | Est. cost |
|---|------|-----------------|---------|-----|-----------|
| 1 | Main kart computer | Heltec WiFi LoRa 32 V3 (ESP32-S3 + SX1262) | Reads sensors, timing, display, logging, radio | 1 | $20 |
| 2 | Pit receiver | Heltec WiFi LoRa 32 V3 (same board) | Receives telemetry, sends messages to the driver | 1 | $20 |
| 3 | 915 MHz antennas | Whip antennas with u.FL/SMA pigtail | Radio range | 2 | $15 |
| 4 | Speed sensor | DRV5023 or A3144 Hall effect sensor | Wheel speed | 2 (1 spare) | $3 |
| 5 | Magnets | Small neodymium magnets | Trigger the Hall sensor | 4–8 | $5 |
| 6 | GPS | u-blox NEO-M9N module (10 Hz+) | Position, lap timing, track map | 1 | $45–70 |
| 7 | IMU | BNO085 breakout | Acceleration, G-force, orientation | 1 | $25 |
| 8 | Current shunt | **TBD** rating (e.g. 300 A / 75 mV) | Battery current | 1 | $15–25 |
| 9 | Power monitor IC | INA228 breakout (up to 85 V) | Reads shunt current and pack voltage | 1 | $15 |
| 10 | Temperature sensors | DS18B20 waterproof probes | Battery, motor, controller temps | 3–4 | $10 |
| 11 | Driver display | Sharp Memory LCD 2.7" (readable in sunlight) | Lap times and battery for the driver | 1 | $45 |
| 12 | Alert LED | High-brightness LED (or small LED strip) | Gets the driver's attention for engineer messages | 1 | $3 |
| 13 | Driver button | Waterproof momentary push button | Acknowledges a message from the engineer | 1 | $5 |
| 14 | microSD module + card | SPI microSD breakout, 16–32 GB card | Data logging | 1 | $12 |
| 15 | DC-DC converter | Rated for **pack voltage** → 5 V, ≥2 A | Powers the electronics | 1 | $15–25 |
| 16 | Fuse + holder | Inline blade fuse, 2 A | Protects the telemetry wiring | 1 | $5 |
| 17 | Reverse-polarity protection | Ideal-diode module or Schottky diode | Survives a backwards connection | 1 | $5 |
| 18 | Connectors | JST-XH (inside box), Deutsch DT or GX16 (outside box) | Vibration-proof connections | — | $25 |
| 19 | Enclosure | IP65 plastic box | Protects the electronics | 1 | $15 |
| 20 | Wire, heat shrink, ferrites, mounts | Twisted/shielded sensor wire | Wiring | — | $20 |
| | | | | **Total (est.)** | **≈ $325–375** |

---

## 4. Sensors

### 4.1 Speed — Hall effect sensor
- **How it works:** magnets are glued around a wheel hub. The Hall sensor is fixed to the frame next to them. Each time a magnet passes, the ESP32 gets an interrupt.
- **Mounting:** use a **front wheel**. Front wheels aren't driven, so wheelspin won't give a false speed. Keep a 2–5 mm gap between sensor and magnet. Agree on the mount with the mechanical team.
- **Math:**
  ```
  wheel_circumference = measured by rolling the wheel 1 full turn (meters)
  distance_per_pulse  = wheel_circumference / number_of_magnets
  speed (m/s)         = distance_per_pulse / time_between_pulses
  speed (km/h)        = speed (m/s) × 3.6
  ```
  Measure the **time between pulses** instead of counting pulses per second. It's more accurate at low speed. If no pulse arrives for 1 s, speed = 0.
- **Rate:** updates on every pulse. Logged at 50 Hz.
- **Check:** compare against GPS speed on a straight. They should agree within a few %.

### 4.2 Position — GPS (u-blox NEO-M9N)
- **Rate:** set it to **10 Hz or faster** using u-blox's binary protocol (UBX-NAV-PVT messages). The default 1 Hz is too slow for lap timing.
- **Gives us:** latitude, longitude, GPS speed, heading, number of satellites, and accuracy.
- **Mounting:** antenna faces the sky, as high as possible, away from the motor and metal.
- **Used for:** lap and sector timing ([Section 5](#5-lap-timing)), track map, and as a backup speed reading.

### 4.3 Acceleration / G-force — IMU (BNO085)
- **Rate:** 100 Hz.
- **Mounting:** as close to the kart's **center of mass** as possible, on rubber/foam to soak up vibration. Write down which way the axes point (X = forward, Y = left, Z = up).
- **Math:**
  ```
  G = acceleration (m/s²) / 9.81
  longitudinal G = braking (−) / accelerating (+)
  lateral G      = cornering left / right
  ```
- **Filtering:** vibration adds a lot of noise. Use a low-pass filter (start with a simple moving average over 5–10 samples) before showing or sending values. Log the **raw** values too.
- **Calibration:** with the kart parked on flat ground, record the "zero" reading and subtract it.
- **Note:** if I2C is unreliable with the ESP32, switch the BNO085 to SPI or UART mode.

### 4.4 Battery — voltage, current, charge left
- **Hardware:** a current shunt on the battery **negative** cable, read by an **INA228**. The INA228 also measures pack voltage directly (up to 85 V), so no voltage divider is needed.
  - If our pack is **above 60 V**, stop and redesign with isolated sensing. That counts as high voltage. **TBD — need the pack voltage.**
- **Rate:** 50 Hz.
- **Charge left (coulomb counting):**
  ```
  Each sample:
    Ah_used += current (A) × Δt (s) / 3600
    Wh_used += voltage (V) × current (A) × Δt (s) / 3600

  charge_left_% = 100 × (1 − Ah_used / pack_capacity_Ah)
  ```
  - Reset `Ah_used = 0` after a full charge.
  - Voltage alone is **not** a reliable way to judge charge left, because it drops every time the throttle is pressed. Only use voltage for a rough cross-check when the kart is parked.
- **Energy per lap:** save `Wh_used` at each lap crossing. The difference between two crossings is the energy that lap used. The pit dashboard needs this number.

### 4.5 Temperatures (DS18B20)
| Sensor | Location | Why |
|--------|----------|-----|
| Battery | Taped to the middle of the pack | Cells lose capacity or get damaged when hot |
| Motor | On the motor case | Overheating weakens the motor |
| Motor controller | On the controller's heatsink | Many controllers cut power when hot |
| (Optional) Ambient | Inside the electronics box | Debugging |

- **Rate:** 1 Hz. Temperatures change slowly, and the DS18B20 takes up to 750 ms per reading.
- **Alert limits:** set from each part's datasheet. **TBD.**

---

## 5. Lap Timing

### 5.1 Start/finish line
- Define the line with **two GPS points**, one on each side of the track. Collect them by walking the track with the GPS, or read them off a satellite map.
- Store them in a config file on the SD card, so a new track doesn't need new code.

### 5.2 Detecting a crossing
For each new GPS fix:
1. Check which side of the start/finish line the kart is on. Use the sign of the cross product between the line and the kart's position.
2. If the side changed since the last fix **and** the kart's path crossed *between* the two line points, it's a crossing.
3. **Minimum lap time:** ignore crossings less than **TBD s** (e.g. 20 s) after the last one. This stops GPS jitter from triggering double laps.

### 5.3 Exact crossing time (interpolation)
At 10 Hz, GPS fixes are 0.1 s apart. Without interpolation, lap times could be off by up to 0.1 s.
```
d1 = distance from line at previous fix (before crossing)
d2 = distance from line at current fix  (after crossing)
fraction = d1 / (d1 + d2)
crossing_time = t_previous + fraction × (t_current − t_previous)
```

### 5.4 Sectors
- Add **2 more lines** around the track to split it into 3 sectors. Same crossing logic.
- Track the best time for each sector. The sum of the best sectors gives a "theoretical best lap."

### 5.5 Gap to best lap
- During each lap, record **distance traveled** (from the speed sensor) together with the time.
- Keep the best lap's distance-vs-time record in memory.
- Live gap = `current lap time at this distance − best lap time at this same distance`.
  - Negative (−0.3) = ahead of your best. Positive (+0.5) = behind.

---

## 6. Driver Display

### 6.1 Hardware
- **Sharp Memory LCD (2.7")**: high contrast in direct sunlight and low power. It's black and white, so we use **+ / −** signs and inverted text instead of colors.
- **Alert LED** next to the screen for engineer messages.
- **Acknowledge button** on the steering wheel or dash.

### 6.2 Layout (mock-up)
```
┌───────────────────────────────┐
│ LAP 7            BATT  64%    │
│                               │
│        1:02.4                 │   ← current lap (biggest text)
│                               │
│   GAP   −0.31                 │   ← gap to best (second biggest)
│                               │
│ LAST 1:03.12   BEST 1:02.81   │
└───────────────────────────────┘
```
- Big numbers only. The driver gets about a half-second glance.
- After a lap crossing, show the **completed lap time** large for 3 s.
- Screen update rate: about 5–10 Hz.

### 6.3 Engineer messages on the display
When a message arrives from the pit:
1. The whole screen switches to the message in huge text, e.g. **`SAVE BATT`**.
2. The alert LED flashes.
3. The driver presses the button to acknowledge. The screen returns to normal, and the next packet tells the pit the message was seen.

Preset messages (fixed codes keep the radio packets small):

| Code | Display text | Meaning |
|------|--------------|---------|
| 1 | `PUSH` | Go faster, we have energy to spare |
| 2 | `SAVE BATT` | Lift and coast. Use less energy per lap |
| 3 | `PIT` | Come into the pits this lap |
| 4 | `TEMP HIGH` | Back off, something is overheating |
| 5 | `GOOD LAP` | Keep doing what you're doing |
| 6 | `SECTOR 2` | Losing time in sector 2 (one code per sector) |

---

## 7. Telemetry Link (LoRa)

### 7.1 Why LoRa
| Option | Range | Data rate | Decision |
|--------|-------|-----------|----------|
| WiFi / ESP-NOW | ~100–200 m | High | ❌ Drops out on the far side of most tracks |
| **LoRa 915 MHz** | **1 km+** | Low (a few hundred bytes/s) | ✅ **Chosen** |
| Cellular 4G | Anywhere | High | ❌ Needs a SIM plan, adds delay, overkill |

915 MHz is a license-free band in the US.

### 7.2 Uplink packet (kart → pit)
Sent **2–5 times per second** (start at 4 Hz). Values are packed as raw bytes, not text, to keep packets small.

| Field | Type | Bytes | Notes |
|-------|------|-------|-------|
| msg_type | uint8 | 1 | `1` = telemetry |
| seq | uint16 | 2 | Packet number. Lets the pit detect missed packets |
| uptime_ms | uint32 | 4 | Time since power-on |
| lap | uint16 | 2 | Current lap number |
| current_lap_ms | uint32 | 4 | |
| last_lap_ms | uint32 | 4 | |
| best_lap_ms | uint32 | 4 | |
| speed_kmh_x10 | uint16 | 2 | 45.3 km/h → 453 |
| lat_e7 | int32 | 4 | Latitude × 10,000,000 |
| lon_e7 | int32 | 4 | Longitude × 10,000,000 |
| pack_v_x100 | uint16 | 2 | 48.52 V → 4852 |
| pack_a_x10 | int16 | 2 | 123.4 A → 1234 (negative = charging/regen) |
| charge_pct | uint8 | 1 | 0–100 |
| batt_temp_c | int8 | 1 | |
| motor_temp_c | int8 | 1 | |
| ctrl_temp_c | int8 | 1 | |
| max_lat_g_x100 | int16 | 2 | Peak cornering G since last packet |
| max_long_g_x100 | int16 | 2 | Peak braking/accel G since last packet |
| flags | uint8 | 1 | Bits: GPS fix OK, SD logging OK, message acknowledged, etc. |
| crc16 | uint16 | 2 | Checksum to reject corrupted packets |
| | | **46 bytes** | |

### 7.3 Downlink packet (pit → kart)
| Field | Type | Bytes |
|-------|------|-------|
| msg_type | uint8 | 1 (`2` = driver message) |
| msg_id | uint8 | 1 (so the kart can acknowledge this exact message) |
| code | uint8 | 1 (see table in 6.3) |
| crc16 | uint16 | 2 |

- LoRa radios can't send and receive at the same time. So the kart **listens for a short window right after each uplink**, and the pit only transmits during that window.
- The pit **resends** the message until the kart's `flags` show it was acknowledged.

### 7.4 Radio settings (starting point, tune in testing)
- Frequency: 915 MHz
- Bandwidth: 125 kHz
- Spreading factor: **SF7** (fastest). Raise to SF8–9 if range is poor, but lower the send rate to match.
- Antenna: mounted **high on the kart**, vertical, away from the motor and metal, and **outside** any metal box.

### 7.5 Range test (do before race day)
Drive or walk the kart around the full track while the pit logs packet loss. Mark any dead spots on the track map.

---

## 8. Pit Dashboard

### 8.1 Setup
```
Pit LoRa board ──USB──► Laptop ──► Python script (pyserial) ──► Dashboard (Streamlit or Dash)
                                              └──► Saves every received packet to CSV
```

### 8.2 What the engineer sees
| Panel | Content |
|-------|---------|
| **Live numbers** | Speed, lap, current lap time, battery %, voltage, current, all temperatures |
| **Lap table** | Every lap time and sector time. Best lap highlighted. Energy (Wh) per lap |
| **Track map** | Kart position live, plus the racing line colored by speed |
| **Energy strategy** | Energy per lap (average of the last 3 laps), **laps remaining**, laps left in the race |
| **Alerts** | Red banners (see 8.3) |
| **Message buttons** | One button per driver message code, showing whether it's been acknowledged |
| **Link health** | Packets per second, % lost, time since last packet |

### 8.3 Energy strategy math
```
Wh_remaining   = pack_capacity_Wh × charge_pct / 100
Wh_per_lap     = average energy used over the last 3 laps
laps_remaining = Wh_remaining / Wh_per_lap

If laps_remaining < laps_left_in_race → alert "WILL NOT FINISH — SAVE BATT"
If laps_remaining > laps_left_in_race + margin → engineer can tell the driver "PUSH"
```

### 8.4 Alerts
| Alert | Trigger |
|-------|---------|
| Battery won't last | laps_remaining < laps_left_in_race |
| Battery hot | batt_temp > **TBD** °C |
| Motor / controller hot | temp > **TBD** °C |
| Low voltage | pack voltage < **TBD** V under load |
| Link lost | No packet for 3 s |
| GPS lost | GPS fix flag off |

### 8.5 What the engineer tells the driver
- **Energy:** "SAVE BATT". Lift off earlier before corners and coast.
- **Sectors:** "Losing time in sector 2." Gives the driver one place to focus.
- **Corners:** low cornering G plus low minimum speed means braking too much or a bad line. (Analyzed after the session from SD data.)
- **Heat:** "TEMP HIGH". Back off before the controller cuts power.

---

## 9. Power and Wiring

### 9.1 Power
- The telemetry system gets power from the main pack through its **own 2 A fuse**, placed as close to the pack as possible.
- **Reverse-polarity protection** before the DC-DC converter.
- The DC-DC converter must be **rated above the pack's maximum voltage** (fully charged voltage, plus margin for regen spikes).
- The telemetry system must **never** be in the kart's kill-switch circuit or the main power path.
- **Safety:** work on wiring only with the pack disconnected. Follow the team's high-voltage rules if the pack is above 60 V.

### 9.2 Grounding and noise
- **One common ground point** (star ground). The shunt sits on the pack negative, so the electronics ground is the pack negative on the electronics side of the shunt. **Confirm the shunt wiring with a mentor before powering up.**
- Motor phase wires are very electrically noisy:
  - Route sensor wires **away** from motor and controller cables. Cross them at 90° if they have to cross.
  - Use **twisted pair** or shielded wire for sensor runs.
  - Put ferrite beads on long sensor cables.

### 9.3 Vibration-proofing
- **No breadboards on the kart.** Prototype on a breadboard, then move to soldered perfboard, then a custom PCB.
- Use **locking connectors**: JST-XH inside the box, Deutsch DT or GX16 aviation connectors where cables leave the box.
- **Strain relief** on every cable (zip-tie mounts, cable glands).
- Mount the box on rubber isolators.
- Optional: conformal coating on the finished boards for moisture and dust.

---

## 10. Build Phases

Every phase must work **on the bench first, then on the kart**.

| Phase | Build | Done when |
|-------|-------|-----------|
| **1. Core** | ESP32 + Hall speed sensor + SD logging + display showing speed. Timing laps by GPS can wait. A button can mark laps for now | Kart drives a session, speed matches GPS/phone within 5%, and a CSV file is on the SD card |
| **2. GPS + IMU** | GPS lap timing (5.1–5.3), IMU G-force, sector times, gap to best | Lap times are within 0.1 s of a hand stopwatch, and a track map can be plotted from the SD data |
| **3. Battery + temps** | INA228 + shunt, coulomb counting, temperature sensors, battery % on the display | Battery % after a session roughly matches the charger's reading, and temps look sensible |
| **4. Telemetry + dashboard** | LoRa uplink, pit dashboard, energy-per-lap, alerts | Range test passes with <10% packet loss around the whole track, and the engineer sees live data |
| **5. Two-way messages** | Downlink, alert LED, acknowledge button | Engineer sends "SAVE BATT", the driver sees it and acknowledges, and the pit shows the acknowledgement |
| **6. Clean-up** | Custom PCB in KiCad, final enclosure, final wiring | Survives a full race weekend without a loose connection |

---

## 11. Open Questions

| # | Question | Who to ask | Answer |
|---|----------|-----------|--------|
| 1 | Pack voltage (nominal and fully charged)? | Powertrain / mentor | **TBD** |
| 2 | Pack chemistry and capacity (Ah / Wh)? | Powertrain / mentor | **TBD** |
| 3 | Maximum motor current (sets the shunt rating)? | Motor controller datasheet | **TBD** |
| 4 | Our budget? | Team lead | **TBD** |
| 5 | Do the competition rules allow radio telemetry and two-way messages to the driver? | Rulebook | **TBD** |
| 6 | Where can the electronics box, display, GPS antenna, and Hall sensor mount? | Mechanical team | **TBD** |
| 7 | Does the motor controller have a data output (CAN or UART) we could read for motor RPM and temperature? | Controller datasheet | **TBD** |
| 8 | Minimum realistic lap time on our track (for the double-lap filter)? | Test day | **TBD** |
| 9 | Temperature limits for battery, motor, and controller? | Datasheets | **TBD** |

---

## 12. Pin Map

Fill in once parts arrive. Check the Heltec V3 pinout, since some pins are already used by the LoRa radio and the onboard screen.

| Device | Bus | ESP32 pin(s) | Notes |
|--------|-----|--------------|-------|
| Hall speed sensor | Interrupt GPIO | **TBD** | Needs pull-up resistor |
| GPS (M9N) | UART | TX **TBD** / RX **TBD** | Set baud rate high enough for 10 Hz+ |
| IMU (BNO085) | I2C or SPI | **TBD** | |
| INA228 | I2C | SDA **TBD** / SCL **TBD** | Can share the I2C bus with the IMU |
| DS18B20 temps | 1-Wire | **TBD** | 4.7 kΩ pull-up, all sensors on one pin |
| Sharp Memory LCD | SPI | **TBD** | |
| microSD | SPI | **TBD** | Separate chip-select pin from the display |
| Alert LED | GPIO | **TBD** | Through a transistor if it's bright/high current |
| Acknowledge button | GPIO | **TBD** | Internal pull-up, debounce in software |
| LoRa radio | SPI (onboard) | Fixed by Heltec | Don't reuse these pins |

---

## 13. Team Roles

| Area | Owner |
|------|-------|
| Speed sensor + lap timing | **TBD** |
| GPS + IMU | **TBD** |
| Battery + temperature monitoring | **TBD** |
| Driver display + messages | **TBD** |
| LoRa link + pit dashboard | **TBD** |
| Power, wiring, enclosure | **TBD** |
| Data analysis (Python) | **TBD** |
