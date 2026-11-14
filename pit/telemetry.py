"""Packet formats for the kart <-> pit radio link.

This file is the pit-side source of truth for the byte layouts in
architecture.md section 7. If the kart firmware changes a field, change it
here too, and update the tests in test_telemetry.py.

All multi-byte values are little-endian (what the ESP32 uses natively).
"""

import struct
from dataclasses import asdict, dataclass

MSG_TELEMETRY = 1
MSG_DRIVER = 2

# Uplink (kart -> pit), 46 bytes. architecture.md 7.2.
#   B msg_type        H seq             I uptime_ms        H lap
#   I current_lap_ms  I last_lap_ms     I best_lap_ms      H speed_kmh_x10
#   i lat_e7          i lon_e7          H pack_v_x100      h pack_a_x10
#   B charge_pct      b batt_temp_c     b motor_temp_c     b ctrl_temp_c
#   h max_lat_g_x100  h max_long_g_x100 B flags
#   then H crc16 over everything before it
_UPLINK_BODY = struct.Struct("<BHIHIIIHiiHhBbbbhhB")
UPLINK_SIZE = _UPLINK_BODY.size + 2

# Downlink (pit -> kart), 5 bytes. architecture.md 7.3.
#   B msg_type  B msg_id  B code  then H crc16
_DOWNLINK_BODY = struct.Struct("<BBB")
DOWNLINK_SIZE = _DOWNLINK_BODY.size + 2

# Bits in the uplink `flags` byte.
FLAG_GPS_FIX = 0x01
FLAG_SD_OK = 0x02
FLAG_MSG_ACK = 0x04  # driver pressed the button for the message echoed below
# The top 4 bits echo the low 4 bits of the last msg_id the kart received.
# That tells the pit the message arrived, even before the driver presses
# the button. msg_id therefore cycles 1..15 (0 = no message yet).
MSG_ECHO_SHIFT = 4

# Preset driver messages. architecture.md 6.3 (sector codes are one per sector).
DRIVER_MESSAGES = {
    1: "PUSH",
    2: "SAVE BATT",
    3: "PIT",
    4: "TEMP HIGH",
    5: "GOOD LAP",
    6: "SECTOR 1",
    7: "SECTOR 2",
    8: "SECTOR 3",
}

_U16 = 0xFFFF
_U32 = 0xFFFFFFFF
_I16 = (-0x8000, 0x7FFF)
_I32 = (-0x80000000, 0x7FFFFFFF)


class PacketError(ValueError):
    """A packet was the wrong size, the wrong type, or failed its checksum."""


@dataclass
class Telemetry:
    """One uplink packet, in real units (km/h, volts, amps, degrees C, G)."""

    seq: int
    uptime_ms: int
    lap: int
    current_lap_ms: int
    last_lap_ms: int  # 0 = no lap completed yet
    best_lap_ms: int  # 0 = no lap completed yet
    speed_kmh: float
    lat: float
    lon: float
    pack_v: float
    pack_a: float  # negative = regen / charging
    charge_pct: int
    batt_temp_c: int
    motor_temp_c: int
    ctrl_temp_c: int
    max_lat_g: float  # peak cornering G since the last packet
    max_long_g: float  # peak braking (-) / accelerating (+) G since the last packet
    gps_fix: bool
    sd_ok: bool
    msg_ack: bool
    msg_echo: int  # low 4 bits of the last msg_id the kart received

    def to_dict(self):
        return asdict(self)


def crc16_ccitt(data):
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF). The kart firmware must match."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _fit(value, lo, hi):
    return max(lo, min(hi, int(round(value))))


def _with_crc(body):
    return body + struct.pack("<H", crc16_ccitt(body))


def _check(data, size, kind):
    if len(data) != size:
        raise PacketError("%s packet is %d bytes, expected %d" % (kind, len(data), size))
    body, (crc,) = data[:-2], struct.unpack("<H", data[-2:])
    if crc16_ccitt(body) != crc:
        raise PacketError("%s packet failed its checksum" % kind)
    return body


