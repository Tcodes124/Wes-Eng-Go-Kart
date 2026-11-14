"""A fake kart for testing the pit dashboard without any hardware.

It drives laps around a made-up track with simple physics (speed limited by
corner grip, battery drain from rolling resistance, air drag and
acceleration, heating from current) and produces real 46-byte packets with
the same encode code the radio path uses, so the dashboard can't tell the
difference. It also "has a driver" who acknowledges messages after a couple
of seconds and changes pace when told to PUSH or SAVE BATT.

The numbers are rough guesses, not our real kart. Replace them once we have
real data.
"""

import bisect
import math
import random
import threading

import telemetry as tl

G = 9.81
ORIGIN_LAT, ORIGIN_LON = 37.0, -122.0  # arbitrary spot; the map only uses relative positions
M_PER_DEG_LAT = 111320.0


class Track:
    """A closed loop of points with distance, curvature and a target speed at each."""

    def __init__(self, n=1200, v_max=17.0, grip_g=1.3, accel=3.2, brake=7.0):
        xs, ys = [], []
        for i in range(n):
            th = 2 * math.pi * i / n
            r = 95 + 28 * math.cos(2 * th) + 25 * math.sin(3 * th) + 14 * math.cos(5 * th)
            xs.append(r * math.cos(th))
            ys.append(0.7 * r * math.sin(th))
        self.x, self.y, self.n = xs, ys, n

        self.seg = [math.hypot(xs[(i + 1) % n] - xs[i], ys[(i + 1) % n] - ys[i]) for i in range(n)]
        self.s = [0.0]
        for d in self.seg[:-1]:
            self.s.append(self.s[-1] + d)
        self.length = self.s[-1] + self.seg[-1]

        # Signed curvature from three neighbouring points (+ = turning left).
        self.k = []
        for i in range(n):
            ax, ay = xs[i - 1], ys[i - 1]
            bx, by = xs[i], ys[i]
            cx, cy = xs[(i + 1) % n], ys[(i + 1) % n]
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            sides = math.hypot(bx - ax, by - ay) * math.hypot(cx - bx, cy - by) * math.hypot(cx - ax, cy - ay)
            self.k.append(2 * cross / sides if sides else 0.0)

        # Fastest speed at each point: corner grip limit, then limited by how
        # hard the kart can accelerate out of / brake into each corner.
        v = [min(v_max, math.sqrt(grip_g * G / abs(k))) if abs(k) > 1e-6 else v_max for k in self.k]
        for _ in range(2):
            for i in range(n):
                j = i - 1
                v[i] = min(v[i], math.sqrt(v[j] ** 2 + 2 * accel * self.seg[j]))
            for i in reversed(range(n)):
                j = (i + 1) % n
                v[i] = min(v[i], math.sqrt(v[j] ** 2 + 2 * brake * self.seg[i]))
        self.v = v

    def index_at(self, dist):
        return bisect.bisect_right(self.s, dist % self.length) - 1

    def position(self, dist):
        d = dist % self.length
        i = self.index_at(d)
        j = (i + 1) % self.n
        f = (d - self.s[i]) / self.seg[i] if self.seg[i] else 0.0
        return self.x[i] + f * (self.x[j] - self.x[i]), self.y[i] + f * (self.y[j] - self.y[i])


