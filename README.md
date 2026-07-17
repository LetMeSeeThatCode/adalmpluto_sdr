# ADALM-Pluto SDR: ADS-B Signal Processing

## Project Idea
Get to know GNU Radio and signal processing.

- 📡 Real-time ADS-B signal capture with ADALM-Pluto SDR / GNU Radio
- 🔍 Decoding of aircraft position, altitude, and flight ID using pyModeS

## 🛠️ Prerequisites

### Hardware
- ADALM-Pluto SDR (Analog Devices)
- USB 3.0 cable
- Antenna

### Software
- Python 3.8+
- GNU Radio (≥ 3.10)
- pyModeS

## 🚀 Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/LetMeSeeThatCode/adalmpluto_sdr.git
   cd adalmpluto_sdr
   ```

## ▶️ Running the Project

1. Open `plutoSDR_adsb_msg_receiver.grc` in GNU Radio.

2. Run the flowgraph to record ADS-B data.
   - **Recommendation:** Stop the recording after about **10 seconds**, otherwise the generated files can become very large.

3. Process the recorded data from the command line:

   ```bash
   python3 adsb_preamble_finder.py file.bin --rate 4000000
   ```
To run example:
   ```bash
   python3 adsb_preamble_finder.py adsb_raw_4000000Hz_1783781677_mag_char --rate 4000000
   ```
