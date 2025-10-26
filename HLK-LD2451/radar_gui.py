import serial
import struct
import time
import threading
from nicegui import ui
import math
import plotly.graph_objects as go

# === Radar serial configuration ===
PORT = "/dev/ttyUSB0"
BAUD = 115200

# === Frame markers ===
FRAME_HEADER = b"\xF4\xF3\xF2\xF1"
FRAME_TAIL   = b"\xF8\xF7\xF6\xF5"
END_CONFIG   = bytes.fromhex("FD FC FB FA 02 00 FE 00 04 03 02 01")

# Shared data
latest_targets = []

def find_frame_start(buffer):
    idx = buffer.find(FRAME_HEADER)
    return idx if idx != -1 else None

def parse_target_data(frame_data):
    if len(frame_data) < 2:
        return []
    count = frame_data[0]
    targets = []
    offset = 2
    for _ in range(count):
        if offset + 5 > len(frame_data):
            break
        angle_raw = frame_data[offset]
        distance_raw = frame_data[offset + 1]
        direction_flag = frame_data[offset + 2]
        speed_value = frame_data[offset + 3]
        snr = frame_data[offset + 4]

        angle = angle_raw - 0x80
        distance = distance_raw
        direction = "approaching" if direction_flag == 0 else "away"
        speed = speed_value
        targets.append({
            "angle": angle,
            "distance": distance,
            "direction": direction,
            "speed": speed,
            "snr": snr,
        })
        offset += 5
    return targets

def send_command(ser, cmd):
    ser.write(cmd)
    time.sleep(0.1)
    ser.read_all()

def radar_reader():
    """Thread: read radar serial data continuously and update latest_targets."""
    global latest_targets
    with serial.Serial(PORT, BAUD, timeout=0.5) as ser:
        send_command(ser, END_CONFIG)
        buffer = bytearray()
        time.sleep(0.5)
        while True:
            try:
                data = ser.read(128)
                buffer.extend(data)
            except:
                pass

            start_idx = find_frame_start(buffer)
            if start_idx is None or len(buffer) < start_idx + 6:
                continue
            frame_length = struct.unpack("<H", buffer[start_idx + 4:start_idx + 6])[0]
            full_len = 4 + 2 + frame_length + 4
            if len(buffer) < start_idx + full_len:
                continue
            frame = buffer[start_idx:start_idx + full_len]
            if not frame.endswith(FRAME_TAIL):
                buffer = buffer[start_idx + 1:]
                continue
            inner_data = frame[6:-4]
            if frame_length < 2 or len(inner_data) < 2:
                buffer = buffer[start_idx + full_len:]
                continue
            targets = parse_target_data(inner_data)
            if targets:
                latest_targets = targets
            buffer = buffer[start_idx + full_len:]

# === GUI ===
ui.label('📡 HLK-LD2451 Live Radar').classes('text-xl font-bold mb-2')

# Initial empty figure
fig = go.Figure()
fig.update_layout(
    polar=dict(
        radialaxis=dict(range=[0, 100], showticklabels=True, ticks='outside'),
        angularaxis=dict(direction="clockwise", rotation=90, tickmode='linear', dtick=30),
    ),
    showlegend=False,
    margin=dict(l=10, r=10, t=10, b=10),
)
fig.add_trace(go.Scatterpolar(r=[], theta=[], mode='markers', marker=dict(size=10, color='lime')))

plot = ui.plotly(fig).classes('w-full h-[600px]')

# scrolling output area (text window)
with ui.column().classes('w-full h-[250px] overflow-y-auto bg-black text-white rounded p-2 font-mono') as log_box:
    log_label = ui.label('Waiting for radar data...').classes('text-sm whitespace-pre')

def update_plot():
    if not latest_targets:
        plot.figure.data[0].r = []
        plot.figure.data[0].theta = []
        # clear log when no targets
        log_label.text = 'Waiting for radar data...'
        plot.update()
        return

    r = []
    theta = []
    colors = []
    sizes = []
    for t in latest_targets:
        r.append(t['distance'])
        theta.append(t['angle'])
        # color by direction, size by SNR
        color = 'red' if t['direction'] == 'away' else 'lime'
        colors.append(color)
        sizes.append(max(6, min(16, t['snr'] / 10)))
    plot.figure.data[0].r = r
    plot.figure.data[0].theta = theta
    plot.figure.data[0].marker.color = colors
    plot.figure.data[0].marker.size = sizes
    plot.update()
    # update textual log area with a compact summary
    try:
        lines = []
        for i, t in enumerate(latest_targets, start=1):
            lines.append(f"Target {i}: Angle={t['angle']}°  Dist={t['distance']}  Dir={t['direction']}  Speed={t['speed']}  SNR={t['snr']}")
        log_label.text = "\n".join(lines)
    except Exception:
        # avoid crashing UI updates if formatting fails
        pass

ui.timer(0.2, update_plot)  # 5 updates per second

# Start background thread
threading.Thread(target=radar_reader, daemon=True).start()

ui.run(title='HLK-LD2451 Radar Viewer', reload=False)
