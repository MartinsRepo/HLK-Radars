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
current_config = {
    "max_range": 20,
    "min_speed": 1, 
    "delay_time": 2,
    "snr_level": 4,
    "last_updated": "Not configured"
}

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

# Configuration status display
with ui.row().classes('w-full justify-between items-start mb-4'):
    # Left side - radar status
    with ui.column().classes('flex-1'):
        ui.label('📊 Radar Status').classes('text-lg font-semibold')
        radar_status = ui.label('🟢 Connected').classes('text-green-600')
    
    # Right side - current configuration parameters
    with ui.card().classes('p-3 bg-blue-50 border border-blue-200'):
        ui.label('⚙️ Current Configuration').classes('text-lg font-semibold mb-2')
        
        # Configuration parameters (these would be updated from config tool)
        config_display = ui.column().classes('gap-1')
        with config_display:
            range_label = ui.label('📏 Max Range: 20m').classes('text-sm')
            speed_label = ui.label('🚗 Min Speed: 1 km/h').classes('text-sm')
            delay_label = ui.label('⏱️ Delay Time: 2s').classes('text-sm')
            snr_label = ui.label('📡 SNR Level: 4').classes('text-sm font-bold text-green-600')
            
        ui.separator().classes('my-1')
        
        # Live target statistics
        with ui.column().classes('gap-1'):
            ui.label('📊 Live Statistics:').classes('text-xs font-semibold text-blue-800')
            target_count_label = ui.label('🎯 Targets: 0').classes('text-xs')
            avg_distance_label = ui.label('📏 Avg Distance: --').classes('text-xs')
            avg_snr_label = ui.label('📡 Avg SNR: --').classes('text-xs')
        
        ui.separator().classes('my-1')
        ui.label('💡 Configure via radar_config_gui.py').classes('text-xs text-gray-600')

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

def update_configuration_display():
    """Update the configuration display with current settings"""
    range_label.text = f'📏 Max Range: {current_config["max_range"]}m'
    speed_label.text = f'🚗 Min Speed: {current_config["min_speed"]} km/h'  
    delay_label.text = f'⏱️ Delay Time: {current_config["delay_time"]}s'
    snr_label.text = f'📡 SNR Level: {current_config["snr_level"]}'

def update_live_statistics():
    """Update live target statistics"""
    if not latest_targets:
        target_count_label.text = '🎯 Targets: 0'
        avg_distance_label.text = '📏 Avg Distance: --'
        avg_snr_label.text = '📡 Avg SNR: --'
        return
    
    count = len(latest_targets)
    avg_dist = sum(t['distance'] for t in latest_targets) / count
    avg_snr = sum(t['snr'] for t in latest_targets) / count
    
    target_count_label.text = f'🎯 Targets: {count}'
    avg_distance_label.text = f'📏 Avg Distance: {avg_dist:.1f}m'
    avg_snr_label.text = f'📡 Avg SNR: {avg_snr:.1f}'

def update_plot():
    if not latest_targets:
        plot.figure.data[0].r = []
        plot.figure.data[0].theta = []
        # clear log when no targets
        log_label.text = 'Waiting for radar data...'
        radar_status.text = '🟡 No Targets'
        update_live_statistics()
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
    
    # Update status with target count
    radar_status.text = f'🟢 Connected - {len(latest_targets)} target(s)'
    
    # Update live statistics
    update_live_statistics()
    
    # update textual log area with a compact summary
    try:
        lines = []
        for i, t in enumerate(latest_targets, start=1):
            lines.append(f"Target {i}: Angle={t['angle']}°  Dist={t['distance']}m  Dir={t['direction']}  Speed={t['speed']}km/h  SNR={t['snr']}")
        log_label.text = "\n".join(lines)
    except Exception:
        # avoid crashing UI updates if formatting fails
        pass

# Update configuration display initially
update_configuration_display()

# Add manual configuration update controls
with ui.row().classes('mt-4 gap-4'):
    ui.label('🔧 Quick Config Update:').classes('font-semibold')
    
    with ui.row().classes('gap-2'):
        snr_input = ui.number('SNR Level', value=current_config["snr_level"], min=3, max=8, step=1).classes('w-24')
        range_input = ui.number('Max Range (m)', value=current_config["max_range"], min=10, max=100, step=5).classes('w-24')
        
        def update_config():
            current_config["snr_level"] = int(snr_input.value)
            current_config["max_range"] = int(range_input.value)
            current_config["last_updated"] = time.strftime("%H:%M:%S")
            update_configuration_display()
            ui.notify(f'Configuration updated: SNR={snr_input.value}, Range={range_input.value}m', type='positive')
        
        ui.button('Update Display', on_click=update_config, color='primary').classes('h-8')
    
    ui.label('💡 Tip: Use radar_config_gui.py to actually configure the radar device').classes('text-sm text-gray-600')

ui.timer(0.2, update_plot)  # 5 updates per second

# Start background thread
threading.Thread(target=radar_reader, daemon=True).start()

ui.run(title='HLK-LD2451 Radar Viewer', reload=False)
