"""Run from the pit folder:  python3 -m unittest -v"""

import unittest

import telemetry as tl
from simulator import KartSimulator


def sample():
    return tl.Telemetry(
        seq=65535, uptime_ms=123456, lap=7, current_lap_ms=62400, last_lap_ms=63120,
        best_lap_ms=62810, speed_kmh=45.3, lat=37.1234567, lon=-122.7654321,
        pack_v=48.52, pack_a=-12.3, charge_pct=64, batt_temp_c=41, motor_temp_c=-5,
        ctrl_temp_c=55, max_lat_g=-1.23, max_long_g=0.45, gps_fix=True, sd_ok=False,
        msg_ack=True, msg_echo=11,
    )


class PacketTests(unittest.TestCase):
    def test_sizes_match_architecture_doc(self):
        self.assertEqual(tl.UPLINK_SIZE, 46)
        self.assertEqual(tl.DOWNLINK_SIZE, 5)

    def test_crc_check_value(self):
        # Standard check value for CRC-16/CCITT-FALSE. Use this to test the firmware's CRC too.
        self.assertEqual(tl.crc16_ccitt(b"123456789"), 0x29B1)

    def test_telemetry_round_trip(self):
        t = sample()
        packet = tl.encode_telemetry(t)
        self.assertEqual(len(packet), 46)
        self.assertEqual(tl.decode_telemetry(packet), t)

    def test_corrupted_packet_rejected(self):
        packet = bytearray(tl.encode_telemetry(sample()))
        packet[10] ^= 0x01
        with self.assertRaises(tl.PacketError):
            tl.decode_telemetry(bytes(packet))

    def test_wrong_length_rejected(self):
        with self.assertRaises(tl.PacketError):
            tl.decode_telemetry(b"\x01" * 45)

    def test_out_of_range_values_are_clamped(self):
        t = sample()
        t.charge_pct = 140
        t.batt_temp_c = 300
        out = tl.decode_telemetry(tl.encode_telemetry(t))
        self.assertEqual(out.charge_pct, 100)
        self.assertEqual(out.batt_temp_c, 127)

    def test_driver_message_round_trip(self):
        packet = tl.encode_driver_message(9, 2)
        self.assertEqual(len(packet), 5)
        self.assertEqual(tl.decode_driver_message(packet), (9, 2))

    def test_serial_lines(self):
        packet = tl.encode_driver_message(3, 1)
        self.assertEqual(tl.parse_line(tl.frame_line("M", packet)), ("M", packet))
        self.assertEqual(tl.parse_line(b"LoRa init ok\r\n"), (None, None))
        self.assertEqual(tl.parse_line(b"T:zz\n"), (None, None))


class SimulatorTests(unittest.TestCase):
    def test_laps_and_packets(self):
        sim = KartSimulator(seed=1)
        for _ in range(int(150 / 0.02)):  # 150 simulated seconds
            sim.step(0.02)
        t = tl.decode_telemetry(tl.encode_telemetry(sim.make_telemetry()))
        self.assertGreaterEqual(t.lap, 2)
        self.assertGreater(t.last_lap_ms, 30000)
        self.assertLess(t.charge_pct, 100)

    def test_flat_battery_starts_new_session(self):
        sim = KartSimulator(seed=4)
        for _ in range(int(60 / 0.02)):
            sim.step(0.02)
        sim.soc = 0.0  # pretend the battery just ran out
        for _ in range(int(30 / 0.02)):  # coast to a stop, wait 10 s, restart
            sim.step(0.02)
        t = sim.make_telemetry()
        self.assertGreaterEqual(t.charge_pct, 98)  # full again, minus a few seconds of driving
        self.assertEqual(t.lap, 1)
        self.assertLess(t.uptime_ms, 30000)

    def test_driver_acknowledges_message(self):
        sim = KartSimulator(seed=2)
        sim.receive(tl.encode_driver_message(5, 2))
        self.assertEqual(sim.make_telemetry().msg_echo, 5)
        for _ in range(int(5 / 0.02)):
            sim.step(0.02)
        self.assertTrue(sim.make_telemetry().msg_ack)


if __name__ == "__main__":
    unittest.main()
