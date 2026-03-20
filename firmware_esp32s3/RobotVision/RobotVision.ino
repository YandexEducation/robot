#include <Arduino.h>
#include <cstring>
#include <Wire.h>
#include "esp_mac.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include "esp_camera.h"
#include "img_converters.h"
#include <LittleFS.h>
#include <esp_heap_caps.h>

// EloquentTinyML 3.x
#include <tflm_esp32.h> 
#include <eloquent_tinyml.h>

#define I2C_DEV_ADDR 0x42


#define BLE_ROBOT_NAME "test"
#define BLE_PASSWORD "test"   // пустая строка "" — без проверки пароля
// Xiao ESP32-S3 Sense: SDA GPIO 5, SCL GPIO 6 (альтернатива: 43/44). ESP32: SDA 21, SCL 22
#define I2C_SDA_PIN 5
#define I2C_SCL_PIN 6

// Camera: OV2640 (2MP) - adjust pins for your board
// Option A: Xiao ESP32-S3 Sense / similar (DVP parallel)
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     10
#define SIOD_GPIO_NUM     40
#define SIOC_GPIO_NUM     39
#define Y9_GPIO_NUM       48
#define Y8_GPIO_NUM       11
#define Y7_GPIO_NUM       12
#define Y6_GPIO_NUM       14
#define Y5_GPIO_NUM       16
#define Y4_GPIO_NUM       18
#define Y3_GPIO_NUM       17
#define Y2_GPIO_NUM       15
#define VSYNC_GPIO_NUM    38
#define HREF_GPIO_NUM     47
#define PCLK_GPIO_NUM     13
// Option B: Standard ESP32-CAM (uncomment if needed):
// #define PWDN_GPIO_NUM     32
// #define RESET_GPIO_NUM    -1
// #define XCLK_GPIO_NUM     0
// #define SIOD_GPIO_NUM     26
// #define SIOC_GPIO_NUM     27
// #define Y9_GPIO_NUM       35
// #define Y8_GPIO_NUM       34
// #define Y7_GPIO_NUM       39
// #define Y6_GPIO_NUM       36
// #define Y5_GPIO_NUM       21
// #define Y4_GPIO_NUM       19
// #define Y3_GPIO_NUM       18
// #define Y2_GPIO_NUM       5
// #define VSYNC_GPIO_NUM    25
// #define HREF_GPIO_NUM     23
// #define PCLK_GPIO_NUM     22

// TFLite Settings: 115x115 Grayscale (стабильная конфигурация по памяти)
#define K_TENSOR_ARENA_SIZE (136 * 1024)
#define MODEL_FILENAME "/model.tflite"
#define INPUT_IMG_W 115
#define INPUT_IMG_H 115
#define INPUT_CHANNELS 1
#define INPUT_SIZE (INPUT_IMG_W * INPUT_IMG_H * INPUT_CHANNELS)
#define OUTPUT_SIZE 3 
#define NUM_OPS 20 

// BLE Stream - 96x96 Grayscale (same as model input)
#define PREVIEW_W 96
#define PREVIEW_H 96
#define PREVIEW_CHANNELS 1
#define PREVIEW_SIZE (PREVIEW_W * PREVIEW_H * PREVIEW_CHANNELS)
#define BLE_STREAM_CHUNK 128   // Smaller chunks = less BLE buffer pressure

// EloquentTinyML Instance
Eloquent::TF::Sequential<NUM_OPS, K_TENSOR_ARENA_SIZE> tf;

// BLE UUIDs
#define SERVICE_UUID           "19B10000-E8F2-537E-4F6C-D104768A1214"
#define CHAR_SCRIPT_UUID       "19B10001-E8F2-537E-4F6C-D104768A1214"
#define CHAR_SIGN_UUID         "19B10002-E8F2-537E-4F6C-D104768A1214"
#define CHAR_IMAGE_UUID        "19B10003-E8F2-537E-4F6C-D104768A1214"
#define CHAR_MODEL_UUID        "19B10004-E8F2-537E-4F6C-D104768A1214"
#define CHAR_STREAM_UUID       "19B10005-E8F2-537E-4F6C-D104768A1214"
#define CHAR_SENSORS_UUID      "19B10006-E8F2-537E-4F6C-D104768A1214"
#define CHAR_AUTH_UUID         "19B10007-E8F2-537E-4F6C-D104768A1214"
// Новый UUID для управления параметрами камеры из браузера, надо допилить сохранение в памяти
#define CHAR_CAMERA_UUID       "19B10008-E8F2-537E-4F6C-D104768A1214"

