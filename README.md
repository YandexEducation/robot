
Откройте: http://localhost:8085

## Загрузка модели на робота (Type-c)

```bash
python3 tools/upload_model.py -p PORT model.tflite
```

- MacOS: `-p /dev/cu.usbmodem*` (например `/dev/cu.usbmodem1101`)

Пример: `python3 tools/upload_model.py -p /dev/cu.usbmodem1101 /path/to/model.tflite`


Примеры работы с сенсорами робота в директории `examples`
