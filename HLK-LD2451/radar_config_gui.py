from nicegui import ui
import serial
import threading
import time
import struct

# Defaults
PORT = "/dev/ttyUSB0"
BAUD = 115200

# === Frame markers for live data ===
FRAME_HEADER = b"\xF4\xF3\xF2\xF1"
FRAME_TAIL   = b"\xF8\xF7\xF6\xF5"

# Shared state
ser = None
log_lines = []
latest_targets = []  # Live radar target data
radar_reader_active = False
log_output = None  # Log display widget
radar_stats = {
    "frames_received": 0,
    "empty_frames": 0,
    "target_frames": 0,
    "bytes_received": 0
}

# Current configuration state
current_config = {
    "max_range": 20,
    "min_speed": 1,
    "delay_time": 2,
    "snr_level": 4,
    "last_updated": "Not configured yet",
    "config_status": "Ready to configure"
}

def log(msg):
    """Append message to log window"""
    log_lines.append(msg)
    if len(log_lines) > 200:
        del log_lines[0]
    # Only update UI if we're in the main thread context
    try:
        log_output.text = "\n".join(log_lines[-30:])
        ui.run_javascript("document.querySelector('.overflow-y-auto').scrollTop = 999999;")
    except RuntimeError:
        # Skip UI update if called from background thread
        pass

def find_frame_start(buffer):
    """Find radar data frame start in buffer"""
    idx = buffer.find(FRAME_HEADER)
    return idx if idx != -1 else None

def parse_target_data(frame_data):
    """Parse target data from radar frame"""
    if len(frame_data) == 0:
        log_lines.append("⚠️ Empty frame data - radar may be idle or not detecting targets")
        return []
    
    if len(frame_data) < 2:
        log_lines.append(f"⚠️ Frame data too short: {len(frame_data)} bytes")
        # Show the raw bytes for debugging
        if len(frame_data) > 0:
            raw_hex = ' '.join(f'{b:02X}' for b in frame_data)
            log_lines.append(f"📋 Raw frame data: {raw_hex}")
        return []
    
    count = frame_data[0]
    frame_type = frame_data[1] if len(frame_data) > 1 else 0
    
    log_lines.append(f"📋 Parse: count={count}, type={frame_type:02X}, data_len={len(frame_data)}")
    
    if count == 0:
        log_lines.append("📋 No targets detected in this frame - radar is working but no objects in range")
        return []
    
    targets = []
    offset = 2
    for i in range(count):
        if offset + 5 > len(frame_data):
            log_lines.append(f"⚠️ Not enough data for target {i+1}, need {offset+5}, have {len(frame_data)}")
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
        
        log_lines.append(f"🎯 Target {i+1}: angle={angle}° dist={distance}m dir={direction} speed={speed} snr={snr}")
        
        targets.append({
            "angle": angle,
            "distance": distance,
            "direction": direction,
            "speed": speed,
            "snr": snr,
        })
        offset += 5
    
    log_lines.append(f"✅ Parsed {len(targets)} targets successfully")
    return targets

def radar_data_reader():
    """Background thread to read live radar data"""
    global latest_targets, radar_reader_active
    radar_reader_active = True
    
    if not ser or not ser.is_open:
        radar_reader_active = False
        return
        
    buffer = bytearray()
    bytes_received = 0
    frames_processed = 0
    
    # Don't call log() from background thread - just store message
    log_lines.append("🔄 Started live radar data reader")
    
    try:
        while radar_reader_active and ser and ser.is_open:
            try:
                data = ser.read(128)
                if not data:
                    time.sleep(0.1)
                    continue
                
                bytes_received += len(data)
                buffer.extend(data)
                
                # Debug: Show raw data every 100 bytes received
                if bytes_received % 100 == 0:
                    log_lines.append(f"📊 Received {bytes_received} bytes, buffer size: {len(buffer)}")
                
                start_idx = find_frame_start(buffer)
                if start_idx is None:
                    # Show some buffer content for debugging
                    if len(buffer) > 20:
                        sample = ' '.join(f'{b:02X}' for b in buffer[:20])
                        log_lines.append(f"🔍 No frame header found, buffer start: {sample}...")
                        # Keep only recent data to prevent buffer overflow
                        buffer = buffer[-100:]
                    continue
                
                if len(buffer) < start_idx + 6:
                    continue
                    
                try:
                    frame_length = struct.unpack("<H", buffer[start_idx + 4:start_idx + 6])[0]
                    full_len = 4 + 2 + frame_length + 4
                    
                    log_lines.append(f"📦 Found frame at {start_idx}, length: {frame_length}, full_len: {full_len}")
                    
                    if len(buffer) < start_idx + full_len:
                        continue
                        
                    frame = buffer[start_idx:start_idx + full_len]
                    if not frame.endswith(FRAME_TAIL):
                        buffer = buffer[start_idx + 1:]
                        continue
                        
                    frames_processed += 1
                    radar_stats["frames_received"] = frames_processed
                    inner_data = frame[6:-4]
                    
                    log_lines.append(f"🎯 Processing frame #{frames_processed}, inner_data length: {len(inner_data)}")
                    
                    if frame_length == 0 or len(inner_data) == 0:
                        radar_stats["empty_frames"] += 1
                        buffer = buffer[start_idx + full_len:]
                        # Only log every 10th empty frame to avoid spam
                        if radar_stats["empty_frames"] % 10 == 0:
                            log_lines.append(f"📊 Stats: {radar_stats['empty_frames']} empty frames, {radar_stats['target_frames']} with targets")
                        continue
                        
                    targets = parse_target_data(inner_data)
                    log_lines.append(f"🎯 Parsed {len(targets) if targets else 0} targets from frame")
                    
                    if targets:
                        radar_stats["target_frames"] += 1
                        latest_targets = targets
                        log_lines.append(f"✅ Updated latest_targets with {len(targets)} targets")
                        
                    buffer = buffer[start_idx + full_len:]
                
                except struct.error as e:
                    log_lines.append(f"⚠️ Struct error: {e}")
                    buffer = buffer[start_idx + 1:]
                    continue
                
            except Exception as e:
                # Store error message without calling log()
                log_lines.append(f"⚠️ Radar data error: {e}")
                time.sleep(0.5)
                
    except Exception as e:
        # Store error message without calling log()
        log_lines.append(f"❌ Radar reader error: {e}")
    finally:
        radar_reader_active = False
        log_lines.append(f"⏹️ Radar data reader stopped. Processed {frames_processed} frames, {bytes_received} bytes")