// Global State
BLEServer* pServer = NULL;
BLECharacteristic* pScriptChar = NULL;
BLECharacteristic* pSignChar = NULL;
BLECharacteristic* pImageChar = NULL;
BLECharacteristic* pModelChar = NULL;
BLECharacteristic* pStreamChar = NULL;
BLECharacteristic* pSensorsChar = NULL;
BLECharacteristic* pAuthChar = NULL;
BLECharacteristic* pCameraChar = NULL;

bool deviceConnected = false;
bool bleAuthenticated = false;
bool stream_active = false;
String scriptBuffer = "";
bool newScriptAvailable = false;
int scriptReadIndex = 0;
#define SIGN_NO_MODEL 0xFF  // When model not loaded
uint8_t currentSign = SIGN_NO_MODEL;
uint8_t currentSignConf = 0;   // 0–100, простая уверенность по "стрику"
uint8_t lastClass = SIGN_NO_MODEL;
uint8_t classStreak = 0;

// I2C Registers
#define REG_STATUS    0x10
#define REG_LEN       0x11
#define REG_DATA      0x12
#define REG_SIGN      0x01
#define REG_SIGN_CONF 0x02
#define REG_SENSORS   0x20
#define REG_HEARTBEAT 0x30
#define SENSORS_LEN 22

volatile uint8_t i2c_register = 0;
unsigned long lastHeartbeatPrint = 0;
unsigned long lastSensorsPrint = 0;
#define HEARTBEAT_PRINT_INTERVAL_MS 2000
#define SENSORS_PRINT_INTERVAL_MS 2000
uint8_t sensorData[SENSORS_LEN];
bool sensorDataValid = false;

bool modelLoaded = false;
uint8_t* model_data = nullptr;
int8_t* ai_input_buf = nullptr;
float* ai_input_float = nullptr;  // For float32 model
#define USE_FLOAT_MODEL 0  // 0=int8 (compact model from train.py), 1=float32
uint8_t* preview_buf_a = nullptr;
uint8_t* preview_buf_b = nullptr;
uint8_t* preview_read = nullptr;
uint8_t* preview_write = nullptr;

// Глобальные настройки камеры (диапазоны OV2640: -2..+2 для яркости/контраста/ae_level)
int8_t cam_brightness = 2;
int8_t cam_contrast   = 1;
int8_t cam_ae_level   = 2;
uint8_t cam_exposure_auto = 1;
uint8_t cam_gain_auto     = 1;
uint8_t cam_whitebal_auto = 1;
sensor_t* g_sensor = nullptr;

unsigned long lastCaptureTime = 0;
// FPS 1 
#define CAPTURE_INTERVAL_MS 1000

// Model Upload State
File uploadFile;
bool isUploadingModel = false;
unsigned long lastUploadTime = 0;

// BLE Server Callbacks
class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
      if (strlen(BLE_PASSWORD) == 0) bleAuthenticated = true;
    };
    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
      bleAuthenticated = false;
      pServer->getAdvertising()->start();
    }
};

// Auth: если пароль пустой — всегда разрешено; иначе нужна успешная AUTH
static uint8_t authResult = 0;

class AuthCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String value = pCharacteristic->getValue();
      if (strlen(BLE_PASSWORD) == 0) {
        bleAuthenticated = true;
        authResult = 1;
      } else if (value == BLE_PASSWORD) {
        bleAuthenticated = true;
        authResult = 1;
        Serial.println("BLE Auth OK");
      } else {
        bleAuthenticated = false;
        authResult = 0;
        Serial.println("BLE Auth FAIL");
      }
    }
    void onRead(BLECharacteristic *pCharacteristic) {
      pCharacteristic->setValue(&authResult, 1);
    }
};

// Script Characteristic Callbacks
class ScriptCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      if (strlen(BLE_PASSWORD) > 0 && !bleAuthenticated) return;
      String value = pCharacteristic->getValue();
      if (value == "START") {
        scriptBuffer = "";
        newScriptAvailable = false;
        scriptReadIndex = 0;
        Serial.println("Starting Script Upload");
      } else if (value == "EOF") {
        newScriptAvailable = true;
        scriptReadIndex = 0;
        Serial.printf("Script Upload Finished, %d bytes. ESP32 will fetch via I2C.\n", (int)scriptBuffer.length());
      } else {
        scriptBuffer += value;
      }
    }
};

// Sign Characteristic Callbacks
class SignCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String value = pCharacteristic->getValue();
      if (value.length() > 0) {
        currentSign = value[0];
      }
    }
    void onRead(BLECharacteristic *pCharacteristic) {
      uint8_t buf[2] = { currentSign, currentSignConf };
      pCharacteristic->setValue(buf, 2);
    }
};