def encode_telemetry(t):
    flags = (
        (FLAG_GPS_FIX if t.gps_fix else 0)
        | (FLAG_SD_OK if t.sd_ok else 0)
        | (FLAG_MSG_ACK if t.msg_ack else 0)
        | ((t.msg_echo & 0x0F) << MSG_ECHO_SHIFT)
    )
    body = _UPLINK_BODY.pack(
        MSG_TELEMETRY,
        t.seq & _U16,
        t.uptime_ms & _U32,
        _fit(t.lap, 0, _U16),
        _fit(t.current_lap_ms, 0, _U32),
        _fit(t.last_lap_ms, 0, _U32),
        _fit(t.best_lap_ms, 0, _U32),
        _fit(t.speed_kmh * 10, 0, _U16),
        _fit(t.lat * 1e7, *_I32),
        _fit(t.lon * 1e7, *_I32),
        _fit(t.pack_v * 100, 0, _U16),
        _fit(t.pack_a * 10, *_I16),
        _fit(t.charge_pct, 0, 100),
        _fit(t.batt_temp_c, -128, 127),
        _fit(t.motor_temp_c, -128, 127),
        _fit(t.ctrl_temp_c, -128, 127),
        _fit(t.max_lat_g * 100, *_I16),
        _fit(t.max_long_g * 100, *_I16),
        flags,
    )
    return _with_crc(body)


def decode_telemetry(data):
    f = _UPLINK_BODY.unpack(_check(data, UPLINK_SIZE, "Telemetry"))
    if f[0] != MSG_TELEMETRY:
        raise PacketError("not a telemetry packet (type %d)" % f[0])
    flags = f[18]
    return Telemetry(
        seq=f[1],
        uptime_ms=f[2],
        lap=f[3],
        current_lap_ms=f[4],
        last_lap_ms=f[5],
        best_lap_ms=f[6],
        speed_kmh=f[7] / 10,
        lat=f[8] / 1e7,
        lon=f[9] / 1e7,
        pack_v=f[10] / 100,
        pack_a=f[11] / 10,
        charge_pct=f[12],
        batt_temp_c=f[13],
        motor_temp_c=f[14],
        ctrl_temp_c=f[15],
        max_lat_g=f[16] / 100,
        max_long_g=f[17] / 100,
        gps_fix=bool(flags & FLAG_GPS_FIX),
        sd_ok=bool(flags & FLAG_SD_OK),
        msg_ack=bool(flags & FLAG_MSG_ACK),
        msg_echo=flags >> MSG_ECHO_SHIFT,
    )


def encode_driver_message(msg_id, code):
    return _with_crc(_DOWNLINK_BODY.pack(MSG_DRIVER, msg_id & 0xFF, code & 0xFF))


def decode_driver_message(data):
    msg_type, msg_id, code = _DOWNLINK_BODY.unpack(_check(data, DOWNLINK_SIZE, "Driver message"))
    if msg_type != MSG_DRIVER:
        raise PacketError("not a driver message (type %d)" % msg_type)
    return msg_id, code


# --- USB serial framing between the laptop and the pit LoRa board ---------
# The pit board forwards each radio packet to the laptop as one text line:
#     T:<packet as hex>\n      kart -> pit telemetry
# and the laptop sends driver messages to the pit board the same way:
#     M:<packet as hex>\n      pit -> kart message
# Any other line (debug prints from the pit board) is ignored.

def frame_line(prefix, payload):
    return ("%s:%s\n" % (prefix, payload.hex())).encode("ascii")


def parse_line(line):
    """Return (prefix, payload bytes), or (None, None) for lines that aren't packets."""
    text = line.decode("ascii", "ignore").strip()
    if len(text) < 3 or text[1] != ":":
        return None, None
    try:
        return text[0], bytes.fromhex(text[2:])
    except ValueError:
        return None, None