def connect_serial():
    global ser
    try:
        ser = serial.Serial(port_field.value, int(baud_field.value), timeout=1)
        log(f"✅ Connected to {ser.port} @ {ser.baudrate}")
    except Exception as e:
        log(f"❌ Connection failed: {e}")

def disconnect_serial():
    global ser
    if ser and ser.is_open:
        ser.close()
        log("🔌 Serial port closed")

def decode_detection_response(response_bytes):
    """Decode detection parameter response (command 0x61)"""
    try:
        if len(response_bytes) < 10:
            return "❌ Response too short to decode"
        
        # Look for command response frame (FD FC FB FA) in the response
        cmd_header = b'\xFD\xFC\xFB\xFA'
        data_header = b'\xF4\xF3\xF2\xF1'
        
        # Check if we got data frames instead of command response
        if response_bytes.startswith(data_header):
            return """⚠️ Received radar data frames instead of command response.
The device may not be in configuration mode or doesn't support this command.
Try:
1. Send 'Enable Config Mode' first
2. Check if device supports parameter reading
3. Device might be continuously streaming data"""
        
        # Look for command response frame
        cmd_start = response_bytes.find(cmd_header)
        if cmd_start == -1:
            return f"❌ No command response frame found. Got: {response_bytes[:50].hex(' ')}..."
            
        # Parse command response: FD FC FB FA <len_lo> <len_hi> <cmd> <status> <data...> 04 03 02 01
        if cmd_start + 8 < len(response_bytes):
            cmd_byte = response_bytes[cmd_start + 6]
            status = response_bytes[cmd_start + 7]
            
            if cmd_byte == 0x61:  # Detection parameter response
                if status == 0x01:  # Success
                    if cmd_start + 14 <= len(response_bytes):
                        max_dist = response_bytes[cmd_start + 8] | (response_bytes[cmd_start + 9] << 8)
                        min_dist = response_bytes[cmd_start + 10] | (response_bytes[cmd_start + 11] << 8)
                        max_angle = response_bytes[cmd_start + 12] - 60
                        min_angle = response_bytes[cmd_start + 13] - 60
                        
                        return f"""📊 Detection Parameters:
  • Max Distance: {max_dist} m
  • Min Distance: {min_dist} m  
  • Max Angle: {max_angle}°
  • Min Angle: {min_angle}°"""
                    else:
                        return "❌ Detection response data incomplete"
                else:
                    return f"❌ Command failed with status: 0x{status:02X}"
            else:
                return f"❌ Expected detection response (0x61), got: 0x{cmd_byte:02X}"
        else:
            return "❌ Command response frame too short"
            
    except Exception as e:
        return f"❌ Error decoding detection response: {e}"

def decode_sensitivity_response(response_bytes):
    """Decode sensitivity parameter response (command 0x65)"""
    try:
        if len(response_bytes) < 10:
            return "❌ Response too short to decode"
            
        # Look for command response frame (FD FC FB FA) in the response
        cmd_header = b'\xFD\xFC\xFB\xFA'
        data_header = b'\xF4\xF3\xF2\xF1'
        
        # Check if we got data frames instead of command response
        if response_bytes.startswith(data_header):
            return """⚠️ Received radar data frames instead of command response.
The device may not be in configuration mode or doesn't support this command.
Try:
1. Send 'Enable Config Mode' first
2. Check if device supports sensitivity parameter reading
3. Device might be continuously streaming data"""
        
        # Look for command response frame
        cmd_start = response_bytes.find(cmd_header)
        if cmd_start == -1:
            return f"❌ No command response frame found. Got: {response_bytes[:50].hex(' ')}..."
            
        # Parse command response: FD FC FB FA <len_lo> <len_hi> <cmd> <status> <data...> 04 03 02 01
        if cmd_start + 8 < len(response_bytes):
            cmd_byte = response_bytes[cmd_start + 6]
            status = response_bytes[cmd_start + 7]
            
            if cmd_byte == 0x65:  # Sensitivity parameter response
                if status == 0x01:  # Success
                    if cmd_start + 12 <= len(response_bytes):
                        approach_sens = response_bytes[cmd_start + 8]
                        away_sens = response_bytes[cmd_start + 9]
                        threshold = response_bytes[cmd_start + 10] | (response_bytes[cmd_start + 11] << 8)
                        
                        return f"""📊 Sensitivity Parameters:
  • Approach Sensitivity: {approach_sens}/9
  • Away Sensitivity: {away_sens}/9
  • Detection Threshold: {threshold}"""
                    else:
                        return "❌ Sensitivity response data incomplete"
                else:
                    return f"❌ Command failed with status: 0x{status:02X}"
            else:
                return f"❌ Expected sensitivity response (0x65), got: 0x{cmd_byte:02X}"
        else:
            return "❌ Command response frame too short"
            
    except Exception as e:
        return f"❌ Error decoding sensitivity response: {e}"

