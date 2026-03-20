import machine
import time
import os
import sys
import config

# Watchdog: перезапуск при зависании (бесконечный цикл без yield, блокировка и т.д.)
WDT_TIMEOUT_MS = 5000  # 5 сек — если feed() не вызван, сброс
try:
    wdt = machine.WDT(timeout=WDT_TIMEOUT_MS)
except Exception:
    wdt = None  # WDT может быть недоступен на некоторых платах

import robot

# Встроенный безопасный скрипт — всегда доступен при сбое загруженного кода
# Сразу останавливает моторы, затем мигает LED
DEFAULT_SCRIPT = b'''# Minimal safe script (fallback)
def run_robot(bot):
    if hasattr(bot, "motors"):
        bot.motors.stop()
    print("LED blink (fallback) started!")
    while True:
        bot.builtin_led_on()
        yield bot.sleep(0.5)
        bot.builtin_led_off()
        yield bot.sleep(0.5)
'''

def restore_default_script():
    """Восстанавливает user_script.py из встроенного безопасного скрипта."""
    try:
        with open('user_script.py', 'wb') as f:
            f.write(DEFAULT_SCRIPT)
        print("Restored default (safe) script.")
    except Exception as e:
        print("Failed to restore default script:", e)


def safe_stop_motors(bot):
    """Останавливает моторы при любой ошибке — защита от runaway."""
    try:
        if hasattr(bot, 'motors') and bot.motors:
            bot.motors.stop()
    except Exception:
        pass


def wdt_feed():
    """Подаёт сигнал watchdog (если доступен)."""
    if wdt:
        try:
            wdt.feed()
        except Exception:
            pass


def get_script_version(path='user_script.py'):
    """
    Возвращает пару (size, checksum) для файла скрипта.
    checksum — простой суммарный хеш по байтам (mod 65536).
    """
    try:
        st = os.stat(path)
        size = st[6] if isinstance(st, tuple) and len(st) > 6 else st[0]
    except Exception:
        return None, None
    checksum = 0
    try:
        with open(path, 'rb') as f:
            while True:
                chunk = f.read(256)
                if not chunk:
                    break
                for b in chunk:
                    checksum = (checksum + (b if isinstance(b, int) else ord(b))) & 0xFFFF
    except Exception:
        return size, None
    return size, checksum


def safe_sleep(seconds):
    """Sleep с периодическим feed watchdog — защита при длинных bot.sleep()."""
    remaining = float(seconds)
    chunk = 0.5  # макс 500мс за раз
    while remaining > 0:
        time.sleep(min(remaining, chunk))
        remaining -= chunk
        wdt_feed()

# I2C Configuration for Updates
I2C_ADDR = config.I2C_SLAVE_ADDR
REG_STATUS = 0x10
REG_LEN = 0x11
REG_DATA = 0x12
REG_HEARTBEAT = 0x30

def check_for_updates(bot):
    """
    Checks if the S3 has a new script for us.
    Protocol:
    - Read REG_STATUS (1 byte): 1 = New Script Available, 0 = None
    - If 1:
        - Loop:
            - Read REG_LEN (1 byte): Length of next chunk
            - If 0: End of file
            - Read REG_DATA (REG_LEN bytes): The data
            - Write to file
        - Write REG_STATUS = 0 (Ack)
        - Restart
    """
    if not hasattr(bot, 'i2c') or bot.i2c is None:
        return
    try:
        bot.i2c.writeto(I2C_ADDR, bytes([REG_STATUS]))
        time.sleep(0.005)
        status = bot.i2c.readfrom(I2C_ADDR, 1)[0]
        
        if status == 1:
            print("New script detected on S3! Downloading...")
            if hasattr(bot, 'leds'):
                bot.leds.fill((0, 0, 255))  # Blue indicating download
                bot.leds.write()
            time.sleep(0.5)  # Дать S3 время подготовиться после EOF
            
            DOWNLOAD_TIMEOUT_MS = 15000  # 15 сек макс на загрузку
            last_progress = time.ticks_ms()
            download_ok = False
            with open('user_script_new.py', 'wb') as f:
                total = 0
                while True:
                    if time.ticks_diff(time.ticks_ms(), last_progress) > DOWNLOAD_TIMEOUT_MS:
                        print("Download timeout! Aborting.")
                        break
                    try:
                        # writeto + readfrom вместо readfrom_mem (надёжнее на ESP32)
                        bot.i2c.writeto(I2C_ADDR, bytes([REG_LEN]))
                        time.sleep(0.005)
                        len_data = bot.i2c.readfrom(I2C_ADDR, 1)
                        chunk_len = len_data[0]
                        
                        if chunk_len == 0:
                            download_ok = total > 0
                            break
                        
                        bot.i2c.writeto(I2C_ADDR, bytes([REG_DATA]))
                        time.sleep(0.005)
                        chunk = bot.i2c.readfrom(I2C_ADDR, chunk_len)
                        last_progress = time.ticks_ms()
                        if len(chunk) != chunk_len:
                            print("I2C chunk size mismatch:", len(chunk), "!=", chunk_len)
                        f.write(chunk)
                        total += len(chunk)
                        time.sleep(0.05)
                        wdt_feed()
                    except OSError as e:
                        print("I2C read error:", e)
                        time.sleep(0.1)
                        continue
            
            if download_ok and total > 0:
                print("Downloaded", total, "bytes")
                # Проверяем скрипт перед применением (уже в user_script_new.py)
                # Важно: сбрасываем кэш import, иначе MicroPython может взять старую версию.
                try:
                    sys.modules.pop('user_script_new', None)
                except Exception:
                    pass
                try:
                    import user_script_new
                    if not hasattr(user_script_new, 'run_robot'):
                        raise AttributeError("run_robot not found")
                    del user_script_new
                except Exception as e:
                    print("Script validation failed:", e, "- keeping old script.")
                    try:
                        os.remove('user_script_new.py')
                    except Exception:
                        pass
                    return
                # Валидация OK — заменяем
                try:
                    os.remove('user_script.py')
                except Exception:
                    pass
                os.rename('user_script_new.py', 'user_script.py')
                bot.i2c.writeto_mem(I2C_ADDR, REG_STATUS, b'\x00')
                if hasattr(bot, 'leds'):
                    bot.leds.fill((0, 255, 0))  # Green success
                    bot.leds.write()
                else:
                    bot.builtin_led_on()
                time.sleep(1)
                print("Restarting...")
                machine.reset()
            else:
                print("Download failed or empty, keeping old script.")
            
    except Exception as e:
        pass  # I2C недоступен (S3 не подключён/не отвечает) — тихо

