"""
Embedded Python Blocks:

Each time this file is saved, GRC will instantiate the first class it finds
to get ports and parameters of your block. The arguments to __init__  will
be the parameters. All of them are required to have default values!
"""

import numpy as np
from gnuradio import gr

preamble_found = False

class blk(gr.sync_block):  # other base classes are basic_block, decim_block, interp_block
    """Post processing of collected data"""

    def __init__(self, file=''):  # only default arguments here
        """arguments to this function show up as parameters in GRC"""
        gr.sync_block.__init__(
            self,
            name='Binary File Processor',   # will show up in GRC
            in_sig=[np.float32],
            out_sig=[np.float32]
        )
        # if an attribute with the same name as a parameter is found,
        # a callback is registered (properties work, too).
        self.filename = file
    #1010001010000000
    def find_sequence_in_binary_file(filename, sequence="1010001010000000"):
        """Check a binary file for the presence of a specific bit sequence.
        
        Args:
            filename (str): Path to the binary file to check
            sequence (str): The bit sequence to search for (e.g., "1010001010000000")
        
        Returns:
            list: List of byte offsets where the sequence starts, or empty list if not found
        """
        if not sequence or not all(c in '01' for c in sequence):
            raise ValueError("Sequence must be a string of 0s and 1s (e.g., '10100010')")

        # Convert the bit sequence to bytes (pad to full bytes)
        # e.g., "1010001010000000" → 2 bytes
        num_bytes = (len(sequence) + 7) // 8
        # Pad with leading zeros if needed
        padded_sequence = sequence.zfill(num_bytes * 8)
        sequence_int = int(padded_sequence, 2)
        sequence_bytes = sequence_int.to_bytes(num_bytes, byteorder='big')

        # Also keep the original bit string for verification
        sequence_bits = sequence

        found_positions = []

        try:
            with open(filename, 'rb') as file:
                chunk_size = 65536  # 64 KB chunks
                buffer = b''
                buffer_bits = ""  # Keep track of bits for overlap

                while True:
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break

                    # Convert chunk to binary string (e.g., b'\x01\x02' → "0000000100000010")
                    chunk_bits = ''.join(format(byte, '08b') for byte in chunk)

                    # Combine with previous buffer's bits (for overlap)
                    combined_bits = buffer_bits + chunk_bits

                    # Search for the sequence in the combined bits
                    start = 0
                    while True:
                        pos = combined_bits.find(sequence_bits, start)
                        if pos == -1:
                            break
                        # Convert bit position to byte position
                        byte_pos = (len(buffer) * 8 + pos) // 8
                        found_positions.append(byte_pos)
                        start = pos + 1  # Continue searching for overlapping matches
                        preamble_found = True

                    # Update buffer for next iteration (keep enough for overlap)
                    buffer_bits = combined_bits[-(len(sequence_bits) - 1):]
                    buffer = chunk  # Keep full chunk for byte position calculation

            return found_positions

        except FileNotFoundError:
            print(f"Error: File '{filename}' not found.")
            return []
        except Exception as e:
            print(f"Error reading file: {e}")
            return []
    
    # def find_sequence_in_binary_file(filename, sequence="1010001010000000"):
    #     """ Check a binary file for the presence of a specific bit sequence.
        
    #     Args:
    #         filename (str): Path to the binary file to check
    #         sequence (str): The bit sequence to search for (default: "10011010")
        
    #     Returns:
    #         bool: True if sequence is found, False otherwise
    #     """
    #     global preamble_found

    #     # Convert sequence to bytes for comparison
    #     sequence_bytes = int(sequence, 2).to_bytes(1, byteorder='big')
        
    #     try:
    #         with open(filename, 'rb') as file:
    #             # Read the file in chunks to handle large files efficiently
    #             chunk_size = 1024
    #             buffer = b''
                
    #             while True:
    #                 chunk = file.read(chunk_size)
    #                 if not chunk:
    #                     break
                    
    #                 # Append new data to buffer
    #                 buffer += chunk
                    
    #                 # Check for the sequence in the buffer
    #                 # We need to check overlapping sequences
    #                 for i in range(len(buffer) - len(sequence) + 1):
    #                     # Extract a byte from the buffer
    #                     byte = buffer[i]
    #                     # Check if the byte matches the sequence
    #                     if byte == sequence_bytes[0]:
    #                         # Verify the exact bit pattern
    #                         # Convert byte to 8-bit binary string
    #                         byte_bits = format(byte, '08b')
    #                         # Check if the sequence matches the last 8 bits
    #                         if byte_bits == sequence:
    #                             return preamble_found == True
                    
    #                 # Keep only the overlapping part for next iteration
    #                 # This ensures we don't miss sequences that span across chunks
    #                 overlap = len(sequence) - 1
    #                 if len(buffer) > overlap:
    #                     buffer = buffer[-overlap:]
                
    #             return False
                
    #     except FileNotFoundError:
    #         print(f"Error: File '{filename}' not found.")
    #         return False
    #     except Exception as e:
    #         print(f"Error reading file: {e}")
    #         return False
    
    def work(self, input_items, output_items):
        """example: multiply with constant"""
        global preamble_found
        output_items[0][:] = input_items[0]
        
        # search for preamble 10011010
        if preamble_found:
            print("Sequence '10011010' found in the file!")
        # else:
        #     print("Sequence '10011010' not found in the file.")
        
        return len(output_items[0])
    