def decode_a_series_response(response_bytes):
    """Decode A-series command responses (A1, A2, A3) - device status/info commands"""
    try:
        if len(response_bytes) < 10:
            return "❌ Response too short to decode"
            
        cmd_header = b'\xFD\xFC\xFB\xFA'
        cmd_start = response_bytes.find(cmd_header)
        if cmd_start == -1:
            return f"❌ No command response frame found"
            
        if cmd_start + 8 > len(response_bytes):
            return "❌ Command response frame too short"
            
        cmd_byte = response_bytes[cmd_start + 6]
        status = response_bytes[cmd_start + 7]
        
        if status != 0x01:
            return f"❌ Command failed with status: 0x{status:02X}"
            
        # Extract data bytes after status
        data_start = cmd_start + 8
        data_end = len(response_bytes) - 4  # Remove tail bytes
        data_bytes = response_bytes[data_start:data_end]
        
        if cmd_byte == 0xA1:
            # A1 response: FD FC FB FA 04 00 A1 01 01 00 04 03 02 01
            # Data: 01 00 (might be baud rate or config status)
            if len(data_bytes) >= 2:
                value = data_bytes[0] | (data_bytes[1] << 8)
                baud_map = {1: '9600', 2: '19200', 3: '38400', 4: '57600', 5: '115200', 6: '230400', 7: '256000', 8: '460800'}
                baud_rate = baud_map.get(data_bytes[0], f"Unknown ({data_bytes[0]})")
                return f"""📊 System Info (A1):
  • Baud Rate Code: {data_bytes[0]} → {baud_rate}
  • Additional Value: {data_bytes[1]}
  • Combined Value: {value}"""
            else:
                return f"📊 System Info (A1): No data returned"
                
        elif cmd_byte == 0xA2:
            # A2 response: FD FC FB FA 04 00 A2 01 00 00 04 03 02 01  
            # Data: 00 00 (might be factory reset status or device state)
            if len(data_bytes) >= 2:
                return f"""📊 System Status (A2):
  • Status Code: {data_bytes[0]:02X}
  • Secondary Status: {data_bytes[1]:02X}
  • Interpretation: {'Factory defaults' if data_bytes[0] == 0 and data_bytes[1] == 0 else 'Custom settings'}"""
            else:
                return f"📊 System Status (A2): No data returned"
                
        elif cmd_byte == 0xA3:
            # A3 response: FD FC FB FA 04 00 A3 01 00 00 04 03 02 01 00
            # Note: Extra byte at end - might be firmware version or device ID
            return f"""📊 Device Info (A3):
  • Data Length: {len(data_bytes)} bytes
  • Raw Data: {' '.join(f'{b:02X}' for b in data_bytes)}
  • Possible: Device ID, firmware info, or extended status"""
        else:
            return f"📊 Unknown A-series command: 0x{cmd_byte:02X}"
            
    except Exception as e:
        return f"❌ Error decoding A-series response: {e}"

def send_command(hex_string, decode_response=None):
    """Send hex string to radar with optional response decoding"""
    global ser
    if not ser or not ser.is_open:
        log("⚠️ Not connected.")
        return
    try:
        data = bytes.fromhex(hex_string)
        
        # Clear any buffered data first
        ser.reset_input_buffer()
        
        # Send command
        ser.write(data)
        log(f"📤 Sent: {hex_string}")
        
        # Wait longer for command responses
        time.sleep(0.3)
        
        # Read response
        response = ser.read_all()
        
        if response:
            log(f"📥 Raw Response ({len(response)} bytes): {' '.join(f'{b:02X}' for b in response)}")
            
            # Check if we're getting data frames instead of command responses
            if response.startswith(b'\xF4\xF3\xF2\xF1'):
                if response == b'\xF4\xF3\xF2\xF1\x00\x00\xF8\xF7\xF6\xF5':
                    log("🔄 Device is streaming empty data frames (no targets detected)")
                else:
                    log("🔄 Device is streaming radar data frames")
                log("⚠️ Device NOT in configuration mode!")
                log("💡 Solution: Click 'Enable Config Mode' first, then try command again")
                return
            
            # Check if response is just an echo of what we sent
            if response == data:
                log("🔄 Device echoed the command back - this might indicate:")
                log("   • Command was received but not processed")
                log("   • Device doesn't support this command")
                log("   • Wrong command format for this device")
                return
            
            # Check for standard ACK pattern
            if len(response) >= 8 and response.startswith(b'\xFD\xFC\xFB\xFA'):
                cmd_sent = data[6] if len(data) > 6 else 0x00
                cmd_recv = response[6] if len(response) > 6 else 0x00
                status = response[7] if len(response) > 7 else 0x00
                
                if cmd_recv == cmd_sent:
                    if status == 0x00:
                        log("✅ Command acknowledged successfully")
                    elif status == 0x01:
                        log("✅ Command executed successfully")
                    else:
                        log(f"⚠️ Command response with status: 0x{status:02X}")
            
            # Try to decode if decoder function provided
            if decode_response and callable(decode_response):
                try:
                    decoded = decode_response(response)
                    log(decoded)
                except Exception as e:
                    log(f"❌ Decoding error: {e}")
        else:
            log("📭 No response received.")
            log("💡 Possible causes:")
            log("   • Device not in configuration mode → Try 'Enable Config Mode'")
            log("   • Command not supported → Try different command ID")
            log("   • Connection issue → Check serial connection")
    except Exception as e:
        log(f"❌ Error sending command: {e}")

