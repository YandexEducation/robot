import machine
import time
import neopixel
from machine import Pin, PWM, ADC, I2C
import config

class HCSR04:
    def __init__(self, trigger_pin, echo_pin, echo_timeout_us=500*2*30):
        self.trigger = Pin(trigger_pin, mode=Pin.OUT, pull=None)
        self.trigger.value(0)
        self.echo = Pin(echo_pin, mode=Pin.IN, pull=None)
        self.echo_timeout_us = echo_timeout_us

    def distance_cm(self):
        self.trigger.value(0)
        time.sleep_us(5)
        self.trigger.value(1)
        time.sleep_us(10)
        self.trigger.value(0)
        try:
            pulse_time = machine.time_pulse_us(self.echo, 1, self.echo_timeout_us)
            if pulse_time < 0:
                return -1
            return (pulse_time / 2) / 29.1
        except OSError:
            return -1

class Motor:
    def __init__(self, pwm_pin, in1_pin, in2_pin):
        self.pwm = PWM(Pin(pwm_pin), freq=1000)
        self.in1 = Pin(in1_pin, Pin.OUT)
        self.in2 = Pin(in2_pin, Pin.OUT)

    def move(self, speed):
        # Speed: -100 to 100
        speed = max(-100, min(100, speed))
        duty = int(abs(speed) * 1023 / 100)
        self.pwm.duty(duty)
        
        # Инверсия: исправление направления моторов (ранее крутились в обратную сторону)
        if speed > 0:
            self.in1.value(0)
            self.in2.value(1)
        elif speed < 0:
            self.in1.value(1)
            self.in2.value(0)
        else:
            self.in1.value(0)
            self.in2.value(0)
            self.pwm.duty(0)

class Motors:
    def __init__(self):
        self.left = Motor(config.MOTOR_A_PWM, config.MOTOR_A_IN1, config.MOTOR_A_IN2)
        self.right = Motor(config.MOTOR_B_PWM, config.MOTOR_B_IN1, config.MOTOR_B_IN2)
        # STBY может быть подтянут к 3.3V на плате и не иметь отдельного пина в config.py
        stby_pin = getattr(config, 'MOTOR_STBY', None)
        if stby_pin is not None:
            self.stby = Pin(stby_pin, Pin.OUT)
            self.stby.value(1)  # Enable motors

    def move(self, left_speed, right_speed):
        self.left.move(left_speed)
        self.right.move(right_speed)

    def stop(self):
        self.move(0, 0)

class Sensor:
    def __init__(self, pin):
        self.adc = ADC(Pin(pin))
        self.adc.atten(ADC.ATTN_11DB) # 0-3.3V range

    def read(self):
        return self.adc.read()

class Servo:
    def __init__(self, pin):
        self.pwm = PWM(Pin(pin), freq=50)

    def set_angle(self, angle):
        # 0-180 degrees -> duty cycle
        duty = int(26 + (angle / 180) * (128 - 26))
        self.pwm.duty(duty)

class Camera:
    def __init__(self, i2c):
        self.i2c = i2c
        self.addr = config.I2C_SLAVE_ADDR
        self.reg_sign = 0x01  # REG_SIGN
        self.reg_sign_conf = 0x02  # REG_SIGN_CONF (уверенность в процентах 0–100)

    def detect_sign(self):
        try:
            # Используем протокол writeto + readfrom, как в main.check_for_updates:
            # сначала отправляем номер регистра, затем отдельным запросом читаем байт.
            self.i2c.writeto(self.addr, bytes([self.reg_sign]))
            time.sleep_ms(5)
            data = self.i2c.readfrom(self.addr, 1)
            if not data:
                return None
            val = data[0]
            # Классы модели на ESP32‑S3:
            # 0: Class0, 1: Class1, 2: Class2
            if val == 0:
                return "Class0"
            if val == 1:
                return "Class1"
            if val == 2:
                return "Class2"
            return None
        except Exception:
            return None

    def detect_sign_with_conf(self):
        """
        Возвращает (sign, conf), где:
          - sign: "Class0" / "Class1" / "Class2" или None
          - conf: уверенность в процентах (0–100)

        Если прошивка ESP32‑S3 не поддерживает регистр уверенности,
        conf будет 0.
        """
        sign = self.detect_sign()
        conf = 0
        try:
            # Аналогично читаем регистр уверенности через writeto + readfrom
            self.i2c.writeto(self.addr, bytes([self.reg_sign_conf]))
            time.sleep_ms(5)
            data = self.i2c.readfrom(self.addr, 1)
            if data:
                conf = int(data[0])  # ожидаем 0–100
        except Exception:
            conf = 0
        return sign, conf

class Sharp:
    def __init__(self, pin):
        self.adc = ADC(Pin(pin))
        self.adc.atten(ADC.ATTN_11DB)

    def read(self):
        return self.adc.read()
    
    def distance_cm(self):
        val = self.adc.read()
        if val < 100: return 80
        volts = val * 3.3 / 4095
        return 27.86 * (volts ** -1.15)

class Encoder:
    """Quadrature encoder with IRQ on both channels."""
    def __init__(self, pin_a, pin_b):
        self.pin_a = Pin(pin_a, Pin.IN, Pin.PULL_UP)
        self.pin_b = Pin(pin_b, Pin.IN, Pin.PULL_UP)
        self._count = 0
        self.pin_a.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._isr)
        self.pin_b.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._isr)

    def _isr(self, pin):
        self._count += 1

    def read(self):
        return self._count

    def reset(self):
        self._count = 0

