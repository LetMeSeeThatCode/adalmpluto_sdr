import numpy as np
from gnuradio import gr


class blk(gr.sync_block):
    def __init__(self):
        gr.sync_block.__init__(
            self,
            name="ADS-B Detector",
            in_sig=[np.float32],
            out_sig=[np.float32]
        )

        # Keep enough history so packets spanning buffers aren't missed
        self.history = np.zeros(512, dtype=np.float32)

        # Prevent duplicate prints
        self.last_detection = -100000

    ####################################################################
    # Detect one ADS-B preamble (32 samples @ 4 MHz)
    ####################################################################
    def is_preamble(self, w):

        if len(w) != 32:
            return False

        pulse = np.mean(np.concatenate((
            w[0:2],
            w[4:6],
            w[14:16],
            w[18:20]
        )))

        gap = np.mean(np.concatenate((
            w[2:4],
            w[6:14],
            w[16:18],
            w[20:32]
        )))

        if pulse < 0.02:
            return False

        if pulse < 3 * gap:
            return False

        return True

    ####################################################################
    # Search an entire buffer
    ####################################################################
    def find_preambles(self, samples):

        detections = []

        for i in range(len(samples) - 32):

            if self.is_preamble(samples[i:i+32]):
                detections.append(i)

        return detections

    ####################################################################
    # GNU Radio work()
    ####################################################################
    def work(self, input_items, output_items):

        x = input_items[0]
        y = output_items[0]

        y[:] = x

        samples = np.concatenate((self.history, x))

        detections = self.find_preambles(samples)

        for d in detections:

            if d - self.last_detection > 64:

                print("Detection:", d)
                print(np.round(samples[d:d+40], 3))
                print()

                self.last_detection = d

        self.history = samples[-512:]

        return len(y)