// Model Characteristic Callbacks (model upload is USB-only; BLE kept for compatibility)
class ModelCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String value = pCharacteristic->getValue();
      size_t len = value.length();
      if (len == 0) return;
      const uint8_t* data = (const uint8_t*)value.c_str();
      if (len >= 5 && memcmp(data, "START", 5) == 0) {
        Serial.println("Starting Model Upload...");
        isUploadingModel = true;
        LittleFS.remove(MODEL_FILENAME);
        uploadFile = LittleFS.open(MODEL_FILENAME, FILE_WRITE);
        if (!uploadFile) Serial.println("Failed to open file for writing");
      }
      else if (len >= 3 && memcmp(data, "EOF", 3) == 0) {
        Serial.println("Model Upload Finished!");
        if (uploadFile) uploadFile.close();
        isUploadingModel = false;
        ESP.restart();
      }
      else {
        if (isUploadingModel && uploadFile) {
          uploadFile.write(data, len);
          lastUploadTime = millis();
        }
      }
    }
};

// Stream control via IMAGE char write: "S1" = start, "S0" = stop (Bobot-style)
class ImageCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String raw = pCharacteristic->getValue();
      if (raw.length() >= 2 && raw[0] == 'S') {
        if (raw[1] == '1') { stream_active = true; Serial.println("Stream ON"); }
        else if (raw[1] == '0') { stream_active = false; Serial.println("Stream OFF"); }
      }
    }
};

// Sensors: value updated by I2C from ESP32, exposed on read
class SensorsCallbacks: public BLECharacteristicCallbacks {
    void onRead(BLECharacteristic *pCharacteristic) {
      pCharacteristic->setValue(sensorData, SENSORS_LEN);
    }
};

// Управление параметрами камеры через BLE (чтение/запись из браузера)
// Формат значения: 6 байт
// [0] int8  brightness (-2..+2)
// [1] int8  contrast   (-2..+2)
// [2] int8  ae_level   (-2..+2)
// [3] uint8 exposure_auto (0/1)
// [4] uint8 gain_auto     (0/1)
// [5] uint8 whitebal_auto (0/1)
void applyCameraSettings();
class CameraCallbacks: public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *pCharacteristic) {
      String value = pCharacteristic->getValue();
      if (value.length() < 6) return;
      cam_brightness     = (int8_t)value[0];
      cam_contrast       = (int8_t)value[1];
      cam_ae_level       = (int8_t)value[2];
      cam_exposure_auto  = value[3] ? 1 : 0;
      cam_gain_auto      = value[4] ? 1 : 0;
      cam_whitebal_auto  = value[5] ? 1 : 0;
      applyCameraSettings();
    }
    void onRead(BLECharacteristic *pCharacteristic) {
      uint8_t buf[6];
      buf[0] = (uint8_t)cam_brightness;
      buf[1] = (uint8_t)cam_contrast;
      buf[2] = (uint8_t)cam_ae_level;
      buf[3] = cam_exposure_auto;
      buf[4] = cam_gain_auto;
      buf[5] = cam_whitebal_auto;
      pCharacteristic->setValue(buf, 6);
    }
};

void setupCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;   // OV2640: 10–20 MHz typical
  config.frame_size = FRAMESIZE_240X240;
  config.pixel_format = PIXFORMAT_GRAYSCALE;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 5;
  config.fb_count = 1;

  if(esp_camera_init(&config) != ESP_OK) {
    Serial.println("Camera Init Failed (PSRAM), retrying with DRAM...");
    config.fb_location = CAMERA_FB_IN_DRAM;
    if(esp_camera_init(&config) != ESP_OK) {
      Serial.println("Camera Init Failed");
      return;
    }
  }
  g_sensor = esp_camera_sensor_get();
  if (g_sensor) {
    g_sensor->set_hmirror(g_sensor, 0);
    g_sensor->set_vflip(g_sensor, 0);
    // Инициализация по умолчанию; реальные значения берутся из глобальных cam_*
    g_sensor->set_saturation(g_sensor, 0);        // neutral (grayscale ignores)
    g_sensor->set_gainceiling(g_sensor, GAINCEILING_16X);    // 16x gain (better low light)
  }
  Serial.println("Camera Init Success");
}

