#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# ADS-B / Mode-S receiver for PlutoSDR
# GNU Radio 3.10 — embedded Python block handles all signal processing:
#   preamble detection → bit extraction → CRC check → hex output
#
# Dependencies:
#   pip install pyModeS watchdog rich
#
# Output: one hex string per valid DF17/18 frame appended to OUTPUT_FILE
# Run decoder.py in a second terminal to display traffic.

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import blocks
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
from gnuradio import iio
import numpy as np
import sip

# ── output file watched by decoder.py ────────────────────────────────────────
OUTPUT_FILE = "/home/bri/workspace/mode_s_decode/controlpathsdotcom/adsb_bursts.txt"


# ─────────────────────────────────────────────────────────────────────────────
#  Embedded Python Block — ADS-B frame extractor
#
#  Input:  float stream — thresholded OOK signal (0.0 / 1.0) at SAMP_RATE
#  Output: passthrough of input (connect to time sink for monitoring)
#
#  Signal timing at 5 Msps / 1 Mbps:
#    samples_per_bit  = 5
#    preamble         = 8 µs  → 8 chips × 5 samples = 40 samples
#    DF17/18 payload  = 112 bits → 560 samples
#    total frame      = 600 samples
#
#  Preamble chip pattern (half-bit level, 1 sample per chip):
#    1 1 0 0 1 0 1 0 0 0 0 0 0 0 0 0   (16 chips = 8 µs at 2 chips/µs)
#  Sampled at 5 Msps → each chip = 2.5 samples, so we look at the
#  centre sample of each half-bit window.
# ─────────────────────────────────────────────────────────────────────────────

