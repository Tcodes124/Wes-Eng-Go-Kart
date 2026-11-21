"""Pit wall server: receives kart telemetry and serves the live dashboard.

    python3 server.py --simulate                      fake kart, no hardware needed
    python3 server.py --simulate --sim-speed 5        fake kart, 5x faster (quick demos)
    python3 server.py --port /dev/cu.usbserial-0001   real pit LoRa board over USB

Then open http://localhost:8000 in a browser. Add --share to let teammates on
the same WiFi open it too (http://<this laptop's IP>:8000).

Uses only Python's standard library, plus pyserial for --port.
"""

import argparse
import csv
import json
import os
import queue
import random
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telemetry as tl
from simulator import KartSimulator

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "static")
TRAIL_MAX = 1200  # GPS points kept for the track map (~5 min at 4 Hz)
CONTENT_TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml"}


class CsvLog:
    """Saves every packet the pit receives, as a backup to the kart's SD card."""

    def __init__(self, folder):
        self.folder = folder
        self.file = None
        self.writer = None

    def write(self, t):
        row = t.to_dict()
        if self.writer is None:
            os.makedirs(self.folder, exist_ok=True)
            name = datetime.now().strftime("session-%Y%m%d-%H%M%S.csv")
            self.path = os.path.join(self.folder, name)
            self.file = open(self.path, "w", newline="")
            self.writer = csv.DictWriter(self.file, fieldnames=["received_at"] + list(row))
            self.writer.writeheader()
            print("Logging packets to", self.path)
        row["received_at"] = datetime.now().isoformat(timespec="milliseconds")
        self.writer.writerow(row)
        self.file.flush()


