#!/usr/bin/env python3
"""
adsb_preamble_finder.py - Claude AI

- ADS-B (Mode S Extended Squitter, 1090 MHz) preamble detector for magnitude data captured from an ADALM-PLUTO SDR.
- Original data collected at 4MHz to gaurantee pulse recognition.
- ADS-B uses PPM

ADS-B preamble structure
-------------------------
    8 microseconds and (four due to 4MHz sampling rate) pulses at: 0.0 us, 1.0 us, 3.5 us, 4.5 us
    This script scans the magnitude waveform for that pulse pattern using a vectorized numpy comparison.

Reccomendations
---------------
    Sample rate should be a multiple of 2 MHz for clean sample alignment.
    Pass --rate to match your capture.

Usage
-----
    python3 adsb_preamble_finder.py adsb_raw_4000000Hz_1783781677_mag_char --rate 4000000

    python3 adsb_preamble_finder.py capture.bin --rate 4000000 \\
        --dtype float32 --threshold 2.0 
"""

import argparse
import numpy as np
from pyModeS import decode
import pandas as pd
from pathlib import Path

def find_preambles(mag, rate=2_000_000, threshold_ratio=2.0, min_amplitude=0.0):
    """
    Vectorized search for ADS-B preambles in a magnitude waveform.

    Parameters
    ----------
    mag : 1D numpy array of magnitude samples
    rate : sample rate in Hz used for the capture
    threshold_ratio : how many times stronger the 4 pulse samples must be
        vs. the quiet samples in the same window (higher = stricter)
    min_amplitude : absolute floor the pulses must exceed (helps ignore
        noise floor triggering false positives on quiet recordings)

    Returns
    -------
    numpy array of sample indices where a preamble was found (index marks
    the start of the preamble, i.e. the first pulse at t=0us).
    """
    samples_per_us = rate / 1_000_000.0
    if not float(samples_per_us).is_integer():
        print(f"[warn] rate {rate} Hz is not an integer number of samples "
              f"per microsecond ({samples_per_us}); pulse alignment may be "
              f"imprecise. Prefer a rate that's a whole multiple of 1 MHz.")

    pulse_us = np.array([0.0, 1.0, 3.5, 4.5])
    pulse_idx = np.round(pulse_us * samples_per_us).astype(int)
    preamble_len = int(round(8 * samples_per_us))

    n_windows = len(mag) - preamble_len
    if n_windows <= 0:
        return np.array([], dtype=int)

    # memory-efficient overlapping windows (view, not a copy)
    windows = np.lib.stride_tricks.sliding_window_view(mag, preamble_len)[:n_windows]

    # pulse (should-be-high) samples
    peaks = windows[:, pulse_idx]
    peak_min = peaks.min(axis=1)  # weakest of the 4 pulses

    # everything else in the window (should-be-low), excluding a 1-sample
    # guard band around each pulse to avoid penalizing pulse rise/fall edges
    low_mask = np.ones(preamble_len, dtype=bool)
    for i in pulse_idx:
        low_mask[max(0, i - 1): i + 2] = False
    lows = windows[:, low_mask]
    low_max = lows.max(axis=1)  # strongest "quiet" sample

    candidates = np.where(
        (peak_min > threshold_ratio * low_max) & (peak_min > min_amplitude)
    )[0]

    return candidates


def dedupe_hits(hits, min_spacing):
    """Collapse clusters of adjacent hits (from a sliding window) down to
    the strongest single detection per cluster, keeping just the first hit
    of each run that's close together."""
    if len(hits) == 0:
        return hits
    keep = [hits[0]]
    for h in hits[1:]:
        if h - keep[-1] > min_spacing:
            keep.append(h)
    return np.array(keep)