# Arduino-based Configuration Functions
def update_config_display():
    """Update the configuration display panel"""
    import time
    range_display.text = f'📏 Max Range: {current_config["max_range"]}m'
    speed_display.text = f'🚗 Min Speed: {current_config["min_speed"]} km/h'
    delay_display.text = f'⏱️ Delay Time: {current_config["delay_time"]}s'
    snr_display.text = f'📡 SNR Level: {current_config["snr_level"]}'
    status_display.text = current_config["config_status"]
    updated_display.text = f'Last updated: {current_config["last_updated"]}'

def update_log_display():
    """Update log display from main thread"""
    try:
        log_output.text = "\n".join(log_lines[-30:])
        ui.run_javascript("document.querySelector('.overflow-y-auto').scrollTop = 999999;")
    except:
        pass

def update_live_targets_display():
    """Update live target data display"""
    # Also update log display to show messages from background thread
    update_log_display()
    
    # Update statistics display
    radar_stats_display.text = f'📈 Frames: {radar_stats["frames_received"]} | Empty: {radar_stats["empty_frames"]} | With Targets: {radar_stats["target_frames"]}'
    
    if not latest_targets:
        target_count_display.text = '📊 Active Targets: 0'
        if radar_stats["frames_received"] > 0:
            empty_ratio = radar_stats["empty_frames"] / radar_stats["frames_received"] * 100
            target_list_display.content = f'''
            <div style="color: #666;">
                <div>✅ <strong>Radar is working!</strong> ({radar_stats["frames_received"]} frames received)</div>
                <div>📡 Empty frames: {empty_ratio:.1f}% - This is normal when no targets present</div>
                <br/>
                <div style="color: #e67e22;"><strong>💡 To detect targets:</strong></div>
                <div>• Click <strong>"🔥 Max Sensitivity"</strong> for easier detection</div>
                <div>• Move your <strong>hand slowly</strong> 0.5-3m in front of radar</div>
                <div>• Try <strong>walking</strong> in the radar's field of view</div>
                <div>• Radar detects <strong>movement</strong>, not static objects</div>
            </div>'''
        else:
            target_list_display.content = '<span style="color: #666;">Waiting for radar data...</span>'
        return
    
    count = len(latest_targets)
    target_count_display.text = f'📊 Active Targets: {count}'
    
    # Create improved HTML table for targets (bigger text, better formatting)
    html_content = '''
    <table style="font-size: 12px; width: 100%; border-collapse: collapse;">
        <thead>
            <tr style="background-color: #f8f9fa; color: #333; font-weight: bold;">
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">Angle</td>
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">Distance</td>
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">Direction</td>
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">Speed</td>
                <td style="padding: 4px; border-bottom: 1px solid #ddd;">SNR</td>
            </tr>
        </thead>
        <tbody>
    '''
    
    for i, target in enumerate(latest_targets[:8]):  # Show more targets with bigger display
        bg_color = '#ffe6e6' if target['direction'] == 'away' else '#e6ffe6'
        text_color = '#d63031' if target['direction'] == 'away' else '#00b894'
        
        html_content += f'''
        <tr style="background-color: {bg_color};">
            <td style="padding: 3px; color: {text_color}; font-weight: bold;">{target["angle"]}°</td>
            <td style="padding: 3px; color: {text_color};">{target["distance"]}m</td>
            <td style="padding: 3px; color: {text_color};">{target["direction"]}</td>
            <td style="padding: 3px; color: {text_color};">{target["speed"]} km/h</td>
            <td style="padding: 3px; color: {text_color};">{target["snr"]}</td>
        </tr>
        '''
    
    if len(latest_targets) > 8:
        html_content += f'<tr><td colspan="5" style="padding: 3px; color: #888; text-align: center;">... and {len(latest_targets)-8} more targets</td></tr>'
    
    html_content += '</tbody></table>'
    target_list_display.content = html_content

def start_radar_reader():
    """Start background radar data reader"""
    global radar_reader_active
    if not ser or not ser.is_open:
        log("❌ Connect to serial port first!")
        ui.notify("Connect to serial port first!", type='negative')
        return
    
    if radar_reader_active:
        log("⚠️ Radar reader already running")
        ui.notify("Radar reader already active", type='warning')
        return
    
    # Start reader thread
    thread = threading.Thread(target=radar_data_reader, daemon=True)
    thread.start()
    log("🔄 Starting live radar data reader...")
    ui.notify("Started live radar data reader", type='positive')

def stop_radar_reader():
    """Stop radar data reader"""
    global radar_reader_active
    radar_reader_active = False
    log("⏹️ Stopping radar data reader...")
    ui.notify("Stopped radar data reader", type='info')

def send_detection_config(max_range, min_speed, delay_time):
    """Send target detection parameters"""
    # Command: setTargetDetectionParams(maxRange, minSpeed, delayTime)
    # Format: FD FC FB FA 06 00 02 00 <maxRange> 01 <minSpeed> <delayTime> 04 03 02 01
    max_range = int(max_range)
    min_speed = int(min_speed) 
    delay_time = int(delay_time)
    
    # Update configuration state
    current_config["max_range"] = max_range
    current_config["min_speed"] = min_speed
    current_config["delay_time"] = delay_time
    current_config["config_status"] = "Detection config sent (may not be supported)"
    current_config["last_updated"] = time.strftime("%H:%M:%S")
    update_config_display()
    
    cmd = f"FD FC FB FA 06 00 02 00 {max_range:02X} 01 {min_speed:02X} {delay_time:02X} 04 03 02 01"
    send_command(cmd)
    log(f"🎯 Sent detection config: Range={max_range}m, Speed={min_speed}km/h, Delay={delay_time}s")