class PitState:
    """Everything the dashboard shows. Updated by the radio thread, read by web requests."""

    def __init__(self, config, source_name, log_dir):
        self.lock = threading.Lock()
        self.config = config
        self.source_name = source_name
        self.source_status = "starting"
        self.log = CsvLog(log_dir)
        self.transmit = None  # set by the source: function(bytes) that sends to the kart

        self.latest = None
        self.last_rx = None
        self.rx_times = deque(maxlen=200)
        self.recent = deque(maxlen=200)  # 1 = received, 0 = lost (from seq gaps)
        self.received = 0
        self.lost = 0
        self.bad_packets = 0
        self.prev_seq = None
        self.prev_uptime = None
        self.prev_lap = None

        self.session = 1
        self.laps = []
        self.trail = deque(maxlen=TRAIL_MAX)
        self.lap_wh = 0.0
        self.lap_max_speed = 0.0
        self.lap_start_charge = None
        self.lap_has_full_data = False  # False until we've seen the start of a lap

        self.message = None
        self.next_msg_id = 1
        self.clients = []

    # --- incoming packets -------------------------------------------------

    def on_raw_uplink(self, payload):
        try:
            t = tl.decode_telemetry(payload)
        except tl.PacketError:
            with self.lock:
                self.bad_packets += 1
            return
        self.on_packet(t)

    def on_packet(self, t):
        now = time.monotonic()
        with self.lock:
            # Kart was switched off and on (uptime went backwards): new session.
            if self.prev_uptime is not None and t.uptime_ms < self.prev_uptime:
                self._new_session()

            if self.prev_seq is not None:
                gap = (t.seq - self.prev_seq) & 0xFFFF
                if 1 < gap < 1000:
                    self.lost += gap - 1
                    self.recent.extend([0] * (gap - 1))
            self.prev_seq = t.seq
            self.received += 1
            self.recent.append(1)
            self.rx_times.append(now)
            self.last_rx = now

            # Energy used: integrate V x I over the time between packets.
            if self.prev_uptime is not None and t.uptime_ms > self.prev_uptime:
                dt = (t.uptime_ms - self.prev_uptime) / 1000
                if dt < 5:
                    self.lap_wh += t.pack_v * t.pack_a * dt / 3600
            self.prev_uptime = t.uptime_ms

            if self.prev_lap is not None and t.lap > self.prev_lap:
                self._finish_lap(t)
            self.prev_lap = t.lap
            self.lap_max_speed = max(self.lap_max_speed, t.speed_kmh)

            point = None
            if t.gps_fix:
                point = [round(t.lat, 7), round(t.lon, 7), round(t.speed_kmh, 1), t.lap]
                self.trail.append(point)

            self._check_message(t)
            self.latest = t.to_dict()
            self.log.write(t)
            snap = self._snapshot()
        snap["point"] = point
        snap["new_packet"] = True
        self.publish("update", snap)

    def _new_session(self):
        """Clear laps, the track trail and messages. The CSV log keeps everything."""
        self.session += 1
        self.laps = []
        self.trail.clear()
        self.prev_seq = self.prev_lap = None
        self.lap_wh = 0.0
        self.lap_max_speed = 0.0
        self.lap_start_charge = None
        self.lap_has_full_data = False
        self.message = None

    def _finish_lap(self, t):
        charge_used = None
        if self.lap_start_charge is not None:
            charge_used = self.lap_start_charge - t.charge_pct
        self.laps.append({
            "lap": self.prev_lap,
            "time_ms": t.last_lap_ms,
            "wh": round(self.lap_wh, 1),
            "charge_used": charge_used,
            "max_speed_kmh": round(self.lap_max_speed, 1),
            "complete": self.lap_has_full_data,
        })
        self.lap_wh = 0.0
        self.lap_max_speed = 0.0
        self.lap_start_charge = t.charge_pct
        self.lap_has_full_data = True

    # --- driver messages --------------------------------------------------

    def send_message(self, code):
        with self.lock:
            msg_id = self.next_msg_id
            self.next_msg_id = msg_id % 15 + 1  # 1..15 so it fits the 4-bit echo
            self.message = {
                "id": msg_id,
                "code": code,
                "text": tl.DRIVER_MESSAGES[code],
                "status": "sending",
                "attempts": 0,
                "sent_at": time.time(),
                "delivered_at": None,
                "acked_at": None,
            }
        self._transmit_message()
        self.publish("update", self.snapshot())
        return self.message

    def _transmit_message(self):
        with self.lock:
            m = self.message
            if not m or m["status"] != "sending":
                return
            m["attempts"] += 1
            if m["attempts"] > 30:
                m["status"] = "failed"
                return
            packet = tl.encode_driver_message(m["id"], m["code"])
        if self.transmit:
            self.transmit(packet)

    def _check_message(self, t):
        m = self.message
        if not m or m["status"] in ("acknowledged", "failed"):
            return
        if t.msg_echo != (m["id"] & 0x0F):
            return
        if m["status"] == "sending":
            m["status"] = "delivered"
            m["delivered_at"] = time.time()
        if t.msg_ack:
            m["status"] = "acknowledged"
            m["acked_at"] = time.time()

    # --- derived numbers --------------------------------------------------

    def _strategy(self):
        cfg = self.config
        full = [lap for lap in self.laps if lap["complete"]]
        recent = full[-3:]
        out = {"wh_per_lap": None, "basis_laps": len(recent), "laps_possible": None,
               "laps_left": None, "margin": None}
        if self.latest:
            out["laps_left"] = max(0, cfg["race_laps"] - (self.latest["lap"] - 1))
        if recent and self.latest:
            wh_per_lap = sum(lap["wh"] for lap in recent) / len(recent)
            out["wh_per_lap"] = round(wh_per_lap, 1)
            if wh_per_lap > 0:
                wh_left = cfg["pack_capacity_wh"] * self.latest["charge_pct"] / 100
                out["laps_possible"] = round(wh_left / wh_per_lap, 1)
                out["margin"] = round(out["laps_possible"] - out["laps_left"], 1)
        return out

    def _alerts(self, since_rx, loss_pct, strategy):
        cfg = self.config
        alerts = []
        if self.latest is None:
            return alerts
        if since_rx is not None and since_rx > cfg["link_timeout_s"]:
            alerts.append(("danger", "NO SIGNAL for %d s" % since_rx))
        if strategy["margin"] is not None and strategy["margin"] < 0:
            alerts.append(("danger", "WON'T FINISH: short by %.1f laps. Tell the driver SAVE BATT"
                           % -strategy["margin"]))
        t = self.latest
        for key, label, limit in (("batt_temp_c", "Battery", "batt_temp_max_c"),
                                  ("motor_temp_c", "Motor", "motor_temp_max_c"),
                                  ("ctrl_temp_c", "Controller", "ctrl_temp_max_c")):
            if t[key] >= cfg[limit]:
                alerts.append(("danger", "%s HOT: %d °C" % (label, t[key])))
            elif t[key] >= cfg[limit] - cfg["temp_warn_margin_c"]:
                alerts.append(("warn", "%s warm: %d °C" % (label, t[key])))
        if t["pack_v"] < cfg["min_pack_v"]:
            alerts.append(("warn", "Low pack voltage: %.1f V" % t["pack_v"]))
        if not t["gps_fix"]:
            alerts.append(("warn", "Kart has no GPS fix"))
        if not t["sd_ok"]:
            alerts.append(("warn", "Kart SD card is not logging"))
        if loss_pct is not None and loss_pct > cfg["max_packet_loss_pct"]:
            alerts.append(("warn", "Radio losing %d%% of packets" % loss_pct))
        return [{"level": level, "text": text} for level, text in alerts]

    def _snapshot(self, include_trail=False):
        now = time.monotonic()
        since_rx = None if self.last_rx is None else round(now - self.last_rx, 1)
        loss_pct = None
        if len(self.recent) >= 10:
            loss_pct = round(100 * self.recent.count(0) / len(self.recent))
        rate = len([x for x in self.rx_times if now - x <= 5]) / 5
        strategy = self._strategy()
        snap = {
            "session": self.session,
            "source": self.source_name,
            "source_status": self.source_status,
            "since_last_rx_s": since_rx,
            "link": {"rate_hz": round(rate, 1), "loss_pct": loss_pct, "received": self.received,
                     "lost": self.lost, "bad": self.bad_packets},
            "latest": self.latest,
            "laps": self.laps,
            "strategy": strategy,
            "alerts": self._alerts(since_rx, loss_pct, strategy),
            "message": self.message,
            "catalog": {str(k): v for k, v in tl.DRIVER_MESSAGES.items()},
            "config": self.config,
        }
        if include_trail:
            snap["trail"] = list(self.trail)
        return snap

    def snapshot(self, include_trail=False):
        with self.lock:
            return self._snapshot(include_trail)

    def update_config(self, changes):
        with self.lock:
            if "race_laps" in changes:
                self.config["race_laps"] = max(1, min(999, int(changes["race_laps"])))
            if "pack_capacity_wh" in changes:
                self.config["pack_capacity_wh"] = max(1.0, float(changes["pack_capacity_wh"]))
        self.publish("update", self.snapshot())
        return self.config

    def set_source_status(self, status):
        with self.lock:
            self.source_status = status

    # --- pushing updates to browsers --------------------------------------

    def subscribe(self):
        q = queue.Queue(maxsize=50)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def publish(self, event, data):
        text = json.dumps(data)
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, text))
            except queue.Full:
                pass  # that browser is too slow; it'll catch up on the next update

    def heartbeat(self):
        """Resends unconfirmed messages, and keeps the link timer moving when packets stop."""
        tick = 0
        while True:
            time.sleep(0.5)
            tick += 1
            self._transmit_message()
            with self.lock:
                quiet = self.last_rx is None or time.monotonic() - self.last_rx > 1
            if quiet and tick % 2 == 0:
                self.publish("update", self.snapshot())


