#!/usr/bin/env python3
"""Inspect TFLite model: ops, input/output, quantization."""
import sys

def main():
    try:
        import tensorflow as tf
    except ImportError:
        print("Install: pip install tensorflow")
        sys.exit(1)
    
    path = sys.argv[1] if len(sys.argv) > 1 else "model.tflite"
    try:
        with open(path, "rb") as f:
            tflite_model = f.read()
    except FileNotFoundError:
        print(f"File not found: {path}")
        sys.exit(1)
    
    print(f"Model: {path}, size: {len(tflite_model)} bytes\n")
    
    interpreter = tf.lite.Interpreter(model_content=tflite_model)
    interpreter.allocate_tensors()
    
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    
    print("\n=== INPUT ===")
    for i, d in enumerate(input_details):
        print(f"  Shape: {d['shape']}")
        print(f"  Dtype: {d['dtype']}")
        if 'quantization_parameters' in d and d['quantization_parameters']:
            q = d['quantization_parameters']
            scales = q.get('scales', [])
            zero_points = q.get('zero_points', [])
            print(f"  Scale: {scales[0] if scales else 'N/A'}")
            print(f"  Zero point: {zero_points[0] if zero_points else 'N/A'}")
            if scales and zero_points:
                zp = int(zero_points[0])
                sc = float(scales[0])
                print(f"  Formula: real = (int8 - {zp}) * {sc}")
                print(f"  For pixel 0-255: int8 = pixel / {sc} + {zp} = pixel*{1/sc:.2f} + {zp}")
    
    print("\n=== OUTPUT ===")
    for i, d in enumerate(output_details):
        print(f"  Shape: {d['shape']}")
        print(f"  Dtype: {d['dtype']}")
        if 'quantization_parameters' in d and d['quantization_parameters']:
            q = d['quantization_parameters']
            scales = q.get('scales', [])
            zero_points = q.get('zero_points', [])
            print(f"  Scale: {scales[0] if scales else 'N/A'}")
            print(f"  Zero point: {zero_points[0] if zero_points else 'N/A'}")

if __name__ == "__main__":
    main()
