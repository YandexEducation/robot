"""Короткий пример управления LED‑лентой.

Показывает, как включить и выключить все светодиоды.
"""


def run_robot(bot):
    # Цвета в формате (R, G, B)
    RED = (255, 0, 0)
    OFF = (0, 0, 0)

    while True:
        # Включаем все светодиоды красным цветом
        bot.leds.fill(RED)
        bot.leds.write()
        yield bot.sleep(0.5)

        # Выключаем все светодиоды
        bot.leds.fill(OFF)
        bot.leds.write()
        yield bot.sleep(0.5)

