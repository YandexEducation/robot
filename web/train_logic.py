import os
import numpy as np
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers, models
import pathlib
import glob
from PIL import Image

# --- CONFIGURATION ---
IMG_HEIGHT = 96
IMG_WIDTH = 96
BATCH_SIZE = 32
EPOCHS = 20
DATASET_DIR = "dataset"
MODEL_FILENAME = "model.tflite"

def load_data():
    """
    Loads images from the dataset directory.
    Expected structure:
    dataset/
      0_Class0/
        img1.png
      1_Class1/
        img2.png
      2_Class2/
        img2.png
      ...
    """
    data_dir = pathlib.Path(DATASET_DIR)
    image_count = len(list(data_dir.glob('*/*.png')))
    print(f"Found {image_count} images.")

    if image_count == 0:
        print(f"ERROR: No images found in {DATASET_DIR}. Please unzip 'robot_dataset.zip' here.")
        exit(1)

    # Use Keras utility to load dataset
    train_ds = tf.keras.utils.image_dataset_from_directory(
        data_dir,
        validation_split=0.2,
        subset="training",
        seed=123,
        image_size=(IMG_HEIGHT, IMG_WIDTH),
        batch_size=BATCH_SIZE,
        color_mode="grayscale", # Important: ESP32 sends grayscale
        label_mode='int' 
    )

    val_ds = tf.keras.utils.image_dataset_from_directory(
        data_dir,
        validation_split=0.2,
        subset="validation",
        seed=123,
        image_size=(IMG_HEIGHT, IMG_WIDTH),
        batch_size=BATCH_SIZE,
        color_mode="grayscale",
        label_mode='int'
    )

    class_names = train_ds.class_names
    print(f"Classes found: {class_names}")
    
    return train_ds, val_ds, class_names

def create_model(num_classes):
    """
    Creates a simple CNN model optimized for 96x96 grayscale images.
    """
    model = models.Sequential([
        layers.Rescaling(1./255, input_shape=(IMG_HEIGHT, IMG_WIDTH, 1)),
        
        # Augmentation (optional, helps with small datasets)
        layers.RandomFlip("horizontal"),
        layers.RandomRotation(0.1),
        layers.RandomZoom(0.1),

        # Convolutional Block 1
        layers.Conv2D(16, 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),

        # Convolutional Block 2
        layers.Conv2D(32, 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),

        # Convolutional Block 3
        layers.Conv2D(64, 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),
        
        layers.Dropout(0.2),

        layers.Flatten(),
        layers.Dense(128, activation='relu'),
        layers.Dense(num_classes) # No Softmax here, raw logits usually better for quantization stability, but softmax is fine too
    ])
    
    model.compile(optimizer='adam',
                  loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
                  metrics=['accuracy'])
    return model

def representative_data_gen():
    """
    Generates data for the quantizer to calibrate the model range.
    """
    data_dir = pathlib.Path(DATASET_DIR)
    # Collect a few images
    img_paths = list(data_dir.glob('*/*.png'))[:100]
    
    for p in img_paths:
        img = tf.keras.preprocessing.image.load_img(p, color_mode="grayscale", target_size=(IMG_HEIGHT, IMG_WIDTH))
        input_data = tf.keras.preprocessing.image.img_to_array(img)
        input_data = np.array(input_data, dtype=np.float32)
        input_data = input_data / 255.0 # Normalize to match model input
        input_data = input_data[np.newaxis, ...] # Add batch dim
        yield [input_data]

def main():
    print("--- 1. Loading Data ---")
    train_ds, val_ds, class_names = load_data()
    
    print("\n--- 2. Training Model ---")
    model = create_model(len(class_names))
    model.summary()
    
    history = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=EPOCHS
    )
    
    print("\n--- 3. Converting to TFLite (Quantized) ---")
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    
    # Enable optimizations
    converter.optimizations = [tf.lite.Optimize.DEFAULT]
    
    # Representative dataset for full integer quantization
    converter.representative_dataset = representative_data_gen
    
    # Ensure input/output are also quantized (INT8)
    converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
    converter.inference_input_type = tf.int8
    converter.inference_output_type = tf.int8
    
    tflite_model = converter.convert()
    
    # Save the model
    with open(MODEL_FILENAME, 'wb') as f:
        f.write(tflite_model)
        
    print(f"\nSUCCESS! Model saved to '{MODEL_FILENAME}'")
    print(f"Size: {len(tflite_model)} bytes")
    print("\nNext steps:")
    print("1. Go to the Web Interface.")
    print(f"2. Upload '{MODEL_FILENAME}' to the robot.")

if __name__ == "__main__":
    main()