# --- where packets come from ---------------------------------------------

class SimulatedSource(threading.Thread):
    """Runs the fake kart and fakes the radio, including dropped packets and a dead spot."""

    def __init__(self, state, speed=1.0, drop=0.03):
        super().__init__(daemon=True)
        self.state = state
        self.speed = speed
        self.drop = drop
        self.sim = KartSimulator()
        self.rng = random.Random()
        state.transmit = self.send

    def run(self):
        self.state.set_source_status("simulator running")
        dt = 0.02
        sim_time = 0.0
        next_packet = 0.0
        start = time.monotonic()
        while True:
            self.sim.step(dt)
            sim_time += dt
            if sim_time >= next_packet:
                next_packet += 0.25  # 4 Hz, as in architecture.md 7.2
                packet = tl.encode_telemetry(self.sim.make_telemetry())
                in_dead_spot = 0.55 < self.sim.lap_fraction() < 0.62
                if self.rng.random() > (0.6 if in_dead_spot else self.drop):
                    self.state.on_raw_uplink(packet)
            delay = start + sim_time / self.speed - time.monotonic()
            if delay > 0:
                time.sleep(delay)

    def send(self, packet):
        if self.rng.random() > 0.2:  # downlink loses some messages too
            self.sim.receive(packet)


class SerialSource(threading.Thread):
    """Reads the pit LoRa board over USB. Reconnects if the cable is unplugged."""

    def __init__(self, state, port, baud):
        super().__init__(daemon=True)
        import serial  # only needed for real hardware

        self.serial = serial
        self.state = state
        self.port = port
        self.baud = baud
        self.conn = None
        self.write_lock = threading.Lock()
        state.transmit = self.send

    def run(self):
        while True:
            try:
                with self.serial.Serial(self.port, self.baud, timeout=1) as conn:
                    self.conn = conn
                    self.state.set_source_status("connected to %s" % self.port)
                    while True:
                        prefix, payload = tl.parse_line(conn.readline())
                        if prefix == "T":
                            self.state.on_raw_uplink(payload)
            except (self.serial.SerialException, OSError) as e:
                self.conn = None
                self.state.set_source_status("serial error: %s" % e)
                time.sleep(2)

    def send(self, packet):
        conn = self.conn
        if conn is None:
            return
        try:
            with self.write_lock:
                conn.write(tl.frame_line("M", packet))
        except (self.serial.SerialException, OSError):
            pass


