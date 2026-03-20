import os
import io
import contextlib
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
import numpy as np
import tensorflow as tf
tf.get_logger().setLevel('ERROR')
try:
    import absl.logging
    absl.logging.set_verbosity(absl.logging.ERROR)
except Exception:
    pass
from tensorflow import keras
from tensorflow.keras import layers, models
import pathlib

# --- CONFIGURATION: 115x115 Grayscale (под исходную стабильную прошивку ESP32‑S3) ---
# Камера на S3 даёт модели 115x115 grayscale (см. firmware_esp32s3/RobotVision.ino: INPUT_IMG_W/H = 115).
IMG_HEIGHT = 115
IMG_WIDTH = 115
BATCH_SIZE = 32
EPOCHS = 30
DATASET_DIR = "dataset"
MODEL_FILENAME = "model.tflite"
# Compact model fits in ~250KB LittleFS (ESP32-S3). Int8 quantization required.
USE_COMPACT_MODEL = True
USE_INT8_QUANT = True

def _log(msg):
    print(msg, flush=True)

def load_data():
    data_dir = pathlib.Path(DATASET_DIR)
    image_count = len(list(data_dir.glob('*/*.png'))) + len(list(data_dir.glob('*/*.jpg')))
    _log(f"Found {image_count} images in {DATASET_DIR}")

    if image_count == 0:
        raise Exception("No images found! Check dataset structure.")

    train_ds = tf.keras.utils.image_dataset_from_directory(
        data_dir,
        validation_split=0.2,
        subset="training",
        seed=123,
        image_size=(IMG_HEIGHT, IMG_WIDTH),
        batch_size=BATCH_SIZE,
        color_mode="grayscale",
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
    # Normalize to 0-1 (model expects this; firmware does same)
    normalize = lambda x, y: (tf.cast(x, tf.float32) / 255.0, y)
    train_ds = train_ds.map(normalize, num_parallel_calls=tf.data.AUTOTUNE)
    val_ds = val_ds.map(normalize, num_parallel_calls=tf.data.AUTOTUNE)
    # Augmentation in data pipeline (not in model - avoids unsupported TFLite ops)
    data_aug = tf.keras.Sequential([
        layers.RandomFlip("horizontal"),
        layers.RandomRotation(0.1),
    ])
    train_ds = train_ds.map(lambda x, y: (data_aug(x, training=True), y), num_parallel_calls=tf.data.AUTOTUNE)
    return train_ds, val_ds, class_names

def create_model(num_classes, batch_size=None, compact=False):
    """Simple CNN - ops must match firmware resolver. Use Reshape (not Flatten) to avoid SHAPE op.
    compact=True: 115x115 grayscale -> 14x14x16=3136 (очень компактная сеть под 136KB arena)."""
    if compact:
        # Сильно ужатая модель: Conv 4->8->16: 115->57->28->14, flat_size=14*14*16=3136
        flat_size = 14 * 14 * 16
        filters = [4, 8, 16]
        dense_units = 32
    else:
        flat_size = 12 * 12 * 64  # 9216 for 96x96
        filters = [16, 32, 64]
        dense_units = 128
    inp = layers.Input(shape=(IMG_HEIGHT, IMG_WIDTH, 1), batch_size=batch_size)
    model = models.Sequential([
        inp,
        layers.Conv2D(filters[0], 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),
        layers.Conv2D(filters[1], 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),
        layers.Conv2D(filters[2], 3, padding='same', activation='relu'),
        layers.MaxPooling2D(),
        layers.Reshape((flat_size,)),
        layers.Dense(dense_units, activation='relu'),
        layers.Dense(num_classes)
    ])
    model.compile(optimizer=keras.optimizers.Adam(learning_rate=1e-3),
                  loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
                  metrics=['accuracy'])
    return model

def representative_data_gen():
    """Yield (1, H, W, 1) float32 - must be 4D for Conv2D."""
    data_dir = pathlib.Path(DATASET_DIR)
    img_paths = list(data_dir.glob('*/*.png')) + list(data_dir.glob('*/*.jpg'))
    img_paths = img_paths[:100]
    if not img_paths:
        # Fallback: synthetic data with correct 4D shape
        for _ in range(10):
            yield [np.zeros((1, IMG_HEIGHT, IMG_WIDTH, 1), dtype=np.float32)]
        return
    for p in img_paths:
        img = tf.keras.preprocessing.image.load_img(p, color_mode="grayscale", target_size=(IMG_HEIGHT, IMG_WIDTH))
        input_data = tf.keras.preprocessing.image.img_to_array(img)
        # Ensure (H, W, 1) then add batch -> (1, H, W, 1)
        if input_data.ndim == 2:
            input_data = np.expand_dims(input_data, axis=-1)
        input_data = np.array(input_data, dtype=np.float32) / 255.0
        input_data = input_data[np.newaxis, ...]  # (1, 128, 128, 1)
        assert input_data.ndim == 4, f"Expected 4D, got {input_data.ndim}D"
        yield [input_data]

def run_training():
    _log("--- 1. Loading Data ---")
    train_ds, val_ds, class_names = load_data()
    
    _log("--- 2. Training Model ---")
    model = create_model(len(class_names), batch_size=None, compact=USE_COMPACT_MODEL)
    # Balance classes if dataset is imbalanced
    class_weight_dict = None
    try:
        labels_flat = np.concatenate([y for x, y in train_ds], axis=0)
        n_classes = len(class_names)
        counts = np.bincount(labels_flat, minlength=n_classes)
        total = len(labels_flat)
        class_weight_dict = {i: total / (n_classes * max(1, c)) for i, c in enumerate(counts)}
        _log("Class weights: " + str(class_weight_dict))
    except Exception as e:
        _log("Could not compute class weights: " + str(e))
    fit_kw = {"epochs": EPOCHS, "verbose": 0}
    if class_weight_dict:
        fit_kw["class_weight"] = class_weight_dict
    _log(f"Training {EPOCHS} epochs...")
    model.fit(train_ds, validation_data=val_ds, **fit_kw)
    
    _log("--- 3. Converting to TFLite ---")
    export_model = create_model(len(class_names), batch_size=1, compact=USE_COMPACT_MODEL)
    export_model.set_weights(model.get_weights())
    # Suppress TF C++ (W0000/I0000) - they write to fd 2 directly
    buf = io.StringIO()
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(2)
    try:
        os.dup2(devnull, 2)
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            if USE_INT8_QUANT:
                converter = tf.lite.TFLiteConverter.from_keras_model(export_model)
                converter.optimizations = [tf.lite.Optimize.DEFAULT]
                converter.representative_dataset = representative_data_gen
                converter.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
                converter.inference_input_type = tf.int8
                converter.inference_output_type = tf.int8
                # Legacy quantizer avoids 5D input bug in some TF versions
                try:
                    converter.experimental_new_quantizer = False
                except AttributeError:
                    pass
                tflite_model = converter.convert()
            else:
                converter = tf.lite.TFLiteConverter.from_keras_model(export_model)
                tflite_model = converter.convert()
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull)
        os.close(old_stderr)
    
    model_size = len(tflite_model)
    _log(f"Exported {'int8 quantized' if USE_INT8_QUANT else 'float32'} model ({model_size} bytes)")
    # Verify input shape is 4D (batch, H, W, C) - required for Conv2D
    interp = tf.lite.Interpreter(model_content=tflite_model)
    interp.allocate_tensors()
    inp = interp.get_input_details()[0]
    shape = inp['shape']
    if len(shape) != 4:
        raise RuntimeError(f"Model input must be 4D (batch,H,W,C), got {len(shape)}D: {shape}")
    _log(f"Input shape OK: {list(shape)}")
    if model_size > 1400000:
        _log(f"WARN: Model may not fit on LittleFS. Check partition scheme.")
    with open(MODEL_FILENAME, 'wb') as f:
        f.write(tflite_model)
    _log("Training Complete.")

if __name__ == "__main__":
    run_training()