class adsb_frame_extractor(gr.sync_block):
    """
    Detects ADS-B (DF17/18) frames in a thresholded OOK float stream.
    Validates CRC and writes hex strings to OUTPUT_FILE.
    """

    SAMP_RATE       = 5_000_000
    BIT_RATE        = 1_000_000
    SPB             = SAMP_RATE // BIT_RATE   # samples per bit = 5
    # Preamble: 8 µs of known chip pattern before the data field.
    # At 5 Msps each bit = 5 samples.  PPM encoding means each bit is
    # represented by a pulse in the first or second half.
    # We detect the preamble by checking the *centre* of each 5-sample bit.
    PREAMBLE_BITS   = [1, 0, 1, 0, 0, 0, 0, 0]   # 8-bit preamble word
    PREAMBLE_LEN    = len(PREAMBLE_BITS)            # 8 bits
    PAYLOAD_BITS    = 112                            # DF17/18 long squitter
    FRAME_SAMPLES   = (PREAMBLE_LEN + PAYLOAD_BITS) * SPB   # 600 samples

    def __init__(self, output_file=OUTPUT_FILE):
        gr.sync_block.__init__(
            self,
            name="ADS-B Frame Extractor",
            in_sig=[np.float32],
            out_sig=[np.float32],
        )
        self._output_file = output_file
        self._buf = np.zeros(self.FRAME_SAMPLES * 4, dtype=np.float32)
        self._buf_len = 0
        self._fh = open(output_file, "a", buffering=1)   # line-buffered

    # ── CRC-24 as specified in ICAO Annex 10 ─────────────────────────────
    _GENERATOR = 0xFFF409

    @staticmethod
    def _crc24(data_bytes):
        crc = 0
        for byte in data_bytes:
            for i in range(7, -1, -1):
                bit = (byte >> i) & 1
                if (crc >> 23) & 1:
                    crc = ((crc << 1) | bit) ^ adsb_frame_extractor._GENERATOR
                else:
                    crc = (crc << 1) | bit
                crc &= 0xFFFFFF
        return crc

    # ── sample the centre of each bit period ─────────────────────────────
    def _sample_bits(self, samples, count):
        """Return `count` bits sampled from the centre of each SPB window."""
        centre = self.SPB // 2   # sample index 2 inside each 5-sample bit
        bits = []
        for i in range(count):
            bits.append(1 if samples[i * self.SPB + centre] > 0.5 else 0)
        return bits

    # ── convert bit list to hex string ────────────────────────────────────
    @staticmethod
    def _bits_to_hex(bits):
        n = len(bits)
        assert n % 8 == 0
        out = []
        for i in range(0, n, 8):
            byte = 0
            for b in bits[i:i+8]:
                byte = (byte << 1) | b
            out.append(byte)
        return bytes(out).hex().upper()

    # ── GNU Radio work() ─────────────────────────────────────────────────
    def work(self, input_items, output_items):
        inp = input_items[0]
        n   = len(inp)

        # Accumulate into rolling buffer
        needed = self.FRAME_SAMPLES * 2
        self._buf = np.concatenate([self._buf[-needed:], inp])
        buf = self._buf
        L   = len(buf)

        pos = 0
        while pos + self.FRAME_SAMPLES <= L:
            # Quick gate: first sample must be 1
            if buf[pos] < 0.5:
                pos += 1
                continue

            # Check preamble bits
            preamble_ok = True
            for k, expected in enumerate(self.PREAMBLE_BITS):
                centre = k * self.SPB + self.SPB // 2
                got = 1 if buf[pos + centre] > 0.5 else 0
                if got != expected:
                    preamble_ok = False
                    break

            if not preamble_ok:
                pos += 1
                continue

            # Extract 112 payload bits that follow the 8-bit preamble
            payload_start = pos + self.PREAMBLE_LEN * self.SPB
            if payload_start + self.PAYLOAD_BITS * self.SPB > L:
                break   # not enough data yet

            payload_bits = self._sample_bits(
                buf[payload_start:], self.PAYLOAD_BITS
            )

            # Convert to bytes for CRC check
            hex_str = self._bits_to_hex(payload_bits)
            raw     = bytes.fromhex(hex_str)

            # Downlink Format lives in the top 5 bits of byte 0
            df = raw[0] >> 3

            if df in (17, 18):
                # CRC covers bytes 0..13; last 3 bytes ARE the parity
                msg_bytes = raw[:11]
                parity    = raw[11:14]
                computed  = self._crc24(msg_bytes)
                received  = int.from_bytes(parity, "big")

                if computed == received:
                    self._fh.write(hex_str + "\n")

            # Advance past this frame to avoid overlapping detections
            pos += self.PAYLOAD_BITS * self.SPB

        output_items[0][:n] = inp
        return n


# ─────────────────────────────────────────────────────────────────────────────
#  Top-level GNU Radio flowgraph
# ─────────────────────────────────────────────────────────────────────────────