// Применение текущих глобальных настроек cam_* к сенсору OV2640
void applyCameraSettings() {
  if (!g_sensor) return;
  // Ограничиваем значения к допустимым диапазонам OV2640 (-2..+2)
  if (cam_brightness < -2) cam_brightness = -2;
  if (cam_brightness >  2) cam_brightness =  2;
  if (cam_contrast   < -2) cam_contrast   = -2;
  if (cam_contrast   >  2) cam_contrast   =  2;
  if (cam_ae_level   < -2) cam_ae_level   = -2;
  if (cam_ae_level   >  2) cam_ae_level   =  2;

  g_sensor->set_brightness(g_sensor, cam_brightness);
  g_sensor->set_contrast(g_sensor, cam_contrast);
  g_sensor->set_ae_level(g_sensor, cam_ae_level);
  g_sensor->set_whitebal(g_sensor, cam_whitebal_auto ? 1 : 0);
  g_sensor->set_exposure_ctrl(g_sensor, cam_exposure_auto ? 1 : 0);
  g_sensor->set_gain_ctrl(g_sensor, cam_gain_auto ? 1 : 0);
  Serial.printf("Camera settings applied: br=%d ct=%d ae=%d auto_exp=%d auto_gain=%d awb=%d\n",
                (int)cam_brightness, (int)cam_contrast, (int)cam_ae_level,
                (int)cam_exposure_auto, (int)cam_gain_auto, (int)cam_whitebal_auto);
}

void loadModelFromFS() {
  Serial.println("Loading Model...");
  
  if (!LittleFS.exists(MODEL_FILENAME)) {
      Serial.println("Model file does not exist. Upload via USB: python tools/upload_model.py model.tflite");
      return;
  }

  File file = LittleFS.open(MODEL_FILENAME, "r");
  if (!file) { Serial.println("Failed to open model file."); return; }
  
  size_t modelSize = file.size();
  Serial.printf("Loading model size: %d bytes\n", modelSize);
  
  if (modelSize == 0) {
      Serial.println("Model file is empty! Close Serial Monitor, run: python tools/upload_model.py model.tflite");
      file.close();
      LittleFS.remove(MODEL_FILENAME);  // Remove bad file so next upload can succeed
      return;
  }
  if (modelSize < 5000) { Serial.printf("WARN: Model too small (%d bytes), may be corrupted.\n", (int)modelSize); }

  if (model_data) { free(model_data); model_data = nullptr; }
  model_data = (uint8_t*)ps_malloc(modelSize); 
  if (!model_data) { model_data = (uint8_t*)malloc(modelSize); }
  if (!model_data) { Serial.println("Failed to allocate memory."); file.close(); return; }

  file.read(model_data, modelSize);
  file.close();

  Serial.println("Initializing TFLite...");
  tf.setNumInputs(INPUT_SIZE);
  tf.setNumOutputs(OUTPUT_SIZE);
  // Add each op ONCE (duplicate AddBuiltin causes "same op more than once" error)
  tf.resolver.AddConv2D();
  tf.resolver.AddMaxPool2D();
  tf.resolver.AddReshape();
  tf.resolver.AddFullyConnected();
  tf.resolver.AddSoftmax();
  tf.resolver.AddQuantize();
  tf.resolver.AddDequantize();

  if (!tf.begin(model_data).isOk()) {
      Serial.print("TF Begin Failed: ");
      Serial.println(tf.exception.toString());
      Serial.println("Check: model ops match resolver, file not corrupted.");
      return;
  }

  modelLoaded = true;
  currentSign = 0;  // Reset to Class0 until first inference
  Serial.println("Model Loaded Successfully!");
}

// I2C Callbacks (ESP32 writeto_mem sends: reg byte + data bytes)
void onI2CReceive(int len) {
  if (len < 1 || !Wire.available()) return;
  uint8_t reg = Wire.read();
  len--;
  if (reg == REG_SENSORS && len >= SENSORS_LEN) {
    for (int i = 0; i < SENSORS_LEN && Wire.available(); i++) {
      sensorData[i] = Wire.read();
    }
    sensorDataValid = true;
    unsigned long now = millis();
    if (now - lastSensorsPrint >= SENSORS_PRINT_INTERVAL_MS) {
      lastSensorsPrint = now;
      int lineC = sensorData[4] | (sensorData[5] << 8);
      Serial.printf("I2C: sensors (line_c=%d)\n", lineC);
    }
  } else if (reg == REG_STATUS && len >= 1 && Wire.read() == 0) {
    newScriptAvailable = false;
    Serial.println("I2C: ESP32 ack script received");
  } else if (reg == REG_HEARTBEAT && len >= 1) {
    Wire.read();  // consume byte
    unsigned long now = millis();
    if (now - lastHeartbeatPrint >= HEARTBEAT_PRINT_INTERVAL_MS) {
      lastHeartbeatPrint = now;
      Serial.println("I2C: ESP32 heartbeat");
    }
  } else {
    i2c_register = reg;
  }
}