# if preamble found - save next 112 bits
# convert to hex
# save as a message

# import pyModeS

# global msg_hex

# def extract_adsb_message(bits, start_index, preamble_found,msg):
#     """
#     Extract the 112 bits immediately following a detected preamble.

#     Parameters:
#         bits (str): Binary string containing 0s and 1s.
#         start_index (int): Index immediately after the preamble.
#         preamble_found (bool): True if a valid preamble was detected.

#     Returns:
#         str or None: 112-bit ADS-B message, or None if unavailable.
#     """
#     if preamble_found and start_index + 112 <= len(bits):
#         message = bits[start_index:start_index + 112]
#         msg_hex = format(int(message, 2), "028X")
#         return msg_hex

#     return None

# result = pyModeS.decode(msg_hex)
# print(result)
# # {
# #     'df': 17,
# #     'icao': '406B90',
# #     'crc_valid': True,
# #     'typecode': 4,
# #     'bds': '0,8',
# #     'callsign': 'EZY85MH',
# #     'category': 0,
# #     'wake_vortex': 'No category information',
# # }






















    # Python code to convert binary number
    # into hexadecimal number

    # function to convert
    # binary to hexadecimal

    # def binToHexa(n):
    #     bnum = int(n)
    #     temp = 0
    #     mul = 1
        
    #     # counter to check group of 4
    #     count = 1
        
    #     # char array to store hexadecimal number
    #     hexaDeciNum = ['0'] * 100
        
    #     # counter for hexadecimal number array
    #     i = 0
    #     while bnum != 0:
    #         rem = bnum % 10
    #         temp = temp + (rem*mul)
            
    #         # check if group of 4 completed
    #         if count % 4 == 0:
            
    #             # check if temp < 10
    #             if temp < 10:
    #                 hexaDeciNum[i] = chr(temp+48)
    #             else:
    #                 hexaDeciNum[i] = chr(temp+55)
    #             mul = 1
    #             temp = 0
    #             count = 1
    #             i = i+1
                
    #         # group of 4 is not completed
    #         else:
    #             mul = mul*2
    #             count = count+1
    #         bnum = int(bnum/10)
            
    #     # check if at end the group of 4 is not
    #     # completed
    #     if count != 1:
    #         hexaDeciNum[i] = chr(temp+48)
            
    #     # check at end the group of 4 is completed
    #     if count == 1:
    #         i = i-1
            
    #     # printing hexadecimal number
    #     # array in reverse order
    #     print("\n Hexadecimal equivalent of {}:  ".format(n), end="")
    #     while i >= 0:
    #         print(end=hexaDeciNum[i])
    #         i = i-1