def demod_mode_s(mag, start_idx, rate=2_000_000, max_bits=112):
    """
    Demodulate the Mode S message that follows a detected preamble.

    Mode S uses PPM (pulse position modulation): each bit occupies 1us, split into two 0.5us half-bit slots.
        bit = 1  ->  first half-bit HIGH, second half-bit LOW
        bit = 0  ->  first half-bit LOW,  second half-bit HIGH
    So each bit is decoded just by comparing the two half-bit samples.

    Parameters
    ----------
    mag : 1D numpy magnitude array (the same one preambles were found in)
    start_idx : sample index where the preamble begins, as returned by
        find_preambles()
    rate : sample rate in Hz
    max_bits : most bits to ever attempt (112 covers the longest Mode S
        "long" squitter; you shouldn't need to change this)

    Returns
    -------
    dict with:
        'bits'        : list of decoded 0/1 ints (56 or 112 long)
        'hex'         : hex string of the packed message bytes
        'df'          : Downlink Format (0-31), read from the first 5 bits
        'length_bits' : 56 (short squitter) or 112 (long squitter)
    Returns None if the capture ends before even the DF field is decodable.
    """
    samples_per_us = rate / 1_000_000.0
    preamble_len = int(round(8 * samples_per_us))
    data_start = start_idx + preamble_len

    bits = []
    for i in range(max_bits):
        i0 = data_start + int(round(i * samples_per_us))
        i1 = data_start + int(round((i + 0.5) * samples_per_us))
        if i1 >= len(mag):
            break
        bits.append(1 if mag[i0] > mag[i1] else 0)

    if len(bits) < 5:
        return None  # capture cut off before we could even read the DF

    df = int("".join(map(str, bits[:5])), 2)
    length_bits = 112 if df >= 16 else 56
    length_bits = min(length_bits, len(bits)) 
    bits = bits[:length_bits]

    # pack bits -> bytes -> hex string
    nbytes = (length_bits + 7) // 8
    padded = bits + [0] * (nbytes * 8 - length_bits)
    byte_vals = [
        int("".join(map(str, padded[b * 8:(b + 1) * 8])), 2) for b in range(nbytes)
    ]
    return {
        "bits": bits,
        "hex": "".join(f"{b:02X}" for b in byte_vals),
        "df": df,
        "length_bits": length_bits,
    }


def mode_s_crc(bits):
    """
    Compute the 24-bit Mode S / ADS-B CRC remainder for a message.

    Parameters
    ----------
    - Number of bits

    Returns
    -------
    24-bit integer remainder.
        * DF17 / DF18 (ADS-B): a valid, uncorrupted message returns 0.
        * DF11 (all-call reply): a valid message returns the aircraft's
          ICAO address XORed with the transmitted CRC field.
        * Anything else (for DF17/18) means the message is corrupted and
          should be discarded.
    """
    poly = 0xFFF409  # Mode S CRC-24 generator polynomial
    reg = 0
    for bit in bits:
        reg ^= (bit << 23)
        if reg & 0x800000:
            reg = ((reg << 1) ^ poly) & 0xFFFFFF
        else:
            reg = (reg << 1) & 0xFFFFFF
    return reg


def main():
    flight_dict = [] 
    ap = argparse.ArgumentParser(description="Find ADS-B preambles in a Pluto SDR magnitude capture.")
    ap.add_argument("infile", help="path to binary magnitude file")
    ap.add_argument("--rate", type=float, default=2_000_000,
                     help="sample rate in Hz (default: 2000000)")
    ap.add_argument("--dtype", default="float32",
                     help="numpy dtype of the samples in the file (default: float32). "
                          "Common alternatives: uint8, int16, uint16, float64")
    ap.add_argument("--threshold", type=float, default=2.0,
                     help="pulse-to-quiet amplitude ratio required (default: 2.0)")
    ap.add_argument("--min-amplitude", type=float, default=0.0,
                     help="absolute amplitude floor for pulses (default: 0, i.e. off)")
    args = ap.parse_args()

    mag = np.fromfile(args.infile, dtype=np.dtype(args.dtype)).astype(np.float32)
    print(f"Loaded {len(mag):,} samples ({len(mag) / args.rate * 1000:.2f} ms) "
          f"from {args.infile}")

    input_path = Path(args.infile)
    filename = input_path.stem 
    hits = find_preambles(
        mag,
        rate=args.rate,
        threshold_ratio=args.threshold,
        min_amplitude=args.min_amplitude,
    )

    preamble_len = int(round(8 * args.rate / 1_000_000))
    hits = dedupe_hits(hits, min_spacing=preamble_len)
    n_valid = 0
    
    for h in hits[:]:
        t_ms = h / args.rate * 1000
        msg = demod_mode_s(mag, h, rate=args.rate)
        if msg is None:
            print(f"  sample {h:>10}  |  t = {t_ms:.4f} ms  |  (truncated, capture ends too soon)")
            continue

        checksum = mode_s_crc(msg["bits"])

        #status = "OK " if valid else ("?  " if valid is None else "BAD")
        if valid:
            n_valid += 1

        result = decode(msg["hex"])
        flight_dict.append(result)
    
    df = pd.DataFrame(flight_dict)
    df.to_csv(f'ADS_B_data_{filename}.csv')


if __name__ == "__main__":
    main()
