import serial
import struct
import time
import threading
from nicegui import ui, run
import plotly.graph_objects as go

PORT = "/dev/ttyUSB0"
BAUD = 115200
FRAME_HEADER = b"\xF4\xF3\xF2\xF1"
FRAME_TAIL = b"\xF8\xF7\xF6\xF5"
END_CONFIG = bytes.fromhex("FD FC FB FA 02 00 FE 00 04 03 02 01")

latest_targets = []
lock = threading.Lock()

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
        direction = "approaching" if direction_flag == 0 else "away"
        targets.append({
            "angle": angle,
            "distance": distance_raw,
            "direction": direction,
            "speed": speed_value,
            "snr": snr,
        })
        offset += 5
    return targets

def send_command(ser, cmd):
    ser.write(cmd)
    time.sleep(0.1)
    ser.read_all()

# === GUI components ===
ui.label('📡 HLK-LD2451 Live Radar').classes('text-xl font-bold mb-2')

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

# scrolling output area
with ui.column().classes('w-full h-[250px] overflow-y-auto bg-black text-white rounded p-2 font-mono') as log_box:
    log_label = ui.label('Waiting for radar data...').classes('text-sm whitespace-pre')

def update_plot(targets):
    """Safe UI update from radar thread."""
    if not targets:
        return
    r = [t['distance'] for t in targets]
    theta = [t['angle'] for t in targets]
    colors = ['red' if t['direction'] == 'away' else 'lime' for t in targets]
    plot.figure.data[0].r = r
    plot.figure.data[0].theta = theta
    plot.figure.data[0].marker.color = colors
    plot.figure.data[0].marker.size = [10] * len(r)
    plot.update()

def append_log(text):
    """Append new lines to the UI log box safely from any thread."""
    async def _update():
        current = log_label.text or ""
        new_text = (current + "\n" + text).strip()
        log_label.text = new_text
        await run.jscript("document.querySelector('.overflow-y-auto').scrollTop = 999999;")
    ui.run_in_main_thread(_update)

def radar_reader():
    """Background thread reading radar frames and pushing updates to the UI."""
    with serial.Serial(PORT, BAUD, timeout=0.5) as ser:
        send_command(ser, END_CONFIG)
        buffer = bytearray()
        time.sleep(0.5)
        while True:
            try:
                data = ser.read(128)
                buffer.extend(data)
            except:
                continue

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
                t1 = targets[0]
                text_block = (
                    f"✅ Successfully read Target data (Length: {len(frame)} bytes)\n"
                    f"Target 1 Angle: {t1['angle']} deg\n"
                    f"Target 1 Distance: {t1['distance']} m\n"
                    f"Target 1 Speed: {t1['speed']} km/h ({t1['direction']})\n"
                    f"Target 1 SNR: {t1['snr']}\n"
                )
                print(text_block)
                ui.run_in_main_thread(lambda: update_plot(targets))
                append_log(text_block)

            buffer = buffer[start_idx + full_len:]

threading.Thread(target=radar_reader, daemon=True).start()

ui.run(title='HLK-LD2451 Radar Viewer', reload=False)


