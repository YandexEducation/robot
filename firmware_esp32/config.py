# Pin Configuration for ESP32 Robot
# TODO: Verify these pins with actual hardware or schematic!

# Motor Driver (TB6612FNG) — ваша распиновка
MOTOR_A_PWM = 32   # PWMA
MOTOR_A_IN1 = 2    # AIN1
MOTOR_A_IN2 = 5    # AIN2
MOTOR_B_PWM = 13   # PWMB
MOTOR_B_IN1 = 15   # BIN1
MOTOR_B_IN2 = 12   # BIN2
#MOTOR_STBY = 27    # STBY (HIGH = моторы включены)

# Encoders
ENCODER_LEFT_A = 34
ENCODER_LEFT_B = 35
ENCODER_RIGHT_A = 36
ENCODER_RIGHT_B = 39

# Line Sensors (Analog)
# Три аналоговых датчика линии на ADC-пинах
LINE_LEFT = 27      # левый датчик (ADC2, GPIO27)
LINE_CENTER = 26    # центр (ADC2, GPIO15)
LINE_RIGHT = 25     # правый (ADC2, GPIO25)
LINE_SENSOR = LINE_CENTER  # алиас для минимального режима

# Ultrasonic (HC-SR04)
# ULTRASONIC_TRIG = 5
# ULTRASONIC_ECHO = 18

# Photoresistor (Analog) 
# PHOTORESISTOR = 26

# Sharp IR distance (Analog) — Sharp подключён к GPIO14 (ADC2_CH6)
# 32,33=линия, 34,35=энк L, 36,39=энк R, 14=Sharp.
SHARP_PIN = 14

# Servo
#SERVO_PIN = 4

# LEDs
# RXD2 (GPIO16) — зелёный светодиод
# TXD2 (GPIO17) — красный светодиод
# D23 (GPIO23) — NeoPixel/WS2812 лента
LED_GREEN_PIN = 16   # RXD2
LED_RED_PIN = 17     # TXD2
LED_PIN = 23         # D23 — лента
LED_COUNT = 12

# Зелёный LED как основной (builtin) — для совместимости
BUILTIN_LED_PIN = LED_GREEN_PIN
BUILTIN_LED_INVERTED = True   # True если LED горит при LOW
LED_RED_INVERTED = True       # красный LED

# Button (физическая кнопка на GPIO 4)
BUTTON_PIN = 4

# I2C (Communication with S3)
# ESP32: SDA GPIO 21, SCL GPIO 22  <->  Xiao S3: SDA GPIO 5, SCL GPIO 6 (или 43/44)
I2C_SDA = 21
I2C_SCL = 22
I2C_SLAVE_ADDR = 0x42  # Address of the S3 camera module
