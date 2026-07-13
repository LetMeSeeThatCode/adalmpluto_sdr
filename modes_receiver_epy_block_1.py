import numpy as np
import pmt
from gnuradio import gr

class adsb_extractor(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(self,
            name="ADSB Bit Extractor",
            in_sig=[np.dtype(np.uint8)],
            out_sig=[np.dtype(np.uint8)])
        self.capturing = False
        self.buffer = []

    def work(self, input_items, output_items):
        in0 = input_items[0]
        out = output_items[0]
        
        # Check for tags in the current window
        tags = self.get_tags_in_range(0, 0, len(in0))
        for tag in tags:
            if tag.key == pmt.intern("preamble"):
                self.capturing = True
                self.buffer = []

        # Process samples
        for i, val in enumerate(in0):
            if self.capturing:
                self.buffer.append(val)
                if len(self.buffer) == 112:  # ADS-B message length
                    bits_str = ''.join(str(int(b)) for b in self.buffer)
                    print(f"ADS-B: {bits_str}")
                    self.capturing = False
                    self.buffer = []
            out[i] = val

        return len(out)