class KartSimulator:
    MASS = 160.0  # kg, kart + driver
    CRR = 0.015  # rolling resistance
    CDA = 0.6  # drag area, m^2
    RHO = 1.2  # air density
    EFF = 0.85  # battery -> wheels
    REGEN = 0.35  # fraction of braking energy recovered
    PACK_AH = 18.0
    R_INT = 0.06  # pack internal resistance, ohms
    AMBIENT = 24.0
    ACCEL, BRAKE = 3.2, 7.0  # m/s^2
    P_MAX = 7000.0  # motor controller power limit, watts

    # How each acknowledged message changes the driver's pace.
    PACE_EFFECT = {1: 1.04, 2: 0.90, 3: 0.90, 4: 0.93, 5: None}

    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.track = Track(accel=self.ACCEL, brake=self.BRAKE)
        self.lock = threading.Lock()

        self.t = 0.0
        self.dist = 0.0
        self.speed = 0.0
        self.lap = 1
        self.lap_start = 0.0
        self.last_lap_ms = 0
        self.best_lap_ms = 0

        self.soc = 1.0
        self.current = 0.0
        self.pack_v = self._ocv()
        self.temp_batt = self.temp_motor = self.temp_ctrl = self.AMBIENT

        self.pace = self.rng.uniform(0.96, 1.0)
        self.pace_effect = 1.0
        self.max_lat = 0.0
        self.max_long = 0.0
        self.seq = 0

        self.msg_id = 0
        self.msg_code = 0
        self.msg_ack = False
        self.ack_due = None

    def _ocv(self):
        return 44.0 + 10.4 * self.soc  # 54.4 V full, 44 V empty

    def step(self, dt):
        with self.lock:
            tr = self.track
            i = tr.index_at(self.dist)
            target = tr.v[i] * self.pace * self.pace_effect
            if self.soc <= 0:
                target = 0.0  # flat battery: coast to a stop
            v = self.speed
            resist = self.CRR * self.MASS * G * v + 0.5 * self.RHO * self.CDA * v ** 3
            if target > v:
                a = min(self.ACCEL, (target - v) / dt)
                # The motor controller caps power, so acceleration fades at speed.
                if v > 0.5 and (self.MASS * a * v + resist) / self.EFF > self.P_MAX:
                    a = (self.P_MAX * self.EFF - resist) / (self.MASS * v)
            else:
                a = max(-self.BRAKE, (target - v) / dt)
            self.speed = max(0.0, v + a * dt)
            self.dist += self.speed * dt
            self.t += dt
            v = self.speed

            lat_g = v * v * tr.k[i] / G
            long_g = a / G
            if abs(lat_g) > abs(self.max_lat):
                self.max_lat = lat_g
            if abs(long_g) > abs(self.max_long):
                self.max_long = long_g

            resist = self.CRR * self.MASS * G * v + 0.5 * self.RHO * self.CDA * v ** 3
            if a >= 0:
                power = min(self.P_MAX, (self.MASS * a * v + resist) / self.EFF)
            else:
                power = max(-1500.0, (self.MASS * a * v + resist) * self.REGEN)
            # Battery current for this power: P = (OCV - I*R) * I, solved for I.
            ocv = self._ocv()
            self.current = (ocv - math.sqrt(max(0.0, ocv * ocv - 4 * self.R_INT * power))) / (2 * self.R_INT)
            self.soc = min(1.0, max(0.0, self.soc - self.current * dt / 3600 / self.PACK_AH))
            self.pack_v = ocv - self.current * self.R_INT

            amb = self.AMBIENT
            self.temp_batt += (self.current ** 2 * self.R_INT - 4.0 * (self.temp_batt - amb)) / 4500 * dt
            self.temp_motor += (abs(power) * 0.12 - 8.0 * (self.temp_motor - amb)) / 1350 * dt
            self.temp_ctrl += (abs(power) * 0.04 - 3.0 * (self.temp_ctrl - amb)) / 500 * dt

            if self.dist >= tr.length:
                self.dist -= tr.length
                lap_ms = int(round((self.t - self.lap_start) * 1000))
                self.last_lap_ms = lap_ms
                self.best_lap_ms = lap_ms if not self.best_lap_ms else min(self.best_lap_ms, lap_ms)
                self.lap += 1
                self.lap_start = self.t
                self.pace = self.rng.uniform(0.96, 1.0)

            if self.ack_due is not None and self.t >= self.ack_due:
                self.ack_due = None
                self.msg_ack = True
                effect = self.PACE_EFFECT.get(self.msg_code, None)
                if effect is not None:
                    self.pace_effect = effect

    def lap_fraction(self):
        with self.lock:
            return self.dist / self.track.length

    def make_telemetry(self):
        """Build the next uplink packet and reset the peak-G trackers."""
        with self.lock:
            self.seq = (self.seq + 1) & 0xFFFF
            x, y = self.track.position(self.dist)
            x += self.rng.gauss(0, 0.8)
            y += self.rng.gauss(0, 0.8)
            lat = ORIGIN_LAT + y / M_PER_DEG_LAT
            lon = ORIGIN_LON + x / (M_PER_DEG_LAT * math.cos(math.radians(ORIGIN_LAT)))
            t = tl.Telemetry(
                seq=self.seq,
                uptime_ms=int(self.t * 1000),
                lap=self.lap,
                current_lap_ms=int((self.t - self.lap_start) * 1000),
                last_lap_ms=self.last_lap_ms,
                best_lap_ms=self.best_lap_ms,
                speed_kmh=self.speed * 3.6,
                lat=lat,
                lon=lon,
                pack_v=self.pack_v,
                pack_a=self.current,
                charge_pct=int(round(self.soc * 100)),
                batt_temp_c=int(round(self.temp_batt)),
                motor_temp_c=int(round(self.temp_motor)),
                ctrl_temp_c=int(round(self.temp_ctrl)),
                max_lat_g=self.max_lat + self.rng.gauss(0, 0.03),
                max_long_g=self.max_long + self.rng.gauss(0, 0.03),
                gps_fix=True,
                sd_ok=True,
                msg_ack=self.msg_ack,
                msg_echo=self.msg_id & 0x0F,
            )
            self.max_lat = 0.0
            self.max_long = 0.0
            return t

    def receive(self, packet):
        """A driver message arrived over the (fake) radio."""
        try:
            msg_id, code = tl.decode_driver_message(packet)
        except tl.PacketError:
            return
        with self.lock:
            if msg_id == self.msg_id:
                return  # a resend of a message we already have
            self.msg_id = msg_id
            self.msg_code = code
            self.msg_ack = False
            self.ack_due = self.t + self.rng.uniform(1.5, 4.0)  # driver reaction time