class modes_receiver(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "ADS-B Receiver", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("ADS-B Receiver — 1090 MHz")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)

        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("GNU Radio", "modes_receiver")
        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)

        # ── Variables ────────────────────────────────────────────────────
        #
        # 5 Msps is critical — the Python block's timing constants assume it.
        # Do NOT change samp_rate without also updating SPB in the block above.
        #
        self.samp_rate   = samp_rate   = 5_000_000
        self.center_freq = center_freq = 1_090_000_000

        # ── PlutoSDR source ──────────────────────────────────────────────
        self.iio_pluto_source_0 = iio.fmcomms2_source_fc32(
            'ip:pluto.local' if 'ip:pluto.local' else iio.get_pluto_uri(),
            [True, True], 64000
        )
        self.iio_pluto_source_0.set_len_tag_key('packet_len')
        self.iio_pluto_source_0.set_frequency(center_freq)
        self.iio_pluto_source_0.set_samplerate(samp_rate)
        self.iio_pluto_source_0.set_gain_mode(0, 'manual')
        self.iio_pluto_source_0.set_gain(0, 30)       # tune 20–40 dB to taste
        self.iio_pluto_source_0.set_quadrature(True)
        self.iio_pluto_source_0.set_rfdc(True)
        self.iio_pluto_source_0.set_bbdc(True)
        self.iio_pluto_source_0.set_filter_params('Auto', '', 0, 0)

        # ── IQ → envelope ────────────────────────────────────────────────
        # complex_to_mag gives the true RF envelope (sqrt of power).
        # Do NOT use complex_to_mag_squared — it distorts pulse shapes and
        # makes threshold tuning very environment-sensitive.
        self.blocks_complex_to_mag_0 = blocks.complex_to_mag(1)

        # ── Low-pass filter ──────────────────────────────────────────────
        # Cuts off above ~1.5 MHz to reject adjacent interference while
        # keeping ADS-B pulse edges (1 Mbps → 1 MHz Nyquist) intact.
        # Transition band 1.5–2.0 MHz keeps group delay low.
        self.low_pass_filter_0 = filter.fir_filter_fff(
            1,                          # decimation = 1 (keep 5 Msps)
            firdes.low_pass(
                1,                      # gain
                samp_rate,              # sampling rate
                1_500_000,              # cutoff Hz
                500_000,                # transition width Hz
                window.WIN_HAMMING,
                6.76
            )
        )

        # ── Threshold → binary OOK ───────────────────────────────────────
        # Low threshold (hysteresis lower edge):  adjust if noise triggers
        # High threshold (hysteresis upper edge): adjust if signal clipped
        # Start here and tune while watching the time sink:
        #   - If you see no pulses:  lower both values
        #   - If noise fills gaps:   raise the low value
        # Rule of thumb: high ≈ 3–5× noise floor; low ≈ 0.5× high.
        self.blocks_threshold_ff_0 = blocks.threshold_ff(0.01, 0.03, 0)

        # ── ADS-B frame extractor (embedded Python block) ────────────────
        self.adsb_extractor = adsb_frame_extractor(OUTPUT_FILE)

        # ── Qt sinks ─────────────────────────────────────────────────────
        self.qtgui_waterfall_sink_x_0 = qtgui.waterfall_sink_c(
            1024, window.WIN_BLACKMAN_hARRIS,
            center_freq, samp_rate, "", 1, None
        )
        self.qtgui_waterfall_sink_x_0.set_update_time(0.10)
        self.qtgui_waterfall_sink_x_0.enable_grid(False)
        self.qtgui_waterfall_sink_x_0.enable_axis_labels(True)
        self.qtgui_waterfall_sink_x_0.set_intensity_range(-140, 10)
        self._qtgui_waterfall_sink_x_0_win = sip.wrapinstance(
            self.qtgui_waterfall_sink_x_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_waterfall_sink_x_0_win)

        self.qtgui_freq_sink_x_0 = qtgui.freq_sink_c(
            1024, window.WIN_BLACKMAN_hARRIS,
            center_freq, samp_rate, "", 1, None
        )
        self.qtgui_freq_sink_x_0.set_update_time(0.10)
        self.qtgui_freq_sink_x_0.set_y_axis(-140, 10)
        self.qtgui_freq_sink_x_0.set_y_label('Relative Gain', 'dB')
        self.qtgui_freq_sink_x_0.enable_autoscale(False)
        self.qtgui_freq_sink_x_0.enable_grid(False)
        self.qtgui_freq_sink_x_0.set_fft_average(1.0)
        self._qtgui_freq_sink_x_0_win = sip.wrapinstance(
            self.qtgui_freq_sink_x_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_freq_sink_x_0_win)

        # Time sink shows raw envelope (blue) and thresholded OOK (red)
        self.qtgui_time_sink_x_0_0 = qtgui.time_sink_f(
            40000, samp_rate, 'OOK Decoder', 2, None
        )
        self.qtgui_time_sink_x_0_0.set_update_time(0.10)
        self.qtgui_time_sink_x_0_0.set_y_axis(-0.1, 1.5)
        self.qtgui_time_sink_x_0_0.set_y_label('Amplitude', "")
        self.qtgui_time_sink_x_0_0.enable_tags(True)
        self.qtgui_time_sink_x_0_0.set_trigger_mode(
            qtgui.TRIG_MODE_FREE, qtgui.TRIG_SLOPE_POS, 0.0, 0, 0, "")
        self.qtgui_time_sink_x_0_0.enable_autoscale(False)
        self.qtgui_time_sink_x_0_0.enable_grid(True)

        labels = ['Envelope (post-LPF)', 'OOK binary',
                  '', '', '', '', '', '', '', '']
        colors = ['blue', 'red', 'green', 'black', 'cyan',
                  'magenta', 'yellow', 'dark red', 'dark green', 'dark blue']
        for i in range(2):
            self.qtgui_time_sink_x_0_0.set_line_label(i, labels[i])
            self.qtgui_time_sink_x_0_0.set_line_color(i, colors[i])
            self.qtgui_time_sink_x_0_0.set_line_width(i, 1)
            self.qtgui_time_sink_x_0_0.set_line_alpha(i, 1.0)

        self._qtgui_time_sink_x_0_0_win = sip.wrapinstance(
            self.qtgui_time_sink_x_0_0.qwidget(), Qt.QWidget)
        self.top_layout.addWidget(self._qtgui_time_sink_x_0_0_win)

        # ── Connections ───────────────────────────────────────────────────
        #
        #  PlutoSDR ──► complex_to_mag ──► LPF ──► threshold ──► extractor
        #         │                  │                    │
        #         ▼                  ▼                    ▼
        #    freq sink        time sink [0]          time sink [1]
        #    waterfall
        #
        self.connect((self.iio_pluto_source_0,    0), (self.blocks_complex_to_mag_0,  0))
        self.connect((self.iio_pluto_source_0,    0), (self.qtgui_freq_sink_x_0,      0))
        self.connect((self.iio_pluto_source_0,    0), (self.qtgui_waterfall_sink_x_0, 0))
        self.connect((self.blocks_complex_to_mag_0, 0), (self.low_pass_filter_0,      0))
        self.connect((self.low_pass_filter_0,     0), (self.qtgui_time_sink_x_0_0,   0))
        self.connect((self.low_pass_filter_0,     0), (self.blocks_threshold_ff_0,    0))
        self.connect((self.blocks_threshold_ff_0, 0), (self.adsb_extractor,           0))
        self.connect((self.adsb_extractor,        0), (self.qtgui_time_sink_x_0_0,   1))

    def closeEvent(self, event):
        self.settings = Qt.QSettings("GNU Radio", "modes_receiver")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()
        event.accept()

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.iio_pluto_source_0.set_samplerate(self.samp_rate)
        self.low_pass_filter_0.set_taps(firdes.low_pass(
            1, self.samp_rate, 1_500_000, 500_000, window.WIN_HAMMING, 6.76))
        self.qtgui_freq_sink_x_0.set_frequency_range(self.center_freq, self.samp_rate)
        self.qtgui_time_sink_x_0_0.set_samp_rate(self.samp_rate)
        self.qtgui_waterfall_sink_x_0.set_frequency_range(self.center_freq, self.samp_rate)

    def get_center_freq(self):
        return self.center_freq

    def set_center_freq(self, center_freq):
        self.center_freq = center_freq
        self.iio_pluto_source_0.set_frequency(self.center_freq)
        self.qtgui_freq_sink_x_0.set_frequency_range(self.center_freq, self.samp_rate)
        self.qtgui_waterfall_sink_x_0.set_frequency_range(self.center_freq, self.samp_rate)


def main(top_block_cls=modes_receiver, options=None):
    qapp = Qt.QApplication(sys.argv)
    tb = top_block_cls()
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT,  sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)
    qapp.exec_()


if __name__ == '__main__':
    main()