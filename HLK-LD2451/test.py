import serial
import time

PORT = "/dev/ttyUSB0"
BAUD = 115200

FRAME_HEADER = b"\xF4\xF3\xF2\xF1"
FRAME_TAIL   = b"\xF8\xF7\xF6\xF5"

# Commands (from protocol)
ENABLE_CONFIG      = bytes.fromhex("FD FC FB FA 04 00 FF 00 01 00 04 03 02 01")
END_CONFIG          = bytes.fromhex("FD FC FB FA 02 00 FE 00 04 03 02 01")
READ_FIRMWARE      = bytes.fromhex("FD FC FB FA 02 00 A0 00 04 03 02 01")

def send_command(ser, cmd, label="CMD"):
    ser.write(cmd)
    time.sleep(0.1)
    response = ser.read_all()
    print(f"{label} -> {response.hex(' ')}")
    return response

def read_frames(ser):
    buffer = bytearray()
    while True:
        data = ser.read(64)
        if data:
            buffer.extend(data)
            print(f"RAW: {data}")
        # Try to parse complete frames
        while True:
            start = buffer.find(FRAME_HEADER)
            if start == -1 or len(buffer) < start + 10:
                break
            length = int.from_bytes(buffer[start+4:start+6], "little")
            total_len = 4 + 2 + length + 4
            if len(buffer) < start + total_len:
                break
            frame = buffer[start:start + total_len]
            print(f"Frame ({len(frame)} bytes): {frame.hex(' ')}")
            buffer = buffer[start + total_len:]
        time.sleep(0.05)

if __name__ == "__main__":
    with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
        print(f"Connected to {PORT} @ {BAUD} baud")
        time.sleep(1)

        # Optional: enable config, read version, then end config
        send_command(ser, ENABLE_CONFIG, "Enable Config")
        send_command(ser, READ_FIRMWARE, "Read Firmware")
        send_command(ser, END_CONFIG, "End Config (start measuring)")

        print("\nNow listening for radar data...\n")
        read_frames(ser)

