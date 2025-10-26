import serial
import struct
import time
from colorama import Fore, Style, init

# === Radar serial configuration ===
PORT = "/dev/ttyUSB0"
BAUD = 115200

# === Frame markers ===
FRAME_HEADER = b"\xF4\xF3\xF2\xF1"
FRAME_TAIL   = b"\xF8\xF7\xF6\xF5"
END_CONFIG   = bytes.fromhex("FD FC FB FA 02 00 FE 00 04 03 02 01")

init(autoreset=True) # reset colors

def find_frame_start(buffer):
    """Find start index of frame header in buffer."""
    idx = buffer.find(FRAME_HEADER)
    return idx if idx != -1 else None

def parse_target_data(frame_data):
    """Parse radar target data according to protocol."""
    # Example layout (after length field):
    # [target_count][alarm][targets...]
    if len(frame_data) < 2:
        return None

    target_count = frame_data[0]
    alarm = frame_data[1]
    targets = []

    offset = 2
    for _ in range(target_count):
        if offset + 5 > len(frame_data):
            break
        angle_raw = frame_data[offset]
        distance_raw = frame_data[offset + 1]
        direction_flag = frame_data[offset + 2]
        speed_value = frame_data[offset + 3]
        snr = frame_data[offset + 4]

        angle = angle_raw - 0x80  # signed offset
        distance = distance_raw   # meters
        direction = "approaching" if direction_flag == 0 else "away"
        speed = speed_value       # km/h

        targets.append({
            "angle": angle,
            "distance": distance,
            "direction": direction,
            "speed": speed,
            "snr": snr,
        })
        offset += 5

    return {
        "alarm": alarm,
        "targets": targets
    }
    
def send_command(ser, cmd, label="CMD"):
    ser.write(cmd)
    time.sleep(0.1)
    response = ser.read_all()
    print(f"{label} -> {response.hex(' ')}")
    return response

def read_radar(port):
    """Continuously read frames and print target data."""
    buffer = bytearray()
    
    ser.reset_input_buffer()
    buffer.clear()
    
    send_command(ser, END_CONFIG, "End Config (start measuring)")

    while True:
        data = port.read(128)
        print(data)
        if not data:
            continue
        buffer.extend(data)

        # Look for frame start
        start_idx = find_frame_start(buffer)
        if start_idx is None:
            # keep buffer size reasonable
            if len(buffer) > 1024:
                buffer.clear()
            continue

        if len(buffer) < start_idx + 6:
            continue

        # Extract length (2 bytes after header, little-endian)
        length_bytes = buffer[start_idx + 4:start_idx + 6]
        frame_length = struct.unpack("<H", length_bytes)[0]

        full_len = 4 + 2 + frame_length + 4  # header + len + data + tail
        if len(buffer) < start_idx + full_len:
            continue  # wait for more data

        frame = buffer[start_idx:start_idx + full_len]
        if not frame.endswith(FRAME_TAIL):
            print(Fore.RED + "[WARN] Invalid frame tail, resyncing...")
            buffer = buffer[start_idx + 1:]
            continue

        inner_data = frame[6:-4]
        print(Fore.CYAN + f"[DEBUG] Frame length: {frame_length}, Data: {inner_data.hex(' ')}")

        if frame_length < 2 or len(inner_data) < 2:
            print(Fore.YELLOW + "[INFO] Empty or status frame received (no targets).")
            buffer = buffer[start_idx + full_len:]
            continue

        result = parse_target_data(inner_data)

        if result and result["targets"]:
            t1 = result["targets"][0]
            print(Fore.GREEN + f"\n✅ Successfully read Target data (Length: {len(frame)} bytes)")
            print(Fore.WHITE + f"Target 1 Angle: {t1['angle']} deg")
            print(Fore.WHITE + f"Target 1 Distance: {t1['distance']} m")
            print(Fore.WHITE + f"Target 1 Speed: {t1['speed']} km/h ({t1['direction']})")
            print(Fore.WHITE + f"Target 1 SNR: {t1['snr']}\n")
        else:
            print(Fore.YELLOW + "[INFO] Frame parsed but contained no valid targets.")

        buffer = buffer[start_idx + full_len:]


if __name__ == "__main__":
    try:
        with serial.Serial(PORT, BAUD, timeout=5.0) as ser:
            print(f"Listening on {PORT} @ {BAUD} baud ...")
            time.sleep(1)
            read_radar(ser)
    except serial.SerialException as e:
        print(f"Serial error: {e}")