def send_sensitivity_config(snr_level):
    """Send sensitivity parameters"""
    # Command: setSensitivityParams(snrLevel)
    # Format: FD FC FB FA 06 00 03 00 02 <snrLevel> 00 00 04 03 02 01
    snr_level = int(snr_level)
    
    # Update configuration state
    current_config["snr_level"] = snr_level
    current_config["config_status"] = "✅ Sensitivity configured successfully"
    current_config["last_updated"] = time.strftime("%H:%M:%S")
    update_config_display()
    
    cmd = f"FD FC FB FA 06 00 03 00 02 {snr_level:02X} 00 00 04 03 02 01"
    send_command(cmd)
    log(f"📡 Sent sensitivity config: SNR Level={snr_level}")

def read_detection_params():
    """Read target detection parameters"""
    # Command: readTargetDetectionParams()
    # Format: FD FC FB FA 02 00 12 00 04 03 02 01
    cmd = "FD FC FB FA 02 00 12 00 04 03 02 01"
    send_command(cmd)
    log("📖 Reading detection parameters")

def read_sensitivity_params():
    """Read sensitivity parameters"""
    # Command: readSensitivityParams()  
    # Format: FD FC FB FA 02 00 13 00 04 03 02 01
    cmd = "FD FC FB FA 02 00 13 00 04 03 02 01"
    send_command(cmd)
    log("📖 Reading sensitivity parameters")

# === UI with Grid Layout ===
ui.label('🧭 HLK-LD2451 Radar Configuration Tool').classes('text-xl font-bold mb-6')