void onI2CRequest() {
  if (i2c_register == REG_SIGN) {
    Wire.write(currentSign);
  } else if (i2c_register == REG_STATUS) {
    Wire.write(newScriptAvailable ? 1 : 0);
  } else if (i2c_register == REG_SIGN_CONF) {
    Wire.write(currentSignConf);
  } else if (i2c_register == REG_LEN) {
    int remaining = scriptBuffer.length() - scriptReadIndex;
    int len = min(16, remaining);  // 16 байт — меньше нагрузка на I2C
    Wire.write(len);
  } else if (i2c_register == REG_DATA) {
    int remaining = scriptBuffer.length() - scriptReadIndex;
    int len = min(16, remaining);
    if (len > 0) {
      const char* ptr = scriptBuffer.c_str() + scriptReadIndex;
      Wire.write((const uint8_t*)ptr, len);
      scriptReadIndex += len;
    }
  }
}

// Image enhancement for grayscale
// OV2640: sensor tuned via set_brightness/ae_level; soft stretch
#define CONTRAST_GAIN 140
#define BRIGHTNESS_OFF 18    // OV2640: boost for dark environments
#define BLACK_LEVEL 16       // lower = more detail in shadows
#define WHITE_LEVEL 240

static inline uint8_t enhance(uint8_t v) {
    int x = ((int)v - BLACK_LEVEL) * 255 / (WHITE_LEVEL - BLACK_LEVEL);
    if (x < 0) x = 0;
    if (x > 255) x = 255;
    x = (x - 128) * CONTRAST_GAIN / 100 + 128 + BRIGHTNESS_OFF;
    if (x < 0) return 0;
    if (x > 255) return 255;
    return (uint8_t)x;
}

// 240x240 Grayscale -> 96x96 Grayscale for BLE stream
void gray240To96(const uint8_t* src, uint8_t* dst) {
    const int srcW = 240, dstW = 96;
    for (int y = 0; y < dstW; y++) {
        for (int x = 0; x < dstW; x++) {
            int sy = (y * srcW) / dstW, sx = (x * srcW) / dstW;
            uint8_t g = src[sy * srcW + sx];
            dst[y * dstW + x] = enhance(g);
        }
    }
}

// 240x240 Grayscale -> INPUT_IMG_W x INPUT_IMG_W int8 for AI
// Сейчас INPUT_IMG_W = 160, так что фактически downscale 240x240 -> 160x160
void gray240ToInput_int8(const uint8_t* src, int8_t* dst) {
    const int srcW = 240, dstW = INPUT_IMG_W;
    for (int y = 0; y < dstW; y++) {
        for (int x = 0; x < dstW; x++) {
            int sy = (y * srcW) / dstW, sx = (x * srcW) / dstW;
            int eg = enhance(src[sy * srcW + sx]);
            dst[y * dstW + x] = (int8_t)(eg - 128);
        }
    }
}

// Serial model upload: receives 4-byte size + data, writes to LittleFS. Returns true if done.
bool runSerialModelUpload() {
  Serial.println("RX_START");
  Serial.flush();
  Serial.setTimeout(5000);  // 5s for size, 2s for data chunks
  uint8_t szBuf[4];
  if (Serial.readBytes(szBuf, 4) != 4) { Serial.println("ERR:size"); return false; }
  uint32_t fsize = (uint32_t)szBuf[0] | ((uint32_t)szBuf[1]<<8) | ((uint32_t)szBuf[2]<<16) | ((uint32_t)szBuf[3]<<24);
  Serial.printf("RX_SIZE:%lu\n", (unsigned long)fsize);
  Serial.flush();
  if (fsize == 0) { Serial.println("ERR:size=0"); return false; }
  if (fsize > 2000000) { Serial.println("ERR:too big"); return false; }
  size_t freeBytes = LittleFS.totalBytes() - LittleFS.usedBytes();
  if (freeBytes < fsize + 10000) { Serial.printf("ERR:no space (need %lu, free %u)\n", (unsigned long)fsize, (unsigned)freeBytes); return false; }
  LittleFS.remove(MODEL_FILENAME);
  File f = LittleFS.open(MODEL_FILENAME, "w");
  if (!f) { Serial.println("ERR:file"); return false; }
  // Signal host: ready for data (host waits for RX_SIZE before sending)
  Serial.println("SEND");
  Serial.flush();
  delay(100);  // Let host receive SEND before we start reading
  Serial.setTimeout(3000);
  uint8_t buf[128];  // Smaller reads = less blocking, more resilient to USB timing
  uint32_t done = 0;
  unsigned long lastProgress = millis();
  while (done < fsize) {
    size_t toRead = min((size_t)(fsize - done), (size_t)128);
    size_t n = Serial.readBytes(buf, toRead);
    if (n == 0) {
      if (millis() - lastProgress > 45000) {
        Serial.printf("ERR:timeout at %lu/%lu\n", (unsigned long)done, (unsigned long)fsize);
        f.close();
        LittleFS.remove(MODEL_FILENAME);
        return false;
      }
      delay(10);
      continue;
    }
    lastProgress = millis();
    size_t written = f.write(buf, n);
    if (written != n) { Serial.println("ERR:write"); f.close(); return false; }
    done += n;
    delay(0);  // yield for watchdog, minimal delay
  }
  f.close();
  Serial.printf("OK (wrote %lu bytes)\n", (unsigned long)done);
  Serial.flush();
  return true;
}