# --- web server -------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "PitWall/0.1"
    state = None  # set in main()

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._file("index.html")
        if path.startswith("/static/"):
            return self._file(path[len("/static/"):])
        if path == "/api/state":
            return self._json(self.state.snapshot(include_trail=True))
        if path == "/events":
            return self._events()
        self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "bad JSON"}, 400)
        if self.path == "/api/message":
            try:
                code = int(body.get("code"))
            except (TypeError, ValueError):
                code = None
            if code not in tl.DRIVER_MESSAGES:
                return self._json({"error": "unknown message code"}, 400)
            return self._json(self.state.send_message(code))
        if self.path == "/api/config":
            try:
                return self._json(self.state.update_config(body))
            except (TypeError, ValueError):
                return self._json({"error": "bad config value"}, 400)
        self.send_error(404)

    def _file(self, name):
        full = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not full.startswith(STATIC_DIR + os.sep) or not os.path.isfile(full):
            return self.send_error(404)
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _events(self):
        """Server-Sent Events: one long response the browser reads updates from."""
        q = self.state.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._send_event("init", json.dumps(self.state.snapshot(include_trail=True)))
            while True:
                try:
                    event, text = q.get(timeout=15)
                    self._send_event(event, text)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.state.unsubscribe(q)

    def _send_event(self, event, text):
        self.wfile.write(("event: %s\ndata: %s\n\n" % (event, text)).encode())
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description="Go-kart pit wall dashboard")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--simulate", action="store_true", help="use a fake kart")
    source.add_argument("--port", help="serial port of the pit LoRa board, e.g. /dev/cu.usbserial-0001")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--sim-speed", type=float, default=1.0, help="run the fake kart faster than real time")
    parser.add_argument("--http-port", type=int, default=8000)
    parser.add_argument("--share", action="store_true", help="allow other devices on the network to open the dashboard")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    args = parser.parse_args()

    with open(args.config) as f:
        config = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    state = PitState(config, "simulator" if args.simulate else args.port, os.path.join(HERE, "logs"))
    if args.simulate:
        SimulatedSource(state, speed=args.sim_speed).start()
    else:
        SerialSource(state, args.port, args.baud).start()
    threading.Thread(target=state.heartbeat, daemon=True).start()

    Handler.state = state
    host = "0.0.0.0" if args.share else "127.0.0.1"
    httpd = ThreadingHTTPServer((host, args.http_port), Handler)
    httpd.daemon_threads = True
    print("Pit wall running: open http://localhost:%d  (Ctrl-C to stop)" % args.http_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
