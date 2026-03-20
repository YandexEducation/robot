#!/usr/bin/env python3
"""
Upload model.tflite to ESP32S3 via USB Serial.

Usage inside Docker:
  docker exec -it olympic_robot-robot-server-1 python /app/tools/upload_model.py /app/model.tflite

  Or with explicit port:
  docker exec -it olympic_robot-robot-server-1 python /app/tools/upload_model.py -p /dev/ttyUSB0 /app/model.tflite

Requires: device passed to container (docker-compose devices)
"""

import serial
import struct
import sys
import time
import glob
import threading

def find_port():
    """Try to find ESP32 serial port. XIAO ESP32-S3 uses usbmodem (USB CDC)."""
    if sys.platform == "win32":
        ports = [f"COM{i}" for i in range(1, 20)]
    else:
        ports = (glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/cu.usbserial*") +
                 glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    for p in ports:
        try:
            s = serial.Serial(p, 460800, timeout=0.5)
            s.close()
            return p
        except Exception:
            pass
    return None

def main():
    port = None
    model_path = None
    
    args = list(sys.argv[1:])
    if "-p" in args or "--port" in args:
        idx = args.index("-p") if "-p" in args else args.index("--port")
        port = args[idx + 1]
        args.pop(idx)
        args.pop(idx)
    if args:
        model_path = args[0]
    
    if not model_path:
        print("Usage: python upload_model.py [-p PORT] model.tflite")
        print("  Example: python upload_model.py /app/model.tflite")
        print("  Example: python upload_model.py -p /dev/ttyUSB0 /app/model.tflite")
        sys.exit(1)
    
    if not port:
        port = find_port()
        if not port:
            # Docker often maps device to /dev/ttyUSB0
            try:
                s = serial.Serial("/dev/ttyUSB0", 460800, timeout=0.5)
                s.close()
                port = "/dev/ttyUSB0"
            except Exception:
                pass
        if not port:
            print("No serial port found. Specify with -p PORT")
            print("  Linux: -p /dev/ttyUSB0")
            print("  Mac:   -p /dev/cu.usbmodem* (XIAO) or /dev/cu.usbserial*")
            print("  Docker: device mapped to /dev/ttyUSB0 via docker-compose")
            sys.exit(1)
    
    with open(model_path, "rb") as f:
        data = f.read()
    
    size = len(data)
    if size == 0:
        print("ERROR: Model file is empty! Train the model first.")
        sys.exit(1)
    if size < 5000:
        print(f"WARNING: Model seems too small ({size} bytes). Expected ~50-500KB.")
    print(f"Model: {model_path}, size: {size} bytes")
    print(f"Port: {port}")
    
    try:
        ser = serial.Serial(port, 460800, timeout=2)
        time.sleep(1.5)  # Port open resets board - wait for full boot
        ser.reset_input_buffer()
        
        print("Sending UPLOAD_MODEL...")
        ser.write(b"UPLOAD_MODEL\n")
        ser.flush()
        time.sleep(0.3)
        
        # Wait for READY (firmware prints it when command received)
        line = ""
        for _ in range(30):
            if ser.in_waiting:
                line += ser.read(ser.in_waiting).decode("utf-8", errors="ignore")
            if "READY" in line:
                break
            time.sleep(0.1)
        
        if "READY" not in line:
            print("No READY. Is the board connected and running? Try opening Serial Monitor first.")
            sys.exit(1)
        
        # Read in background FIRST (device prints RX_START, RX_SIZE; must consume to avoid deadlock)
        rx_buffer = []
        rx_done = threading.Event()
        def read_thread():
            try:
                while not rx_done.is_set():
                    if ser.in_waiting:
                        rx_buffer.append(ser.read(ser.in_waiting))
                    time.sleep(0.03)
            except Exception:
                pass
        reader = threading.Thread(target=read_thread, daemon=True)
        reader.start()
        time.sleep(0.1)
        
        # Send size immediately — device waits in readBytes (3s timeout)
        
        # Send size (4 bytes little endian)
        ser.write(struct.pack("<I", size))
        ser.flush()
        time.sleep(0.2)
        
        # Send data in chunks (256 bytes — reliable on Mac USB CDC)
        chunk = 256
        sent = 0
        while sent < size:
            n = min(chunk, size - sent)
            ser.write(data[sent:sent + n])
            ser.flush()
            sent += n
            print(f"\r  {sent}/{size} ({100*sent//size}%)", end="")
            time.sleep(0.025)
            if sent % 10000 < chunk:
                time.sleep(0.05)
        print()
        
        print("Waiting for OK (~30s)...")
        ok_received = False
        for _ in range(60):
            time.sleep(0.5)
            line = b"".join(rx_buffer).decode("utf-8", errors="ignore")
            if "OK" in line:
                ok_received = True
                print("Upload complete! Board restarting...")
                break
            if "ERR" in line:
                rx_done.set()
                print(f"Error: {line[:200]}")
                sys.exit(1)
        rx_done.set()
        if not ok_received:
            line = b"".join(rx_buffer).decode("utf-8", errors="ignore")
            if not line and sent == size:
                print("No OK (device may have restarted — check MODEL_STATUS in Serial Monitor)")
            else:
                print("Warning: No OK. Device output:", repr(line[-600:]) if line else "(nothing)")
                if "RX_START" in line and "RX_SIZE" in line:
                    print("  -> Device got size; data transfer may have failed.")
                elif "RX_START" in line:
                    print("  -> Device got command but not 4-byte size.")
                elif "ERR:" in line:
                    print("  -> See ERR message above. Common: ERR:no space = need Partition Scheme with SPIFFS.")
        
        ser.close()
        
        # Verify: wait for restart, then check MODEL_STATUS
        print("\nVerifying (wait 6s for board restart)...")
        time.sleep(6)
        try:
            ser2 = serial.Serial(port, 460800, timeout=2)
            time.sleep(1)
            ser2.reset_input_buffer()
            ser2.write(b"MODEL_STATUS\n")
            resp = ""
            for _ in range(25):
                if ser2.in_waiting:
                    resp += ser2.read(ser2.in_waiting).decode("utf-8", errors="ignore")
                if "modelLoaded=1" in resp:
                    print("Verified: Model loaded on device.")
                    break
                if "modelLoaded=0" in resp and "file exists=1" in resp:
                    if "size: 0" in resp:
                        print("WARNING: File exists but size=0. Re-upload may be needed.")
                    else:
                        print("WARNING: File exists but model not loaded (TF error?). Check Serial Monitor.")
                    break
                if "modelLoaded=0" in resp and "file exists=0" in resp:
                    print("WARNING: Model file not found! Check: Partition Scheme = LittleFS in Arduino IDE.")
                    break
                time.sleep(0.2)
            else:
                print("(Could not verify - open Serial Monitor and send MODEL_STATUS)")
            ser2.close()
        except Exception as e:
            print(f"(Verify skipped: {e})")
        
    except serial.SerialException as e:
        print(f"Serial error: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print(f"File not found: {model_path}")
        sys.exit(1)

if __name__ == "__main__":
    main()