def main():
    print("Booting Robot Firmware v1.0...")
    print("DEBUG: LEDs green=%s red=%s strip=%s" % (
        getattr(config, 'LED_GREEN_PIN', 16),
        getattr(config, 'LED_RED_PIN', 17),
        getattr(config, 'LED_PIN', 23)))
    
    # Initialize hardware
    bot = robot.bot
    if hasattr(bot, 'led_red'):
        bot.led_red_off()
    if hasattr(bot, 'leds'):
        bot.leds.fill((50, 50, 0))  # Yellow boot
        bot.leds.write()
    time.sleep(0.5)
    wdt_feed()

    # Check for updates (S3 по I2C)
    check_for_updates(bot)
    wdt_feed()

    # Try to import and run user script
    try:
        import user_script
        print("DEBUG: user_script loaded OK")
        # Печатаем «версию» текущего скрипта по файлу user_script.py
        size, checksum = get_script_version('user_script.py')
        if size is not None:
            print("USER_SCRIPT_VERSION size=%d checksum=%s" % (size, str(checksum)))
            # Сохраняем версию в объекте bot, чтобы её мог отправить S3
            try:
                bot.script_size = int(size)
                bot.script_checksum = int(checksum) if checksum is not None else 0
            except Exception:
                pass
        print("Starting user_script...")
        
        # Create generator
        generator = user_script.run_robot(bot)
        last_update_check = time.ticks_ms()
        last_heartbeat = time.ticks_ms()
        last_sensor_send = time.ticks_ms()
        
        while True:
            try:
                # 1. Execute next step of robot logic
                delay = next(generator)
                wdt_feed()
                
                # 2. Push sensor data to S3 (каждые 100мс, если есть метод)
                now = time.ticks_ms()
                if hasattr(bot, 'send_sensors_to_s3') and time.ticks_diff(now, last_sensor_send) > 100:
                    last_sensor_send = now
                    try:
                        bot.send_sensors_to_s3()
                    except Exception as e:
                        print("send_sensors exception:", e)
                # 3. Heartbeat to S3 (каждые 2 сек) — видно в консоли S3
                if hasattr(bot, 'i2c') and bot.i2c and time.ticks_diff(now, last_heartbeat) > 2000:
                    last_heartbeat = now
                    try:
                        bot.i2c.writeto_mem(I2C_ADDR, REG_HEARTBEAT, b'\x01')
                    except Exception:
                        pass
                # 4. Check for script updates (каждые 3 сек)
                if time.ticks_diff(now, last_update_check) > 3000:
                    last_update_check = now
                    check_for_updates(bot)
                
                # 5. Handle delay (safe_sleep кормит watchdog при длинных паузах)
                if isinstance(delay, (int, float)):
                    safe_sleep(delay)
                else:
                    safe_sleep(0.01)
                    
            except StopIteration:
                print("User script finished.")
                safe_stop_motors(bot)
                break
            except Exception as e:
                print(f"Runtime Error: {e}")
                safe_stop_motors(bot)
                if hasattr(bot, 'led_red'):
                    bot.led_red_on()
                if hasattr(bot, 'leds'):
                    bot.leds.fill((255, 0, 0))
                    bot.leds.write()
                time.sleep(1)
                restore_default_script()
                print("Restarting with default script...")
                machine.reset()
                
    except (ImportError, AttributeError, SyntaxError, TypeError, OSError) as e:
        print("User script error:", e)
        safe_stop_motors(bot)
        restore_default_script()
        print("Restarting with default script...")
        time.sleep(1)
        machine.reset()

if __name__ == "__main__":
    main()
