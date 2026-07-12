#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5 import Qt
from gnuradio import qtgui, blocks, gr
from gnuradio.fft import window
from gnuradio import iio
import sys, signal, sip


class modes_receiver(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Mode S Simple Detector", catch_exceptions=True)
        Qt.QWidget.__init__(self)

        self.setWindowTitle("Mode S Simple Detector")

        self.top_layout = Qt.QVBoxLayout(self)

        # -----------------------
        # Parameters
        # -----------------------
        self.samp_rate = 8000000   # better than 5 MS/s
        self.center_freq = 1090000000

        # -----------------------
        # SDR Source (Pluto)
        # -----------------------
        self.src = iio.fmcomms2_source_fc32(
            '' if '' else iio.get_pluto_uri(),
            [True, True],
            1024
        )

        self.src.set_frequency(self.center_freq)
        self.src.set_samplerate(self.samp_rate)
        self.src.set_gain_mode(0, False)
        self.src.set_gain(0, 40)   # IMPORTANT: avoid clipping
        self.src.set_quadrature(True)
        self.src.set_rfdc(True)
        self.src.set_bbdc(True)

        # -----------------------
        # Signal Processing
        # -----------------------

        # Envelope detection (correct for RF pulses)
        self.mag2 = blocks.complex_to_mag_squared(1)

        # Peak detector (replaces threshold + burst logic)
        self.peak = blocks.peak_detector2_fb(
            threshold_factor_rise=0.4,
            threshold_factor_fall=0.2,
            look_ahead=1,
            alpha=0.001
        )

        # -----------------------
        # GUI Sinks
        # -----------------------

        self.time_sink = qtgui.time_sink_f(
            20000,
            self.samp_rate,
            "Mode S Signal View",
            2
        )

        self.time_sink.set_y_axis(0, 0.02)

        self.freq_sink = qtgui.freq_sink_c(
            1024,
            window.WIN_BLACKMAN_hARRIS,
            self.center_freq,
            self.samp_rate,
            "Spectrum",
            1
        )

        # -----------------------
        # Connections
        # -----------------------

        self.connect(self.src, self.mag2)
        self.connect(self.mag2, self.peak)

        self.connect(self.mag2, (self.time_sink, 0))
        self.connect(self.peak, (self.time_sink, 1))

        self.connect(self.src, self.freq_sink)

        # -----------------------
        # Add to GUI
        # -----------------------
        self.top_layout.addWidget(sip.wrapinstance(self.time_sink.qwidget(), Qt.QWidget))
        self.top_layout.addWidget(sip.wrapinstance(self.freq_sink.qwidget(), Qt.QWidget))

    def closeEvent(self, event):
        self.stop()
        self.wait()
        event.accept()


def main():
    qapp = Qt.QApplication(sys.argv)
    tb = modes_receiver()
    tb.start()
    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    qapp.exec_()


if __name__ == "__main__":
    main()