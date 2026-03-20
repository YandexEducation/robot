#!/usr/bin/env python3
"""
Test TFLite model inference with same preprocessing as firmware.
Usage: python test_model_inference.py model.tflite [dataset_dir]
"""
import sys
import numpy as np

def main():
    try:
        import tensorflow as tf
    except ImportError:
        print("Install: pip install tensorflow")
        sys.exit(1)

    model_path = sys.argv[1] if len(sys.argv) > 1 else "model.tflite"
    data_dir = sys.argv[2] if len(sys.argv) > 2 else "dataset"

    try:
        with open(model_path, "rb") as f:
            tflite_model = f.read()
    except FileNotFoundError:
        print(f"Model not found: {model_path}")
        sys.exit(1)

    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    print("=== Model Info ===")
    print(f"Input shape: {input_details[0]['shape']}")
    print(f"Input dtype: {input_details[0]['dtype']}")
    print(f"Output shape: {output_details[0]['shape']}")

    q = input_details[0].get('quantization_parameters', {})
    scales = q.get('scales', [])
    zps = q.get('zero_points', [])
    scale = float(scales[0]) if scales else 1.0
    zp = int(zps[0]) if zps else 0
    print(f"Input scale={scale}, zero_point={zp}")
    print(f"Firmware should use: int8 = round(pixel/255/scale) + {zp} = round(pixel*{1/(255*scale):.4f}) + {zp}")

    # Test with sample images
    from pathlib import Path
    labels = ["0_Class0", "1_Class1", "2_Class2"]
    is_float = input_details[0]['dtype'] == np.float32

    for i, folder in enumerate(labels):
        folder_path = Path(data_dir) / folder
        if not folder_path.exists():
            continue
        imgs = list(folder_path.glob("*.png")) + list(folder_path.glob("*.jpg"))
        if not imgs:
            continue
        img_path = imgs[0]
        img = tf.keras.preprocessing.image.load_img(
            img_path, color_mode="grayscale", target_size=(115, 115)
        )
        arr = tf.keras.preprocessing.image.img_to_array(img)  # (H, W, 1)
        if is_float:
            input_data = (arr / 255.0).astype(np.float32).flatten()
        else:
            input_data = (np.array(arr, dtype=np.int32) - 128).astype(np.int8).flatten()

        interpreter.set_tensor(input_details[0]['index'], input_data.reshape(input_details[0]['shape']))
        interpreter.invoke()
        output = interpreter.get_tensor(output_details[0]['index'])
        pred = np.argmax(output.flatten())
        scores = output.flatten()
        print(f"\n  {folder}: pred={pred} ({labels[pred] if pred < len(labels) else '?'})")
        print(f"    Raw scores: {[f'{s:.1f}' for s in scores]}")

    # Also test with blank image
    blank = np.zeros((115, 115, 1), dtype=np.uint8) + 128
    if is_float:
        input_data = (blank.astype(np.float32) / 255.0).flatten()
    else:
        input_data = (blank.astype(np.int32) - 128).astype(np.int8).flatten()
    interpreter.set_tensor(input_details[0]['index'], input_data.reshape(input_details[0]['shape']))
    interpreter.invoke()
    output = interpreter.get_tensor(output_details[0]['index'])
    pred = np.argmax(output.flatten())
    print(f"\n  Blank (gray): pred={pred}, scores={output.flatten()}")

if __name__ == "__main__":
    main()