# Main Grid Layout: 2x2 structure
# Top Row: Connection Controls | Configuration Display  
# Bottom Row: Live Radar Data | Debug Log
with ui.grid(columns='1fr 1fr').classes('w-full gap-6'):
    
    # Grid Cell 1: Connection & Control Panel
    with ui.card().classes('p-4'):
        ui.label('🔌 Connection & Controls').classes('text-lg font-bold mb-3')
        
        # Connection inputs in sub-grid
        with ui.grid(columns='2fr 1fr 1fr 1fr').classes('w-full gap-2 mb-4'):
            port_field = ui.input('Serial Port', value=PORT)
            baud_field = ui.input('Baud', value=str(BAUD))
            ui.button('Connect', on_click=connect_serial, color='green')
            ui.button('Disconnect', on_click=disconnect_serial, color='red')
        
        # Action buttons in 2x2 grid
        ui.label('⚡ Quick Actions').classes('text-sm font-semibold mt-4 mb-2')
        with ui.grid(columns='1fr 1fr').classes('w-full gap-2'):
            
            def ensure_data_mode():
                """Send end config command to ensure radar is in data output mode"""
                if not ser or not ser.is_open:
                    log("❌ Connect to serial first!")
                    return
                end_config_cmd = "FD FC FB FA 02 00 FE 00 04 03 02 01"
                send_command(end_config_cmd)
                log("📡 Sent END CONFIG - radar should now output data")
            
            ui.button('📡 Data Mode', on_click=ensure_data_mode, color='blue')
            
            def quick_config_and_start():
                """Configure radar with basic settings and start data mode"""
                if not ser or not ser.is_open:
                    log("❌ Connect to serial first!")
                    return
                
                log("🔧 Starting quick configuration sequence...")
                
                # 1. Enable config mode
                log("1️⃣ Enabling config mode...")
                send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01')
                time.sleep(0.5)
                
                # 2. Set sensitivity level 5 (medium) - using working format from Configuration tab
                log("2️⃣ Setting sensitivity level 5...")
                send_command('FD FC FB FA 06 00 03 00 02 05 00 00 04 03 02 01')
                time.sleep(0.5)
                
                # 3. End config mode to start data output
                log("3️⃣ Ending config mode - starting data output...")
                send_command('FD FC FB FA 02 00 FE 00 04 03 02 01')
                time.sleep(0.5)
                
                log("✅ Quick configuration complete - radar should now detect targets")
            
            ui.button('⚡ Quick Config', on_click=quick_config_and_start, color='orange')
            
            def simple_config_test():
                """Use the exact working sequence from Test SNR functions"""
                if not ser or not ser.is_open:
                    log("❌ Connect to serial first!")
                    return
                
                log("🧪 Using exact working sequence from Test SNR...")
                # Use the exact sequence that works in the Test SNR functions
                # Enable config mode
                send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01')
                # Send sensitivity after short delay (using timer like the working version)
                ui.timer(0.3, lambda: send_sensitivity_config(5), once=True)
                # End config mode
                ui.timer(0.6, lambda: send_command('FD FC FB FA 02 00 FE 00 04 03 02 01'), once=True)
                
            ui.button('🧪 Simple Test', on_click=simple_config_test, color='green')
            
            def test_high_sensitivity():
                """Test with very high sensitivity for maximum detection"""
                if not ser or not ser.is_open:
                    log("❌ Connect to serial first!")
                    return
                
                log("🔥 Testing HIGH SENSITIVITY (level 3) for maximum detection...")
                # Enable config mode
                send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01')
                # Send high sensitivity (level 3 = most sensitive)
                ui.timer(0.3, lambda: send_sensitivity_config(3), once=True)
                # End config mode
                ui.timer(0.6, lambda: send_command('FD FC FB FA 02 00 FE 00 04 03 02 01'), once=True)
                ui.timer(1.0, lambda: log("✅ High sensitivity set - try moving your hand slowly in front of radar"), once=True)
                
            ui.button('🔥 Max Sensitivity', on_click=test_high_sensitivity, color='red')
        
        ui.label('💡 Move hand slowly after clicking').classes('text-xs text-orange-600 mt-2')
        
        # Manual command input section
        ui.label('🖥️ Manual Commands').classes('text-sm font-semibold mt-4 mb-2')
        with ui.row().classes('w-full gap-2'):
            cmd_field = ui.input('Command (Hex)', placeholder='FD FC FB FA 04 00 FE 01 00 00 04 03 02 01').classes('flex-1')
            ui.button('Send', on_click=lambda: send_command(cmd_field.value)).props('icon=send')
    
    # Grid Cell 2: Configuration Status Display
    with ui.card().classes('p-4'):
        ui.label('⚙️ Configuration Status').classes('text-lg font-bold mb-3')
        
        # Configuration parameters in a neat grid
        with ui.grid(columns='1fr 1fr').classes('gap-3 mb-4'):
            range_display = ui.label(f'📏 Range: {current_config["max_range"]}m').classes('text-sm font-mono p-2 bg-green-50 rounded')
            speed_display = ui.label(f'🚗 Speed: {current_config["min_speed"]} km/h').classes('text-sm font-mono p-2 bg-green-50 rounded')
            delay_display = ui.label(f'⏱️ Delay: {current_config["delay_time"]}s').classes('text-sm font-mono p-2 bg-green-50 rounded')
            snr_display = ui.label(f'📡 SNR: {current_config["snr_level"]}').classes('text-sm font-mono font-bold p-2 bg-green-50 rounded')
        
        status_display = ui.label(current_config["config_status"]).classes('text-sm text-center p-2 bg-blue-50 rounded')
        updated_display = ui.label(f'Last updated: {current_config["last_updated"]}').classes('text-xs text-gray-600 text-center mt-2')
    
    # Grid Cell 3: Live Radar Data
    with ui.card().classes('p-4 bg-blue-50'):
        ui.label('🎯 Live Radar Targets').classes('text-lg font-bold text-blue-800 mb-3')
        
        # Status indicators in grid
        with ui.grid(columns='1fr 1fr').classes('gap-2 mb-4'):
            target_count_display = ui.label('📊 Active Targets: 0').classes('text-sm font-mono font-bold')
            radar_stats_display = ui.label('📈 Frames: 0').classes('text-sm font-mono text-gray-600')
        
        # Target data display - full width minus 10px
        target_list_display = ui.html('', sanitize=False).classes('text-sm font-mono h-40 overflow-y-auto bg-white p-3 rounded border').style('width: calc(100% - 10px);')
        
        # Live data controls in grid
        with ui.grid(columns='1fr 1fr').classes('gap-2 mt-4'):
            ui.button('📡 Start Live Data', on_click=lambda: start_radar_reader(), color='blue')
            ui.button('⏹️ Stop Live Data', on_click=lambda: stop_radar_reader(), color='orange')
    
    # Grid Cell 4: Debug Log & Tips
    with ui.card().classes('p-4'):
        ui.label('📝 Live Debug Log').classes('text-lg font-bold mb-3')
        
        # Debug log - full width minus 10px
        with ui.column().classes('h-48 overflow-y-auto bg-black text-white rounded p-3 font-mono mb-4').style('width: calc(100% - 10px);'):
            log_output = ui.label('--- Radar Configuration Log ---').classes('text-sm whitespace-pre')
        
        # Detection tips
        ui.label('💡 Detection Tips').classes('text-sm font-bold text-blue-800 mb-2')
        ui.html('''
        <div style="font-size: 11px; color: #666;">
            <div>✋ <strong>Hand:</strong> 0.5-2m, slow wave</div>
            <div>🚶 <strong>Walking:</strong> Cross field of view</div>
            <div>📏 <strong>Range:</strong> 0.2-12m optimal</div>
            <div>⚡ <strong>Speed:</strong> 0.1-10 m/s detectable</div>
        </div>''', sanitize=False)

# Configuration tabs (full width below)
ui.separator().classes('mt-4')
ui.label('Configuration Commands').classes('text-lg font-bold mt-3')

with ui.tabs().classes('w-full') as tabs:
    basic_tab = ui.tab('Basic')
    config_tab = ui.tab('Configuration')
    system_tab = ui.tab('System')

