import numpy as np
import glob
import plotly.graph_objs as go
from plotly.offline import plot

# Decimation factor
decimation_factor = 1

# Mode-S frame length in bits
mode_s_frame_length_bits = 112

# Samples per bit for Manchester decoding
sample_rate = 5e6 # 5 Msps
bit_rate = 1e6 # 1 Mbaud
samples_per_bit = int(sample_rate / bit_rate)

# Calculate the number of samples per Mode-S frame
samples_per_frame = mode_s_frame_length_bits * samples_per_bit

# Search .dat files in the current directory
data_files = glob.glob("*.dat")

# generate plotly traces for each data file
traces = []
for file in data_files:
    data = np.fromfile(open(file), dtype=np.float32)
    
    # Add trace only if data length is sufficient
    if len(data) >= samples_per_frame:
        trace = go.Scatter(
            x=np.linspace(0, len(data)-1, len(data)),
            y=data_decimate,
            mode='lines+markers',
            name=file
        )
        traces.append(trace)
# Create layout for the plot
layout = go.Layout(
    title='Data from .dat files',
    xaxis=dict(title='X-axis'),
    yaxis=dict(title='Y-axis'),
)
fig = go.Figure(data=traces, layout=layout)

# Save the plot as an HTML file
plot(fig, filename='data_plot.html')

def synchronize_data(data):
    data = np.asarray(data)
    # Normalize data
    bits = (data > 0).astype(np.uint8)

    p1 = np.array([1,1,1,0,0,1,1,1], dtype=np.uint8)
    p2 = np.array([1,1,0,0,1,1,1,0], dtype=np.uint8)

    if bits.size >= 8 and np.array_equal(bits[:8], p2):
        out = np.concatenate((np.array([1.0], dtype=np.float32),
                              data.astype(np.float32)))
        return out
    elif bits.size >= 8 and np.array_equal(bits[:8], p1):
        return data.astype(np.float32)
    else:
        # default
        return data.astype(np.float32)
    
# Decimation factor
decimation_factor = 5

# Add trace only if data length is sufficient
if len(data) >= samples_per_frame:
    data_sync = synchronize_data(data)
    data_decimate = data_sync[1::decimation_factor]
    trace = go.Scatter(
        x=np.linspace(0, len(data_decimate)-1, len(data_decimate)),
        y=data_decimate,
        mode='lines+markers',
        name=file
    )
    traces.append(trace)

def decode_mode_s(data):
    messages = []
    data = np.asarray(data[8:120])  # Skip the first 8 samples used for synchronization
    bits = (data > 0.5).astype(np.uint8)

    nibbles = bits.reshape(-1,4).tolist()

    weights = np.array([8, 4, 2, 1], dtype=np.uint8)
    vals = (nibbles * weights).sum(axis=1) # Convert 4 bits to a nibble value

    # Lookup table for hex characters
    lut = np.frombuffer(b"0123456789ABCDEF", dtype="S1")
    hex_chars = lut[vals].astype(str)               # array de '0'..'F'
    hex_str = "".join(hex_chars.tolist())

    message = pms.tell(hex_str)

    if message is not None:
        return message