// Serial script upload: 4-byte size LE + data -> scriptBuffer, newScriptAvailable = true (ESP32 fetches via I2C)
#define MAX_SCRIPT_SIZE 60000
bool runSerialScriptUpload() {
  Serial.setTimeout(3000);
  uint8_t szBuf[4];
  if (Serial.readBytes(szBuf, 4) != 4) { Serial.println("ERR:size"); return false; }
  uint32_t fsize = (uint32_t)szBuf[0] | ((uint32_t)szBuf[1] << 8) | ((uint32_t)szBuf[2] << 16) | ((uint32_t)szBuf[3] << 24);
  if (fsize == 0) { Serial.println("ERR:size=0"); return false; }
  if (fsize > MAX_SCRIPT_SIZE) { Serial.println("ERR:too big"); return false; }
  scriptBuffer = "";
  scriptBuffer.reserve(fsize);
  uint8_t buf[128];
  uint32_t done = 0;
  unsigned long lastProgress = millis();
  while (done < fsize) {
    size_t toRead = min((size_t)(fsize - done), (size_t)128);
    size_t n = Serial.readBytes(buf, toRead);
    if (n == 0) {
      if (millis() - lastProgress > 15000) {
        Serial.println("ERR:timeout");
        return false;
      }
      delay(10);
      continue;
    }
    lastProgress = millis();
    for (size_t i = 0; i < n; i++) scriptBuffer += (char)buf[i];
    done += n;
    delay(0);
  }
  newScriptAvailable = true;
  scriptReadIndex = 0;
  Serial.printf("OK (script %lu bytes)\n", (unsigned long)done);
  Serial.flush();
  return true;
}