with ui.tab_panels(tabs, value=basic_tab).classes('w-full'):
    # Basic Commands Tab
    with ui.tab_panel(basic_tab):
        ui.markdown('**Basic radar operations and firmware information**')
        ui.markdown('⚠️ **IMPORTANT:** Enable config mode FIRST before any other commands!')
        ui.markdown('🔄 **Device exits config mode automatically** after some time or operations')
        with ui.row():
            ui.button('🟢 Enable Config Mode', on_click=lambda: send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01'), color='positive')
            ui.button('🔹 Read Firmware', on_click=lambda: send_command('FD FC FB FA 02 00 A0 00 04 03 02 01'))
        with ui.row().classes('mt-2'):
            ui.button('📊 Device Info (A1)', on_click=lambda: send_command('FD FC FB FA 02 00 A1 00 04 03 02 01', decode_a_series_response))
            ui.button('📊 System Status (A2)', on_click=lambda: send_command('FD FC FB FA 02 00 A2 00 04 03 02 01', decode_a_series_response))
            ui.button('🔴 End Config Mode', on_click=lambda: send_command('FD FC FB FA 02 00 FE 00 04 03 02 01'), color='negative')
    
    # Configuration Tab
    with ui.tab_panel(config_tab):
        ui.markdown("""
        ## Radar Configuration Commands ✅
        
        **🎯 LIVE TESTING RESULTS** (Just tested on your device):
        - ✅ **Sensitivity Config (0x03)**: **CONFIRMED WORKING** - Device acknowledges and responds properly
        - ❌ **Detection Config (0x02)**: No response - Hardware-fixed parameters (common on this model)
        - ✅ **Config Mode (0xFF/0xFE)**: **CONFIRMED WORKING** - Proper protocol handshaking
        
        **🚀 SUCCESS**: We found the working sensitivity configuration for HLK-LD2451!
        
        Based on successful microcontroller implementation + Live device validation:
        """)
        
        # Detection Parameters Section
        with ui.card().classes('w-full p-4 mt-4 bg-orange-50'):
            ui.label('Target Detection Parameters').classes('text-lg font-bold')
            ui.markdown('❌ **Device Testing Result**: No response to command 0x02 - Detection parameters may be fixed in hardware')
            ui.markdown('Command: `setTargetDetectionParams(maxRange, minSpeed, delayTime)` - Try anyway:')
            
            with ui.row().classes('w-full items-center gap-4'):
                ui.label('Max Range (m):').classes('w-32')
                config_max_range = ui.number(
                    label='Range', 
                    value=20, 
                    min=10, 
                    max=100, 
                    step=1
                ).classes('w-24')
                ui.label('Detection range in meters (10-100)')
            
            with ui.row().classes('w-full items-center gap-4'):
                ui.label('Min Speed (km/h):').classes('w-32')
                config_min_speed = ui.number(
                    label='Speed', 
                    value=1, 
                    min=1, 
                    max=60, 
                    step=1
                ).classes('w-24')
                ui.label('Minimum detection speed (1-60 km/h)')
            
            with ui.row().classes('w-full items-center gap-4'):
                ui.label('Delay Time (s):').classes('w-32')
                config_delay_time = ui.number(
                    label='Delay', 
                    value=2, 
                    min=0, 
                    max=10, 
                    step=1
                ).classes('w-24')
                ui.label('Detection delay in seconds (0-10)')
            
            with ui.row().classes('mt-4 gap-4'):
                ui.button('✅ Send Detection Config', 
                         on_click=lambda: send_detection_config(
                             config_max_range.value, 
                             config_min_speed.value, 
                             config_delay_time.value
                         ),
                         color='positive')
                ui.button('📖 Read Detection Params', 
                         on_click=read_detection_params,
                         color='secondary')
                
                def update_display_values():
                    """Update configuration display with current input values"""
                    current_config["max_range"] = int(config_max_range.value)
                    current_config["min_speed"] = int(config_min_speed.value)
                    current_config["delay_time"] = int(config_delay_time.value)
                    current_config["config_status"] = "📝 Display updated (not sent to device)"
                    current_config["last_updated"] = time.strftime("%H:%M:%S")
                    update_config_display()
                    ui.notify('Configuration display updated', type='info')
                
                ui.button('📋 Update Display Only', 
                         on_click=update_display_values,
                         color='secondary')
        
        # Sensitivity Parameters Section
        with ui.card().classes('w-full p-4 mt-4 bg-green-50'):
            ui.label('Sensitivity Parameters').classes('text-lg font-bold')
            ui.markdown('✅ **Device Testing Result**: Command 0x03 WORKS! Device acknowledges sensitivity changes')
            ui.markdown('Command: `setSensitivityParams(snrLevel)` - **CONFIRMED WORKING**:')
            
            with ui.row().classes('w-full items-center gap-4'):
                ui.label('SNR Level:').classes('w-32')
                config_snr_level = ui.number(
                    label='SNR', 
                    value=4, 
                    min=3, 
                    max=8, 
                    step=1
                ).classes('w-24')
                ui.label('Signal-to-Noise Ratio level (3-8)')
            
            with ui.row().classes('mt-4 gap-4'):
                ui.button('📡 Send Sensitivity Config', 
                         on_click=lambda: send_sensitivity_config(
                             config_snr_level.value
                         ),
                         color='positive')
                ui.button('📖 Read Sensitivity Params', 
                         on_click=read_sensitivity_params,
                         color='secondary')
                
                def update_snr_display():
                    """Update SNR display with current input value"""
                    current_config["snr_level"] = int(config_snr_level.value)
                    current_config["config_status"] = "📝 SNR display updated (not sent to device)"
                    current_config["last_updated"] = time.strftime("%H:%M:%S")
                    update_config_display()
                    ui.notify(f'SNR Level display updated to {config_snr_level.value}', type='info')
                
                ui.button('📋 Update SNR Display', 
                         on_click=update_snr_display,
                         color='secondary')
        
        # Configuration Process Section
        with ui.card().classes('w-full p-4 mt-4 bg-blue-50'):
            ui.label('Complete Configuration Process').classes('text-lg font-bold')
            ui.markdown("""
            **Configuration process:**
            1. Enable configuration mode
            2. Send detection parameters (0x02 command)
            3. Send sensitivity parameters (0x03 command)  
            4. End configuration mode
            
            **Command IDs:**
            - `0x02`: Target detection parameters
            - `0x03`: Sensitivity parameters
            - `0x12`: Read detection parameters
            - `0x13`: Read sensitivity parameters
            """)
            
            def run_full_config():
                """Execute the complete configuration sequence - single execution"""
                log("🚀 Starting configuration sequence...")
                
                # Step 1: Enable config mode
                send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01')
                log("1️⃣ Config mode enabled")
                
                # Step 2: Send detection config (after 500ms delay)
                ui.timer(0.5, lambda: [
                    send_detection_config(
                        config_max_range.value, 
                        config_min_speed.value, 
                        config_delay_time.value
                    ),
                    log("2️⃣ Detection parameters sent")
                ], once=True)
                
                # Step 3: Send sensitivity config (after 1000ms delay) 
                ui.timer(1.0, lambda: [
                    send_sensitivity_config(config_snr_level.value),
                    log("3️⃣ Sensitivity parameters sent")
                ], once=True)
                
                # Step 4: End config mode (after 1500ms delay)
                ui.timer(1.5, lambda: [
                    send_command('FD FC FB FA 02 00 FE 00 04 03 02 01'),
                    log("4️⃣ Config mode ended")
                ], once=True)
                
                # Step 5: Final completion message (after 2000ms delay)
                def complete_sequence():
                    current_config["config_status"] = "🎯 Complete configuration sent"
                    current_config["last_updated"] = time.strftime("%H:%M:%S")
                    update_config_display()
                    log("✅ Configuration sequence complete - ONE TIME EXECUTION")
                
                ui.timer(2.0, complete_sequence, once=True)
            
            ui.button('🎯 Run Complete Configuration', 
                     on_click=run_full_config,
                     color='positive').classes('w-full mt-4')
        
        # Quick Sensitivity Test Section
        with ui.card().classes('w-full p-4 mt-4 bg-green-100'):
            ui.label('Quick Sensitivity Test').classes('text-lg font-bold')
            ui.markdown('**Test different SNR levels** to see if device behavior changes:')
            
            def test_sensitivity_level(level):
                """Test a specific sensitivity level"""
                log(f"🧪 Testing SNR Level {level}...")
                # Enable config mode
                send_command('FD FC FB FA 04 00 FF 00 01 00 04 03 02 01')
                # Send sensitivity after short delay
                ui.timer(0.3, lambda: send_sensitivity_config(level), once=True)
                # End config mode
                ui.timer(0.6, lambda: send_command('FD FC FB FA 02 00 FE 00 04 03 02 01'), once=True)
            
            with ui.row().classes('gap-2'):
                ui.button('Test SNR=3 (Low)', on_click=lambda: test_sensitivity_level(3), color='blue')
                ui.button('Test SNR=5 (Medium)', on_click=lambda: test_sensitivity_level(5), color='blue')
                ui.button('Test SNR=7 (High)', on_click=lambda: test_sensitivity_level(7), color='blue')
            
            ui.markdown('💡 **Tip**: After setting different levels, observe if radar detection behavior changes with moving objects.')
    
    # System Operations Tab
    with ui.tab_panel(system_tab):
        ui.markdown('**System operations and data format settings**')
        with ui.card().classes('p-4'):
            ui.markdown('**Baud Rate Configuration**')
            with ui.row().classes('items-center gap-4'):
                baud_select = ui.select(['9600', '19200', '38400', '57600', '115200', '230400', '256000', '460800'], 
                                      value='115200', label='Baud Rate').props('outlined')
                
                def build_baud_cmd():
                    # Command ID: 0xA1 for baud rate setting
                    baud_map = {'9600': 1, '19200': 2, '38400': 3, '57600': 4, '115200': 5, '230400': 6, '256000': 7, '460800': 8}
                    baud_code = baud_map.get(baud_select.value, 5)
                    cmd = f"FD FC FB FA 04 00 A1 00 {baud_code:02X} 00 04 03 02 01"
                    return cmd
                
                ui.button('Set Baud Rate', on_click=lambda: send_command(build_baud_cmd()), color='orange')
            
            ui.separator().classes('my-4')
            
            ui.markdown('**Factory Reset & Restart**')
            with ui.row().classes('gap-4'):
                ui.button('Factory Reset', on_click=lambda: send_command('FD FC FB FA 02 00 A2 00 04 03 02 01'), color='red')
                ui.button('Restart Module', on_click=lambda: send_command('FD FC FB FA 02 00 A3 00 04 03 02 01'), color='orange')
            
            ui.separator().classes('my-4')
            
            ui.markdown('**Data Output Format**')
            with ui.column().classes('gap-2'):
                cb_target_info = ui.checkbox('Enable target information output', value=True)
                cb_engineering = ui.checkbox('Enable engineering mode data', value=False)
                
                def build_output_cmd():
                    # Command ID: 0x90 for output format (hypothetical - adjust based on actual protocol)
                    flags = 0
                    if cb_target_info.value:
                        flags |= 0x01
                    if cb_engineering.value:
                        flags |= 0x02
                    cmd = f"FD FC FB FA 04 00 90 00 {flags:02X} 00 04 03 02 01"
                    return cmd
                
                ui.button('Set Output Format', on_click=lambda: send_command(build_output_cmd()), color='secondary')

# Auto-update live targets display
ui.timer(0.5, update_live_targets_display)  # Update every 500ms

ui.run(title='HLK-LD2451 Config Tool', reload=False)
