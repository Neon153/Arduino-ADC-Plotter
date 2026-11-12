#!/usr/bin/env python3
"""
AD7606 Viewer – PC-Programm zur Firmware ad7606_daq.ino

Läuft unter Linux und Windows (Python >= 3.9).
    pip install -r requirements.txt
    python ad7606_viewer.py          normaler Betrieb
    python ad7606_viewer.py --demo   mit simuliertem Gerät, ganz ohne Hardware

Aufbau:
    SerialReader (Thread)  liest Bytes, zerlegt sie in Frames, legt sie in eine Queue
    MainWindow (GUI)       holt alle 30 ms die Frames ab, wertet aus und zeichnet
Dadurch blockiert die serielle Schnittstelle nie die Oberfläche und umgekehrt.
"""
from __future__ import annotations

import queue
import struct
import sys
import threading
import time

import numpy as np
import pyqtgraph as pg
import serial
import serial.tools.list_ports
from PySide6 import QtCore, QtGui, QtWidgets

# =============================================================================
# Protokoll – muss zur Firmware passen
# =============================================================================
BAUD = 1_000_000
SYNC = b"\xA5\x5A"
F_INFO, F_BURST, F_STREAM, F_TEXT = 1, 2, 3, 4
INFO_FMT = "<BHBIHBB"          # proto, bufWords, mask, periodUs, nsets, os, range
INFO_LEN = struct.calcsize(INFO_FMT)
BURST_FMT = "<BIHBBH"          # mask, periodUs, nsets, os, range, overruns
BURST_HDR = struct.calcsize(BURST_FMT)
MAX_FRAME = 4096
PROTO_VERSION = 1

OS_LABELS = ["aus (1×)", "2×", "4×", "8×", "16×", "32×", "64×"]
T_CONV_US = [4.0, 9.1, 18.8, 39.0, 78.0, 158.0, 315.0]   # Wandlungszeit laut Datenblatt
CH_COLORS = ["#ff5555", "#55dd55", "#5599ff", "#ffaa33",
             "#cc77ff", "#33dddd", "#ff77bb", "#dddd55"]
DEMO_PORT = "__demo__"


def channels(mask: int) -> list[int]:
    """Kanalindizes (0 = V1) aus einer Bitmaske."""
    return [i for i in range(8) if mask >> i & 1]


def full_scale(range_bit: int) -> float:
    return 10.0 if range_bit else 5.0


def quantize_period(us: int) -> int:
    """Dieselbe Rundung wie die Firmware (Timer1-Prescaler 8/64/256/1024)."""
    us = max(10, min(int(us), 4_000_000))
    if us <= 32768:
        return us
    if us <= 262144:
        return us // 4 * 4
    if us <= 1048576:
        return us // 16 * 16
    return us // 64 * 64


def fmt_rate(hz: float) -> str:
    return f"{hz / 1000:.3g} kSa/s" if hz >= 1000 else f"{hz:.3g} Sa/s"


def fmt_time(s: float) -> str:
    if s >= 1:
        return f"{s:.3g} s"
    if s >= 1e-3:
        return f"{s * 1e3:.3g} ms"
    return f"{s * 1e6:.3g} µs"


# =============================================================================
# Empfang
# =============================================================================
class FrameParser:
    """Zerlegt den Bytestrom in Frames. Synchronisiert sich nach Fehlern selbst neu."""

    def __init__(self):
        self.buf = bytearray()
        self.bad = 0                       # verworfene Frames (Prüfsumme/Länge)

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        b = self.buf
        b += data
        frames = []
        while True:
            i = b.find(SYNC)
            if i < 0:
                del b[:max(0, len(b) - 1)]   # letztes Byte könnte 0xA5 sein
                break
            if i:
                del b[:i]
            if len(b) < 5:
                break
            ftype, ln = b[2], b[3] | b[4] << 8
            if ftype not in (F_INFO, F_BURST, F_STREAM, F_TEXT) or ln > MAX_FRAME:
                del b[:2]
                self.bad += 1
                continue
            total = 6 + ln
            if len(b) < total:
                break
            if sum(b[2:5 + ln]) & 0xFF != b[5 + ln]:
                del b[:2]
                self.bad += 1
                continue
            frames.append((ftype, bytes(b[5:5 + ln])))
            del b[:total]
        return frames