void setup() {
  Serial.begin(460800);  
  delay(500);
  
  if (!LittleFS.begin(true)) {
    Serial.println("LittleFS Mount Failed");
    while(1) delay(1000);
  }
  
  Serial.println("--- Booting RobotVision ---");
  Serial.printf("Free heap: %u, Free PSRAM: %u\n", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());
  Serial.println("Send UPLOAD_MODEL over Serial to upload model via USB");
  
  if (USE_FLOAT_MODEL) {
    ai_input_float = (float*)ps_malloc(INPUT_SIZE * sizeof(float));
    if (!ai_input_float) ai_input_float = (float*)heap_caps_malloc(INPUT_SIZE * sizeof(float), MALLOC_CAP_INTERNAL);
  } else {
    ai_input_buf = (int8_t*)ps_malloc(INPUT_SIZE);
    if (!ai_input_buf) ai_input_buf = (int8_t*)heap_caps_malloc(INPUT_SIZE, MALLOC_CAP_INTERNAL);
  }
  preview_buf_a = (uint8_t*)ps_malloc(PREVIEW_SIZE);
  if (!preview_buf_a) preview_buf_a = (uint8_t*)heap_caps_malloc(PREVIEW_SIZE, MALLOC_CAP_INTERNAL);
  preview_buf_b = (uint8_t*)ps_malloc(PREVIEW_SIZE);
  if (!preview_buf_b) preview_buf_b = (uint8_t*)heap_caps_malloc(PREVIEW_SIZE, MALLOC_CAP_INTERNAL);
  if (preview_buf_a) preview_read = preview_buf_a;
  if (preview_buf_b) preview_write = preview_buf_b;
  if (!preview_buf_a || !preview_buf_b) Serial.println("CRITICAL: Preview buffer failed!");
  else if (USE_FLOAT_MODEL && !ai_input_float) Serial.println("WARN: AI float buffer failed");
  else if (!USE_FLOAT_MODEL && !ai_input_buf) Serial.println("WARN: AI input buffer failed");
  else Serial.println("Buffers allocated in PSRAM");
  
  Serial.printf("LittleFS Mounted (free: %u bytes)\n", (unsigned)(LittleFS.totalBytes() - LittleFS.usedBytes()));
  
  setupCamera();
  // Применяем исходные значения cam_* к сенсору
  applyCameraSettings();
  loadModelFromFS();
  
  Serial.println("Initializing I2C...");
  Wire.begin((uint8_t)I2C_DEV_ADDR, I2C_SDA_PIN, I2C_SCL_PIN, 100000);
  Wire.onReceive(onI2CReceive);
  Wire.onRequest(onI2CRequest);

  Serial.println("Initializing BLE...");
  // костыль с изменением мака по имени робота, mac кэширует маки 
  {
    uint8_t mac[6] = {0x24, 0x6F, 0x28, 0x00, 0x00, 0x00};
    uint16_t h = 0;
    for (const char* p = BLE_ROBOT_NAME; *p; p++) h = h * 31 + (uint8_t)*p;
    mac[4] = (h >> 8) & 0xFF;
    mac[5] = h & 0xFF;
    esp_base_mac_addr_set(mac);
  }
  BLEDevice::init(BLE_ROBOT_NAME);
  BLEDevice::setMTU(256);  // Lower MTU = less BLE buffer allocation
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());
  BLEService *pService = pServer->createService(SERVICE_UUID);

  pScriptChar = pService->createCharacteristic(CHAR_SCRIPT_UUID, BLECharacteristic::PROPERTY_WRITE);
  pScriptChar->setCallbacks(new ScriptCallbacks());

  pSignChar = pService->createCharacteristic(CHAR_SIGN_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_READ);
  pSignChar->setCallbacks(new SignCallbacks());

  pModelChar = pService->createCharacteristic(CHAR_MODEL_UUID, BLECharacteristic::PROPERTY_WRITE);
  pModelChar->setCallbacks(new ModelCallbacks());

  pImageChar = pService->createCharacteristic(CHAR_IMAGE_UUID, BLECharacteristic::PROPERTY_WRITE);
  pImageChar->setCallbacks(new ImageCallbacks());

  pStreamChar = pService->createCharacteristic(CHAR_STREAM_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  pStreamChar->addDescriptor(new BLE2902());

  pSensorsChar = pService->createCharacteristic(CHAR_SENSORS_UUID, BLECharacteristic::PROPERTY_READ);
  pSensorsChar->setCallbacks(new SensorsCallbacks());

  pAuthChar = pService->createCharacteristic(CHAR_AUTH_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_READ);
  pAuthChar->setCallbacks(new AuthCallbacks());

  // Характеристика для управления параметрами камеры
  pCameraChar = pService->createCharacteristic(CHAR_CAMERA_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_WRITE);
  pCameraChar->setCallbacks(new CameraCallbacks());

  pService->start();
  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  // Web Bluetooth фильтрует по UUID — без него робот не виден в браузере.
  pAdvertising->setScanResponse(true);
  pAdvertising->setName(BLE_ROBOT_NAME);
  pAdvertising->setMinPreferred(0x0);
  BLEDevice::startAdvertising();
  Serial.println("BLE Ready");
}

// Handle Serial PREVIEW command: send "PREV" + 1 byte (detect) + 4 bytes size LE + image data
#define PREVIEW_MAGIC "PREV"
void handleSerialPreview() {
    if (!preview_read) return;
    Serial.write((const uint8_t*)PREVIEW_MAGIC, 4);
    Serial.write((uint8_t)currentSign);
    uint32_t sz = PREVIEW_SIZE;
    Serial.write((uint8_t)(sz & 0xFF));
    Serial.write((uint8_t)((sz >> 8) & 0xFF));
    Serial.write((uint8_t)((sz >> 16) & 0xFF));
    Serial.write((uint8_t)((sz >> 24) & 0xFF));
    Serial.write(preview_read, PREVIEW_SIZE);
}