class Button:
    def __init__(self, pin):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_UP)

    def is_pressed(self):
        return not self.pin.value()

# I2C registers for sensor data (ESP32 -> S3), packet 22 bytes
REG_SENSORS = 0x20

class Robot:
    def __init__(self):
        self.motors = Motors()
        # Линия: три аналоговых датчика
        self.line_left = Sensor(config.LINE_LEFT)
        self.line_sensor = Sensor(config.LINE_CENTER)
        self.line_right = Sensor(config.LINE_RIGHT)

        # Ультразвук может быть отключён в config.py
        if hasattr(config, 'ULTRASONIC_TRIG') and hasattr(config, 'ULTRASONIC_ECHO'):
            self.ultrasonic = HCSR04(config.ULTRASONIC_TRIG, config.ULTRASONIC_ECHO)
        else:
            self.ultrasonic = None

        # Фоторезистор опционален
        photo_pin = getattr(config, 'PHOTORESISTOR', None)
        self.photoresistor = Sensor(photo_pin) if photo_pin is not None else None

        # Серво опционален
        servo_pin = getattr(config, 'SERVO_PIN', None)
        self.servo = Servo(servo_pin) if servo_pin is not None else None
        self.leds = neopixel.NeoPixel(Pin(config.LED_PIN), config.LED_COUNT)
        self.builtin_led = Pin(config.BUILTIN_LED_PIN, Pin.OUT)  # зелёный (RXD2)
        self._led_inverted = getattr(config, 'BUILTIN_LED_INVERTED', True)
        self.led_red = Pin(getattr(config, 'LED_RED_PIN', 17), Pin.OUT)  # красный (TXD2)
        self._led_red_inverted = getattr(config, 'LED_RED_INVERTED', True)
        self.button = Button(config.BUTTON_PIN)
        
        self.i2c = I2C(0, scl=Pin(config.I2C_SCL), sda=Pin(config.I2C_SDA), freq=100000)
        self.camera = Camera(self.i2c)
        
        sharp_pin = getattr(config, 'SHARP_PIN', 37)
        try:
            self.sharp = Sharp(sharp_pin)
        except (ValueError, OSError):
            self.sharp = None  # Sharp на не-ADC пине или не подключён
        
        self.left_encoder = Encoder(config.ENCODER_LEFT_A, config.ENCODER_LEFT_B)
        self.right_encoder = Encoder(config.ENCODER_RIGHT_A, config.ENCODER_RIGHT_B)

    def sleep(self, seconds):
        return seconds

    def log(self, msg):
        """Вывод сообщения в web. Использовать светодиоды (bot.leds)
        для визуальной обратной связи. В симуляторе bot.log() показывается в логе."""
        pass  # no-op on hardware; simulator shows in log panel

    def builtin_led_on(self):
        self.builtin_led.value(0 if self._led_inverted else 1)

    def builtin_led_off(self):
        self.builtin_led.value(1 if self._led_inverted else 0)

    def led_red_on(self):
        self.led_red.value(0 if self._led_red_inverted else 1)

    def led_red_off(self):
        self.led_red.value(1 if self._led_red_inverted else 0)

    def send_sensors_to_s3(self):
        """Push sensor data to S3 via I2C for BLE broadcast to web."""
        try:
            # Дистанция (если ультразвук есть)
            if self.ultrasonic is not None:
                dist = self.ultrasonic.distance_cm()
                dist_u16 = int(max(0, min(400, dist)) * 10) if dist >= 0 else 0
            else:
                dist_u16 = 0
            # Версия пользовательского скрипта (если main.py сохранил её)
            script_size = getattr(self, 'script_size', 0) or 0
            line_l = min(4095, self.line_left.read())
            line_c = min(4095, self.line_sensor.read())
            line_r = min(4095, self.line_right.read())
            enc_l = self.left_encoder.read() & 0xFFFFFFFF
            enc_r = self.right_encoder.read() & 0xFFFFFFFF
            # В поле photo передаём размер скрипта (младшие 16 бит) вместо фоторезистора
            photo = int(script_size) & 0xFFFF
            sharp_val = min(4095, self.sharp.read()) if self.sharp else 0
            btn = 1 if self.button.is_pressed() else 0
            v = self.builtin_led.value()
            led_on = 1 if (v if not self._led_inverted else (1 - v)) else 0
            data = bytes([
                dist_u16 & 0xFF, dist_u16 >> 8,
                line_l & 0xFF, line_l >> 8,
                line_c & 0xFF, line_c >> 8,
                line_r & 0xFF, line_r >> 8,
                enc_l & 0xFF, (enc_l >> 8) & 0xFF, (enc_l >> 16) & 0xFF, enc_l >> 24,
                enc_r & 0xFF, (enc_r >> 8) & 0xFF, (enc_r >> 16) & 0xFF, enc_r >> 24,
                photo & 0xFF, photo >> 8,
                sharp_val & 0xFF, sharp_val >> 8,
                btn, led_on
            ])
            self.i2c.writeto_mem(config.I2C_SLAVE_ADDR, REG_SENSORS, data)
        except Exception as e:
            pass  # Silent fail

# Create the bot instance
bot = Robot()
