"""Короткий пример управления моторами.

Запускается в симуляторе и на реальном роботе одинаково.
"""


def run_robot(bot):
    # Вперёд
    bot.motors.move(40, 40)
    yield bot.sleep(1.0)

    # Стоим на месте
    bot.motors.stop()
    yield bot.sleep(1.0)

    # Назад
    bot.motors.move(-40, -40)
    yield bot.sleep(1.0)

    # Полная остановка и «ничего не делать», чтобы скрипт не завершался
    bot.motors.stop()
    while True:
        yield bot.sleep(1.0)