class SerialReader(threading.Thread):
    def __init__(self, port, out: queue.Queue):
        super().__init__(daemon=True)
        self.port, self.out = port, out
        self.parser = FrameParser()
        self.running = True

    def run(self):
        while self.running:
            try:
                data = self.port.read(max(1, self.port.in_waiting))
            except Exception as e:                     # Kabel gezogen o. Ä.
                if self.running:
                    self.out.put(("error", str(e)))
                return
            if data:
                frames = self.parser.feed(data)
                if frames:
                    self.out.put(("frames", frames))


# =============================================================================
# Simuliertes Gerät (gleiches Protokoll wie die Firmware)
# =============================================================================
class DemoDevice:
    """Verhält sich wie pyserial.Serial mit angeschlossenem Uno – zum Testen ohne Hardware."""
    BUF_WORDS = 640

    def __init__(self):
        self.lock = threading.RLock()
        self.out = bytearray()
        self.line = bytearray()
        self.mask, self.period, self.nsets, self.os, self.range = 0xFF, 100, 80, 0, 0
        self.streaming = False
        self.stream_t0 = 0.0
        self.stream_sent = 0
        self.burst_ready: float | None = None
        self.sample_idx = 0
        self.rng = np.random.default_rng()
        self.is_open = True
        self._frame(F_TEXT, b"AD7606 DAQ bereit (Demo)")
        self._info()

    # ---- pyserial-kompatible Schnittstelle ----
    @property
    def in_waiting(self) -> int:
        with self.lock:
            self._tick()
            return len(self.out)

    def read(self, size: int = 1) -> bytes:
        end = time.monotonic() + 0.05
        while self.is_open:
            with self.lock:
                self._tick()
                if self.out:
                    d = bytes(self.out[:size])
                    del self.out[:size]
                    return d
            if time.monotonic() >= end:
                break
            time.sleep(0.002)
        return b""

    def write(self, data: bytes) -> int:
        with self.lock:
            for c in data:
                if self.streaming:              # jedes Byte beendet den Stream
                    self.streaming = False
                    self._info()
                    continue
                if c in (10, 13):
                    if self.line:
                        self._command(self.line.decode("ascii", "ignore"))
                        self.line.clear()
                else:
                    self.line.append(c)
        return len(data)

    def close(self):
        self.is_open = False

    # ---- interne Logik ----
    def _command(self, s: str):
        cmd = s[:1].upper()
        try:
            arg = int(s[1:].strip() or "0", 0)
        except ValueError:
            arg = 0
        max_sets = self.BUF_WORDS // len(channels(self.mask))
        if cmd == "C":
            self.mask = (arg & 0xFF) or 1
            self.nsets = min(self.nsets, self.BUF_WORDS // len(channels(self.mask)))
        elif cmd == "R":
            self.period = quantize_period(arg)
        elif cmd == "N":
            self.nsets = max(1, min(arg, max_sets))
        elif cmd == "O":
            self.os = min(arg, 6)
        elif cmd == "G":
            self.range = 1 if arg else 0
        elif cmd == "B":
            self.burst_ready = time.monotonic() + self.nsets * self.period * 1e-6 + 0.005
            return
        elif cmd == "S":
            self.streaming, self.stream_t0, self.stream_sent = True, time.monotonic(), 0
            return
        elif cmd == "X":
            return
        elif cmd != "I":
            self._frame(F_TEXT, b"Unbekannter Befehl")
            return
        self._info()

    def _signals(self, idx: np.ndarray) -> np.ndarray:
        t = idx * (self.period * 1e-6)
        v = np.empty((len(idx), 8))
        v[:, 0] = 2.0 * np.sin(2 * np.pi * 50 * t)                    # Sinus 50 Hz
        v[:, 1] = 1.0 * np.sin(2 * np.pi * 1000 * t)                  # Sinus 1 kHz
        v[:, 2] = 1.5 * np.sign(np.sin(2 * np.pi * 200 * t))          # Rechteck 200 Hz
        v[:, 3] = 3.0 * (2 * np.abs(2 * ((20 * t) % 1) - 1) - 1)      # Dreieck 20 Hz
        v[:, 4] = 1.234                                               # Gleichspannung
        v[:, 5] = 4.0 * ((5 * t) % 1)                                 # Sägezahn 5 Hz
        v[:, 6] = 0.5 * np.sin(2 * np.pi * 50 * t) + 0.3 * np.sin(2 * np.pi * 3000 * t)
        v[:, 7] = 0.0                                                 # nur Rauschen
        v += self.rng.normal(0, 0.003, v.shape)
        codes = np.clip(np.round(v / full_scale(self.range) * 32768), -32768, 32767)
        return np.ascontiguousarray(codes.astype("<i2")[:, channels(self.mask)])

    def _tick(self):
        now = time.monotonic()
        if self.burst_ready is not None and now >= self.burst_ready:
            self.burst_ready = None
            n = self.nsets
            codes = self._signals(self.sample_idx + np.arange(n))
            self.sample_idx += n
            hdr = struct.pack(BURST_FMT, self.mask, self.period, n, self.os, self.range, 0)
            self._frame(F_BURST, hdr + codes.tobytes())
        if self.streaming:
            nch = len(channels(self.mask))
            rec = 1 + 2 * nch
            link_rate = BAUD / 10 / (6 + rec)            # Frames/s, die durch die Leitung passen
            want = 1e6 / self.period
            due = min(int((now - self.stream_t0) * min(want, link_rate)) - self.stream_sent, 20000)
            if due <= 0:
                return
            codes = self._signals(self.sample_idx + np.arange(due))
            self.sample_idx += due
            self.stream_sent += due
            fr = np.empty((due, 6 + rec), np.uint8)
            fr[:, 0], fr[:, 1], fr[:, 2], fr[:, 3], fr[:, 4] = 0xA5, 0x5A, F_STREAM, rec & 0xFF, rec >> 8
            fr[:, 5] = 1 if want > link_rate else 0      # Overrun-Flag wie die echte Firmware
            fr[:, 6:-1] = codes.view(np.uint8).reshape(due, 2 * nch)
            fr[:, -1] = (fr[:, 2:-1].sum(axis=1) & 0xFF).astype(np.uint8)
            self.out += fr.tobytes()

    def _frame(self, ftype: int, payload: bytes):
        head = bytes([ftype, len(payload) & 0xFF, len(payload) >> 8])
        self.out += SYNC + head + payload + bytes([(sum(head) + sum(payload)) & 0xFF])

    def _info(self):
        self._frame(F_INFO, struct.pack(INFO_FMT, PROTO_VERSION, self.BUF_WORDS, self.mask,
                                        self.period, self.nsets, self.os, self.range))


# =============================================================================
# Ringpuffer für den Stream
# =============================================================================
class Rolling:
    """Hält die letzten `cap` Zeilen; view() liefert sie zusammenhängend ohne Kopie."""

    def __init__(self, cap: int, ncol: int):
        self.cap, self.ncol = cap, ncol
        self.a = np.empty((2 * cap, ncol))
        self.n = 0

    def extend(self, x: np.ndarray):
        k = len(x)
        if k >= self.cap:
            self.a[:self.cap] = x[-self.cap:]
            self.n = self.cap
            return
        if self.n + k > len(self.a):               # selten: Rest nach vorne schieben
            keep = self.cap - k
            self.a[:keep] = self.a[self.n - keep:self.n]
            self.n = keep
        self.a[self.n:self.n + k] = x
        self.n += k

    def view(self) -> np.ndarray:
        return self.a[max(0, self.n - self.cap):self.n]


# =============================================================================
# Hauptfenster
# =============================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, demo: bool = False):
        super().__init__()
        self.setWindowTitle("AD7606 Viewer")
        self.ser = None
        self.reader: SerialReader | None = None
        self.rx: queue.Queue = queue.Queue()
        self.dev: dict | None = None            # Inhalt des letzten INFO-Frames
        self.mode = "idle"                      # idle | run | stream
        self.waiting_burst = False
        self.burst_deadline = 0.0
        self.data_t: np.ndarray | None = None   # aktuell angezeigte Daten (CSV, Statistik)
        self.data_v: np.ndarray | None = None
        self.data_ch: list[int] = []
        self.data_fs = 5.0
        self.legend_ch: list[int] | None = None
        self.stream_buf: Rolling | None = None
        self.stream_count = 0
        self.stream_overruns = 0
        self.rate_t0 = time.monotonic()
        self.rate_n = 0
        self.last_stats = 0.0

        self._build_ui()
        self._refresh_ports(select_demo=demo)
        self._update_estimate()
        self._update_controls()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(30)
        if demo:
            self._toggle_connect()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        side = QtWidgets.QWidget()
        side.setFixedWidth(330)
        sl = QtWidgets.QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)

        # Verbindung
        g = QtWidgets.QGroupBox("Verbindung")
        gl = QtWidgets.QGridLayout(g)
        self.cb_port = QtWidgets.QComboBox()
        self.btn_refresh = QtWidgets.QPushButton("↻")
        self.btn_refresh.setFixedWidth(32)
        self.btn_refresh.setToolTip("Ports neu einlesen")
        self.btn_connect = QtWidgets.QPushButton("Verbinden")
        gl.addWidget(self.cb_port, 0, 0)
        gl.addWidget(self.btn_refresh, 0, 1)
        gl.addWidget(self.btn_connect, 1, 0, 1, 2)
        sl.addWidget(g)

        # Kanäle
        g = QtWidgets.QGroupBox("Kanäle")
        gl = QtWidgets.QGridLayout(g)
        self.cb_ch: list[QtWidgets.QCheckBox] = []
        for i in range(8):
            cb = QtWidgets.QCheckBox(f"V{i + 1}")
            cb.setChecked(True)
            pm = QtGui.QPixmap(12, 12)
            pm.fill(QtGui.QColor(CH_COLORS[i]))
            cb.setIcon(QtGui.QIcon(pm))
            cb.toggled.connect(lambda on, cb=cb: self._on_channel(cb, on))
            gl.addWidget(cb, i // 4, i % 4)
            self.cb_ch.append(cb)
        sl.addWidget(g)

        # Erfassung
        g = QtWidgets.QGroupBox("Erfassung")
        fl = QtWidgets.QFormLayout(g)
        self.sp_period = QtWidgets.QSpinBox()
        self.sp_period.setRange(10, 4_000_000)
        self.sp_period.setValue(100)
        self.sp_period.setSuffix(" µs")
        self.sp_period.setSingleStep(10)
        self.sp_period.setAccelerated(True)
        self.lbl_rate = QtWidgets.QLabel()
        self.sp_nsets = QtWidgets.QSpinBox()
        self.sp_nsets.setRange(1, 80)
        self.sp_nsets.setValue(80)
        self.cb_os = QtWidgets.QComboBox()
        self.cb_os.addItems(OS_LABELS)
        self.cb_range = QtWidgets.QComboBox()
        self.cb_range.addItems(["±5 V", "±10 V"])
        self.sp_window = QtWidgets.QDoubleSpinBox()
        self.sp_window.setRange(0.1, 30.0)
        self.sp_window.setValue(5.0)
        self.sp_window.setSuffix(" s")
        self.lbl_est = QtWidgets.QLabel()
        self.lbl_est.setWordWrap(True)
        fl.addRow("Abtastperiode", self.sp_period)
        fl.addRow("", self.lbl_rate)
        fl.addRow("Samples/Burst", self.sp_nsets)
        fl.addRow("Oversampling", self.cb_os)
        fl.addRow("Messbereich", self.cb_range)
        fl.addRow("Stream-Fenster", self.sp_window)
        fl.addRow(self.lbl_est)
        self.sp_period.valueChanged.connect(self._update_estimate)
        self.sp_nsets.valueChanged.connect(self._update_estimate)
        self.cb_os.currentIndexChanged.connect(self._update_estimate)
        sl.addWidget(g)

        # Steuerung
        g = QtWidgets.QGroupBox("Steuerung")
        gl = QtWidgets.QGridLayout(g)
        self.btn_single = QtWidgets.QPushButton("Einzelaufnahme")
        self.btn_run = QtWidgets.QPushButton("Run (Burst-Schleife)")
        self.btn_run.setCheckable(True)
        self.btn_stream = QtWidgets.QPushButton("Stream")
        self.btn_stream.setCheckable(True)
        self.btn_csv = QtWidgets.QPushButton("CSV speichern …")
        self.chk_yfix = QtWidgets.QCheckBox("Y-Achse fest auf Messbereich")
        gl.addWidget(self.btn_single, 0, 0)
        gl.addWidget(self.btn_run, 0, 1)
        gl.addWidget(self.btn_stream, 1, 0)
        gl.addWidget(self.btn_csv, 1, 1)
        gl.addWidget(self.chk_yfix, 2, 0, 1, 2)
        sl.addWidget(g)

        # Status + Log
        self.lbl_dev = QtWidgets.QLabel("Nicht verbunden")
        self.lbl_dev.setWordWrap(True)
        self.lbl_acq = QtWidgets.QLabel("")
        self.lbl_acq.setWordWrap(True)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        sl.addWidget(self.lbl_dev)
        sl.addWidget(self.lbl_acq)
        sl.addWidget(self.log, 1)

        # Plot
        pg.setConfigOptions(antialias=False)
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.setLabel("left", "Spannung", units="V")
        self.plot.setLabel("bottom", "Zeit", units="s")
        self.plot.setDownsampling(auto=True, mode="peak")
        self.plot.setClipToView(True)
        self.legend = self.plot.addLegend(offset=(10, 10))
        self.curves = [self.plot.plot(pen=pg.mkPen(CH_COLORS[i], width=1.5)) for i in range(8)]

        # Statistik
        self.table = QtWidgets.QTableWidget(8, 6)
        self.table.setHorizontalHeaderLabels(["Mittelwert", "Min", "Max", "Spitze-Spitze", "Std.-Abw.", "Samples"])
        self.table.setVerticalHeaderLabels([f"V{i + 1}" for i in range(8)])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        for i in range(8):
            for j in range(6):
                item = QtWidgets.QTableWidgetItem("")
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(i, j, item)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        split.addWidget(self.plot)
        split.addWidget(self.table)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)

        central = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(central)
        lay.addWidget(side)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)

        self.btn_refresh.clicked.connect(lambda: self._refresh_ports())
        self.btn_connect.clicked.connect(self._toggle_connect)
        self.btn_single.clicked.connect(self._start_burst)
        self.btn_run.toggled.connect(self._toggle_run)
        self.btn_stream.toggled.connect(self._toggle_stream)
        self.btn_csv.clicked.connect(self._save_csv)
        self.chk_yfix.toggled.connect(self._apply_yrange)

    def _log(self, msg: str):
        self.log.appendPlainText(time.strftime("%H:%M:%S  ") + msg)

    def _refresh_ports(self, select_demo: bool = False):
        self.cb_port.clear()
        best = -1
        for p in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device):
            self.cb_port.addItem(f"{p.device} – {p.description}", p.device)
            info = f"{p.description} {p.manufacturer or ''}"
            if best < 0 and any(k in info for k in ("Arduino", "CH340", "USB")):
                best = self.cb_port.count() - 1
        self.cb_port.addItem("Demo (simuliertes Gerät)", DEMO_PORT)
        if select_demo:
            self.cb_port.setCurrentIndex(self.cb_port.count() - 1)
        else:
            self.cb_port.setCurrentIndex(max(best, 0))

    def _mask(self) -> int:
        return sum(1 << i for i, cb in enumerate(self.cb_ch) if cb.isChecked())

    def _on_channel(self, cb: QtWidgets.QCheckBox, on: bool):
        if not on and self._mask() == 0:     # mindestens ein Kanal bleibt aktiv
            cb.setChecked(True)
            return
        self._update_estimate()

    def _update_estimate(self):
        """Grobe Abschätzung, wie schnell Uno + AD7606 mit den Einstellungen können."""
        ch = channels(self._mask()) or [0]
        nch, last = len(ch), max(ch) + 1
        buf_words = self.dev["buf_words"] if self.dev else 640
        self.sp_nsets.setMaximum(max(1, buf_words // nch))
        p = quantize_period(self.sp_period.value())
        t_burst = T_CONV_US[self.cb_os.currentIndex()] + 3.5 * last + 5   # Wandeln + SPI + Overhead
        frame = 7 + 2 * nch                                               # Bytes pro Stream-Frame
        t_stream = max(t_burst + 3 * frame, 10 * frame)                   # 10 µs/Byte bei 1 MBaud
        txt = f"= {fmt_rate(1e6 / p)}, Burst-Dauer {fmt_time(self.sp_nsets.value() * p * 1e-6)}"
        if p != self.sp_period.value():
            txt += f"\n(Gerät rundet auf {p} µs)"
        self.lbl_rate.setText(txt)
        self.lbl_est.setText(
            f"Kürzeste Periode ca. {t_burst:.0f} µs im Burst ({fmt_rate(1e6 / t_burst)}), "
            f"{t_stream:.0f} µs im Stream ({fmt_rate(1e6 / t_stream)})")
        self.lbl_est.setStyleSheet("color: #d33;" if p < t_burst else "color: gray;")

    def _update_controls(self):
        connected = self.ser is not None
        ready = connected and self.dev is not None
        idle = self.mode == "idle" and not self.waiting_burst
        self.btn_connect.setText("Trennen" if connected else "Verbinden")
        self.cb_port.setEnabled(not connected)
        self.btn_refresh.setEnabled(not connected)
        self.btn_single.setEnabled(ready and idle)
        self.btn_run.setEnabled(ready and (self.mode == "run" or idle))
        self.btn_stream.setEnabled(ready and (self.mode == "stream" or idle))
        for w in (*self.cb_ch, self.sp_period, self.sp_nsets, self.cb_os, self.cb_range):
            w.setEnabled(idle)

    # ------------------------------------------------------------ Verbindung
    def _toggle_connect(self):
        if self.ser is not None:
            self._disconnect()
            return
        port = self.cb_port.currentData()
        try:
            self.ser = DemoDevice() if port == DEMO_PORT else serial.Serial(port, BAUD, timeout=0.05)
        except Exception as e:
            self.ser = None
            QtWidgets.QMessageBox.critical(self, "Verbindung", f"Port konnte nicht geöffnet werden:\n{e}")
            return
        self.dev = None
        self.rx = queue.Queue()
        self.reader = SerialReader(self.ser, self.rx)
        self.reader.start()
        self._log(f"Verbunden: {self.cb_port.currentText()} – warte auf Gerät …")
        # Der Uno startet beim Öffnen des Ports neu (DTR) und meldet sich selbst.
        # Falls nicht (Board ohne Auto-Reset), nach 2,5 s aktiv nachfragen.
        QtCore.QTimer.singleShot(2500, self._request_info)
        self._update_controls()

    def _request_info(self):
        if self.ser is not None and self.dev is None:
            self._send("I\n")

    def _disconnect(self):
        was_stream = self.mode == "stream"
        self.mode, self.waiting_burst = "idle", False
        if self.ser is not None and was_stream:
            try:
                self.ser.write(b"X\n")
            except Exception:
                pass
        if self.reader:
            self.reader.running = False
            self.reader.join(0.5)
        try:
            self.ser.close()
        except Exception:
            pass
        self.ser, self.reader, self.dev = None, None, None
        for b in (self.btn_run, self.btn_stream):
            b.blockSignals(True)
            b.setChecked(False)
            b.blockSignals(False)
        self.lbl_dev.setText("Nicht verbunden")
        self._log("Getrennt")
        self._update_controls()

    def _send(self, text: str):
        if self.ser is None:
            return
        try:
            self.ser.write(text.encode("ascii"))
        except Exception as e:
            self._log(f"Sendefehler: {e}")
            self._disconnect()

    def _config_cmds(self) -> str:
        return (f"C{self._mask()}\nR{self.sp_period.value()}\nN{self.sp_nsets.value()}\n"
                f"O{self.cb_os.currentIndex()}\nG{self.cb_range.currentIndex()}\n")

    # ------------------------------------------------------------ Erfassung
    def _start_burst(self):
        self._send(self._config_cmds() + "B\n")
        n, p = self.sp_nsets.value(), quantize_period(self.sp_period.value())
        self.burst_deadline = time.monotonic() + n * p * 1e-6 + 2.0
        self.waiting_burst = True
        self._update_controls()

    def _toggle_run(self, on: bool):
        if on:
            self.mode = "run"
            if not self.waiting_burst:
                self._start_burst()
        else:
            self.mode = "idle"     # laufender Burst wird noch angezeigt
        self._update_controls()

    def _toggle_stream(self, on: bool):
        if on:
            self.mode = "stream"
            self.stream_buf, self.stream_count, self.stream_overruns = None, 0, 0
            self.rate_t0, self.rate_n = time.monotonic(), 0
            self._send(self._config_cmds() + "S\n")
        else:
            self._send("X\n")
            self.mode = "idle"
        self._update_controls()

    # ------------------------------------------------------------ Empfang
    def _poll(self):
        streams: list[bytes] = []
        while True:
            try:
                kind, payload = self.rx.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                if self.ser is not None:
                    self._log(f"Verbindungsfehler: {payload}")
                    self._disconnect()
                continue
            for ftype, p in payload:
                if ftype == F_STREAM:
                    streams.append(p)
                elif ftype == F_INFO:
                    self._on_info(p)
                elif ftype == F_BURST:
                    self._on_burst(p)
                elif ftype == F_TEXT:
                    self._log("Gerät: " + p.decode("ascii", "replace"))
        if streams and self.mode == "stream":
            self._on_stream(streams)
        if self.waiting_burst and time.monotonic() > self.burst_deadline:
            self.waiting_burst = False
            self._log("Timeout: keine Burst-Daten empfangen")
            if self.mode == "run":
                self._start_burst()
            self._update_controls()

    def _on_info(self, p: bytes):
        if len(p) != INFO_LEN:
            return
        proto, buf_words, mask, period, nsets, os_i, rng = struct.unpack(INFO_FMT, p)
        first = self.dev is None
        self.dev = dict(proto=proto, buf_words=buf_words, mask=mask, period=period,
                        nsets=nsets, os=os_i, range=rng)
        if first:
            self._log(f"Gerät bereit (Protokoll v{proto}, Puffer {buf_words} Werte)")
            if proto != PROTO_VERSION:
                self._log(f"⚠ Protokollversion {proto} erwartet {PROTO_VERSION}")
            self._update_estimate()
            self._update_controls()
        self.lbl_dev.setText(
            f"<b>Gerät:</b> {', '.join(f'V{i + 1}' for i in channels(mask))}<br>"
            f"{period} µs ({fmt_rate(1e6 / period)}), {nsets} Samples/Burst<br>"
            f"Oversampling {OS_LABELS[min(os_i, 6)]}, ±{full_scale(rng):g} V")

    def _bad_frames(self) -> str:
        bad = self.reader.parser.bad if self.reader else 0
        return f"\n⚠ {bad} fehlerhafte Frames verworfen" if bad else ""

    def _on_burst(self, p: bytes):
        mask, period, n, _os, rng, overruns = struct.unpack_from(BURST_FMT, p)
        ch = channels(mask)
        if len(p) != BURST_HDR + 2 * n * len(ch):
            self._log("Burst-Frame mit falscher Länge verworfen")
            return
        raw = np.frombuffer(p, "<i2", count=n * len(ch), offset=BURST_HDR).reshape(n, len(ch))
        fs = full_scale(rng)
        self._show(np.arange(n) * (period * 1e-6), raw * (fs / 32768.0), ch, fs, symbols=n <= 300)
        self._update_stats(force=True)
        msg = f"Burst: {n} Samples × {len(ch)} Kanäle @ {fmt_rate(1e6 / period)}, Dauer {fmt_time(n * period * 1e-6)}"
        if overruns:
            msg += f"\n⚠ {overruns} Overruns – Periode zu kurz für diese Einstellungen"
        self.lbl_acq.setText(msg + self._bad_frames())
        self.waiting_burst = False
        if self.mode == "run":
            self._start_burst()
        self._update_controls()

    def _on_stream(self, payloads: list[bytes]):
        if not self.dev:
            return
        ch = channels(self.dev["mask"])
        nch, rec = len(ch), 1 + 2 * len(ch)
        blob = b"".join(p for p in payloads if len(p) == rec)
        if not blob:
            return
        arr = np.frombuffer(blob, np.uint8).reshape(-1, rec)
        n = len(arr)
        self.stream_overruns += int(np.count_nonzero(arr[:, 0] & 1))
        fs, period = full_scale(self.dev["range"]), self.dev["period"]
        vals = np.ascontiguousarray(arr[:, 1:]).view("<i2") * (fs / 32768.0)
        t = (self.stream_count + np.arange(n)) * (period * 1e-6)
        self.stream_count += n
        self.rate_n += n

        cap = max(2, int(self.sp_window.value() * 1e6 / period))
        if self.stream_buf is None or self.stream_buf.cap != cap or self.stream_buf.ncol != nch + 1:
            old = self.stream_buf.view() if self.stream_buf is not None and self.stream_buf.ncol == nch + 1 else None
            self.stream_buf = Rolling(cap, nch + 1)
            if old is not None and len(old):
                self.stream_buf.extend(old)
        self.stream_buf.extend(np.column_stack([t, vals]))
        view = self.stream_buf.view()
        self._show(view[:, 0], view[:, 1:], ch, fs)
        self._update_stats()

        now = time.monotonic()
        if now - self.rate_t0 >= 1.0:
            rate = self.rate_n / (now - self.rate_t0)
            self.rate_t0, self.rate_n = now, 0
            msg = (f"Stream: {fmt_rate(rate)} empfangen (Soll {fmt_rate(1e6 / period)}), "
                   f"{self.stream_count} Samples gesamt")
            if self.stream_overruns:
                msg += (f"\n⚠ {self.stream_overruns} Samples verspätet – die USB-Verbindung schafft "
                        f"die Rate nicht, Zeitachse ungenau. Periode erhöhen oder Kanäle reduzieren.")
            self.lbl_acq.setText(msg + self._bad_frames())

    # ------------------------------------------------------------ Anzeige
    def _show(self, t: np.ndarray, v: np.ndarray, ch: list[int], fs: float, symbols: bool = False):
        self.data_t, self.data_v, self.data_ch, self.data_fs = t, v, list(ch), fs
        if self.legend_ch != self.data_ch:
            self.legend.clear()
            for i in ch:
                self.legend.addItem(self.curves[i], f"V{i + 1}")
            self.legend_ch = list(ch)
        tc = np.ascontiguousarray(t)
        for i, c in enumerate(self.curves):
            if i in ch:
                y = np.ascontiguousarray(v[:, ch.index(i)])
                if symbols:
                    c.setData(tc, y, symbol="o", symbolSize=4, symbolBrush=CH_COLORS[i], symbolPen=None)
                else:
                    c.setData(tc, y, symbol=None)
                c.setVisible(True)
            else:
                c.setData([], [])
                c.setVisible(False)
        self._apply_yrange()

    def _apply_yrange(self):
        if self.chk_yfix.isChecked():
            fs = self.data_fs if self.data_t is not None else full_scale(self.cb_range.currentIndex())
            self.plot.setYRange(-fs, fs, padding=0.02)
        else:
            self.plot.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

    def _update_stats(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self.last_stats < 0.25:
            return
        self.last_stats = now
        for i in range(8):
            if self.data_t is not None and i in self.data_ch and len(self.data_t):
                y = self.data_v[:, self.data_ch.index(i)]
                ymin, ymax = float(y.min()), float(y.max())
                vals = [f"{y.mean():+.4f} V", f"{ymin:+.4f} V", f"{ymax:+.4f} V",
                        f"{ymax - ymin:.4f} V", f"{y.std():.4f} V", str(len(y))]
            else:
                vals = [""] * 6
            for j, s in enumerate(vals):
                self.table.item(i, j).setText(s)

    def _save_csv(self):
        if self.data_t is None or not len(self.data_t):
            QtWidgets.QMessageBox.information(self, "CSV", "Noch keine Daten vorhanden.")
            return
        # Kopie ziehen, bevor der Dialog öffnet – ein laufender Stream schreibt weiter
        t, v, ch = self.data_t.copy(), self.data_v.copy(), list(self.data_ch)
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "CSV speichern", time.strftime("ad7606_%Y%m%d_%H%M%S.csv"), "CSV (*.csv)")
        if not path:
            return
        header = ",".join(["t_s"] + [f"V{i + 1}_V" for i in ch])
        np.savetxt(path, np.column_stack([t, v]), delimiter=",", header=header, comments="", fmt="%.7g")
        self._log(f"Gespeichert: {path} ({len(t)} Zeilen)")

    def closeEvent(self, e: QtGui.QCloseEvent):
        if self.ser is not None:
            self._disconnect()
        super().closeEvent(e)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow(demo="--demo" in sys.argv)
    w.resize(1300, 800)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