void loop() {
    // Serial commands: UPLOAD_MODEL, PREVIEW
    static String serialCmd = "";
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n' || c == '\r') {
        if (serialCmd == "UPLOAD_MODEL") {
          Serial.println("READY");  // Client sends 4-byte size LE, then binary data
          if (runSerialModelUpload()) {
            Serial.println("Restarting...");
            delay(500);
            ESP.restart();
          }
        } else if (serialCmd == "UPLOAD_SCRIPT") {
          Serial.println("READY");
          Serial.flush();
          runSerialScriptUpload();
        } else if (serialCmd == "PREVIEW") {
          handleSerialPreview();
        } else if (serialCmd == "DETECT") {
          const char* labels[] = {"Class0", "Class1", "Class2"};
          if (currentSign == SIGN_NO_MODEL)
            Serial.println("DETECT: (model not loaded)");
          else if (currentSign < OUTPUT_SIZE)
            Serial.printf("DETECT: %d (%s)\n", (int)currentSign, labels[currentSign]);
          else
            Serial.printf("DETECT: %d (?)\n", (int)currentSign);
        } else if (serialCmd == "MODEL_STATUS") {
          Serial.printf("modelLoaded=%d, file exists=%d\n", (int)modelLoaded, (int)LittleFS.exists(MODEL_FILENAME));
          if (LittleFS.exists(MODEL_FILENAME)) {
            File f = LittleFS.open(MODEL_FILENAME, "r");
            Serial.printf("model size=%d bytes\n", (int)f.size());
            f.close();
          }
        } else if (serialCmd == "CAM_GET") {
          uint8_t camBuf[6] = { (uint8_t)cam_brightness, (uint8_t)cam_contrast, (uint8_t)cam_ae_level,
                                cam_exposure_auto, cam_gain_auto, cam_whitebal_auto };
          Serial.write(camBuf, 6);
        } else if (serialCmd == "CAM_SET") {
          uint8_t camBuf[6];
          Serial.setTimeout(500);
          if (Serial.readBytes(camBuf, 6) == 6) {
            cam_brightness = (int8_t)camBuf[0];
            cam_contrast   = (int8_t)camBuf[1];
            cam_ae_level   = (int8_t)camBuf[2];
            cam_exposure_auto = camBuf[3] ? 1 : 0;
            cam_gain_auto     = camBuf[4] ? 1 : 0;
            cam_whitebal_auto = camBuf[5] ? 1 : 0;
            applyCameraSettings();
            Serial.println("OK");
          } else {
            Serial.println("ERR:timeout");
          }
        } else if (serialCmd == "SIGN") {
          uint8_t buf[2] = { currentSign, currentSignConf };
          Serial.write(buf, 2);
        } else if (serialCmd == "SENSORS") {
          Serial.write(sensorData, SENSORS_LEN);
        } else if (serialCmd == "STREAM_ON") {
          stream_active = true;
          Serial.println("OK");
        } else if (serialCmd == "STREAM_OFF") {
          stream_active = false;
          Serial.println("OK");
        }
        serialCmd = "";
      } else if (serialCmd.length() < 32) serialCmd += c;
    }
    
    if (isUploadingModel) {
        delay(50);
        return;
    }
    
    if (!preview_read || !preview_write) {
        delay(100);
        return;
    }

    // Capture a frame periodically
    unsigned long now = millis();
    if (now - lastCaptureTime < CAPTURE_INTERVAL_MS) {
        delay(50);
        return;
    }

    camera_fb_t * fb = esp_camera_fb_get();
    if (!fb) {
        delay(50);
        return;
    }
    if (fb->len < 240 * 240) {  // Grayscale = 1 byte/pixel
        esp_camera_fb_return(fb);
        delay(50);
        return;
    }
    gray240To96(fb->buf, preview_write);
    if (ai_input_buf) gray240ToInput_int8(fb->buf, ai_input_buf);
    esp_camera_fb_return(fb);
    uint8_t* tmp = preview_read;
    preview_read = preview_write;
    preview_write = tmp;
    lastCaptureTime = now;

    // AI Inference: 96x96 grayscale (model input)
    if (modelLoaded && ai_input_buf) {
        if (tf.predict(ai_input_buf).isOk()) {
            uint8_t cls = tf.classification;
            // Временная фильтрация: считаем, сколько кадров подряд один и тот же класс
            if (cls == lastClass) {
                if (classStreak < 15) classStreak++;   // накапливаем до 15 кадров
            } else {
                lastClass = cls;
                classStreak = 1;
            }
            currentSign = cls;
            // Конвертируем длину «стрику» в процент уверенности:
            //  - 1 кадр  → 10%
            //  - ...
            //  - 10 кадров подряд → 100%
            // (максимум 100, даже если стрик больше)
            uint8_t conf = classStreak * 10;
            if (conf > 100) conf = 100;
            currentSignConf = conf;
        }
    }

    // BLE Stream: JPEG push (Bobot-style) - only when connected and requested
    if (deviceConnected && stream_active && pStreamChar) {
        uint8_t* jpg_buf = NULL;
        size_t jpg_len = 0;
        if (fmt2jpg(preview_read, PREVIEW_SIZE, PREVIEW_W, PREVIEW_H, PIXFORMAT_GRAYSCALE, 90, &jpg_buf, &jpg_len)) {
            for (size_t i = 0; i < jpg_len; i += BLE_STREAM_CHUNK) {
                if (!deviceConnected) break;
                size_t s = (jpg_len - i > BLE_STREAM_CHUNK) ? BLE_STREAM_CHUNK : (jpg_len - i);
                pStreamChar->setValue(jpg_buf + i, s);
                pStreamChar->notify();
                delay(10);
            }
            free(jpg_buf);
        }
    }

    delay(10); 
}
