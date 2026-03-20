# I2C Scanner for MicroPython (ESP32)
import machine

def scan_i2c():
    # Настройка пинов I2C согласно нашей схеме
    sda_pin = machine.Pin(21)
    scl_pin = machine.Pin(22)
    
    print(f"Scanning I2C bus (SDA={sda_pin}, SCL={scl_pin})...")
    
    try:
        i2c = machine.I2C(0, sda=sda_pin, scl=scl_pin, freq=100000)
        devices = i2c.scan()
        
        if len(devices) == 0:
            print("No I2C devices found!")
        else:
            print("Found I2C devices:", len(devices))
            for device in devices:
                print(f"Decimal: {device} | Hex: {hex(device)}")
                if device == 0x42:
                    print("  -> Vision Module found!")
                elif device == 0x68 or device == 0x69:
                    print("  -> IMU (MPU9250/6050) found!")
                    
    except Exception as e:
        print(f"I2C Error: {e}")

if __name__ == "__main__":
    scan_i2c()
