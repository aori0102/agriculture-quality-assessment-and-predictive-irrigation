
// #include <Arduino.h>
// #include "esp_camera.h"
// #include <cstdlib>
// #include <cstring>

// // =====================================================
// // AI-THINKER ESP32-CAM - OV2640 PIN CONFIGURATION
// // =====================================================

// #define CAMERA_PIN_PWDN     32
// #define CAMERA_PIN_RESET    -1
// #define CAMERA_PIN_XCLK      0
// #define CAMERA_PIN_SIOD     26
// #define CAMERA_PIN_SIOC     27

// #define CAMERA_PIN_Y9       35
// #define CAMERA_PIN_Y8       34
// #define CAMERA_PIN_Y7       39
// #define CAMERA_PIN_Y6       36
// #define CAMERA_PIN_Y5       21
// #define CAMERA_PIN_Y4       19
// #define CAMERA_PIN_Y3       18
// #define CAMERA_PIN_Y2        5

// #define CAMERA_PIN_VSYNC    25
// #define CAMERA_PIN_HREF     23
// #define CAMERA_PIN_PCLK     22

// // =====================================================
// // UART CONFIGURATION
// // =====================================================

// #define UART_RX_PIN         13
// #define UART_TX_PIN         14

// constexpr uint32_t UART_BAUD_RATE = 921600;

// // UART1: ESP32-CAM <-> ESP32-WROOM
// HardwareSerial cameraSerial(1);

// // =====================================================
// // CAMERA CONFIGURATION
// // =====================================================

// constexpr size_t MAX_IMAGE_SIZE = 256 * 1024;

// constexpr framesize_t CAMERA_FRAME_SIZE = FRAMESIZE_SVGA;
// constexpr int CAMERA_JPEG_QUALITY = 15;

// // =====================================================
// // COMMAND CONFIGURATION
// // =====================================================

// constexpr size_t COMMAND_BUFFER_SIZE = 64;

// char commandBuffer[COMMAND_BUFFER_SIZE] = {};
// size_t commandIndex = 0;

// uint32_t captureCount = 0;

// // =====================================================
// // INITIALIZE CAMERA
// // =====================================================

// bool initializeCamera() {
//     camera_config_t config = {};

//     config.ledc_channel = LEDC_CHANNEL_0;
//     config.ledc_timer = LEDC_TIMER_0;

//     config.pin_d0 = CAMERA_PIN_Y2;
//     config.pin_d1 = CAMERA_PIN_Y3;
//     config.pin_d2 = CAMERA_PIN_Y4;
//     config.pin_d3 = CAMERA_PIN_Y5;
//     config.pin_d4 = CAMERA_PIN_Y6;
//     config.pin_d5 = CAMERA_PIN_Y7;
//     config.pin_d6 = CAMERA_PIN_Y8;
//     config.pin_d7 = CAMERA_PIN_Y9;

//     config.pin_xclk = CAMERA_PIN_XCLK;
//     config.pin_pclk = CAMERA_PIN_PCLK;
//     config.pin_vsync = CAMERA_PIN_VSYNC;
//     config.pin_href = CAMERA_PIN_HREF;

//     config.pin_sccb_sda = CAMERA_PIN_SIOD;
//     config.pin_sccb_scl = CAMERA_PIN_SIOC;

//     config.pin_pwdn = CAMERA_PIN_PWDN;
//     config.pin_reset = CAMERA_PIN_RESET;

//     config.xclk_freq_hz = 20000000;
//     config.pixel_format = PIXFORMAT_JPEG;

//     config.frame_size = CAMERA_FRAME_SIZE;
//     config.jpeg_quality = CAMERA_JPEG_QUALITY;

//     config.fb_count = 1;
//     config.fb_location = CAMERA_FB_IN_PSRAM;
//     config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

//     Serial.println("[CAMERA] Initializing...");

//     if (!psramFound()) {
//         Serial.println("[CAMERA] ERROR: PSRAM not found!");
//         return false;
//     }

//     esp_err_t error = esp_camera_init(&config);

//     if (error != ESP_OK) {
//         Serial.printf(
//             "[CAMERA] Initialization failed: 0x%x\n",
//             error
//         );

//         return false;
//     }

//     Serial.println("[CAMERA] Initialization successful!");
//     Serial.println("[CAMERA] Resolution: SVGA 800x600");

//     return true;
// }

// // =====================================================
// // SEND UINT32 - LITTLE ENDIAN
// // =====================================================

// bool sendUInt32(uint32_t value) {
//     uint8_t buffer[4];

//     buffer[0] = value & 0xFF;
//     buffer[1] = (value >> 8) & 0xFF;
//     buffer[2] = (value >> 16) & 0xFF;
//     buffer[3] = (value >> 24) & 0xFF;

//     return cameraSerial.write(buffer, sizeof(buffer))
//         == sizeof(buffer);
// }

// // =====================================================
// // SEND ERROR PACKET
// // =====================================================

// void sendError(uint32_t sampleSeq) {
//     Serial.printf(
//         "[CAMERA] Sending error response. Sequence: %lu\n",
//         (unsigned long)sampleSeq
//     );

//     cameraSerial.write("ERR0", 4);

//     sendUInt32(sampleSeq);
//     sendUInt32(0);

//     cameraSerial.flush();
// }

// // =====================================================
// // CHECK JPEG VALIDITY
// // =====================================================

// bool isValidJPEG(const camera_fb_t* frame) {
//     if (frame == nullptr || frame->buf == nullptr) {
//         return false;
//     }

//     if (frame->len < 4) {
//         return false;
//     }

//     return frame->format == PIXFORMAT_JPEG
//         && frame->buf[0] == 0xFF
//         && frame->buf[1] == 0xD8
//         && frame->buf[frame->len - 2] == 0xFF
//         && frame->buf[frame->len - 1] == 0xD9;
// }

// // =====================================================
// // SEND IMAGE TO MAIN ESP32
// // =====================================================

// bool sendImage(
//     uint32_t sampleSeq,
//     const uint8_t* imageData,
//     size_t imageSize
// ) {
//     if (imageData == nullptr || imageSize == 0) {
//         return false;
//     }

//     if (imageSize > MAX_IMAGE_SIZE) {
//         Serial.printf(
//             "[CAMERA] Image too large: %u bytes\n",
//             (unsigned int)imageSize
//         );

//         return false;
//     }

//     // Header: IMG0
//     if (cameraSerial.write("IMG0", 4) != 4) {
//         return false;
//     }

//     // Sample sequence
//     if (!sendUInt32(sampleSeq)) {
//         return false;
//     }

//     // Image size
//     if (!sendUInt32((uint32_t)imageSize)) {
//         return false;
//     }

//     // JPEG data
//     constexpr size_t CHUNK_SIZE = 1024;

//     size_t sentBytes = 0;

//     while (sentBytes < imageSize) {
//         size_t remaining = imageSize - sentBytes;

//         size_t chunkSize =
//             remaining < CHUNK_SIZE
//                 ? remaining
//                 : CHUNK_SIZE;

//         size_t written = cameraSerial.write(
//             imageData + sentBytes,
//             chunkSize
//         );

//         if (written != chunkSize) {
//             Serial.println("[CAMERA] UART transmission failed!");
//             return false;
//         }

//         sentBytes += written;
//     }

//     cameraSerial.flush();

//     Serial.printf(
//         "[CAMERA] Image sent successfully: %u bytes\n",
//         (unsigned int)sentBytes
//     );

//     return true;
// }

// // =====================================================
// // CAPTURE IMAGE
// // =====================================================

// void captureImage(uint32_t sampleSeq) {
//     Serial.println();
//     Serial.println("========== CAMERA CAPTURE ==========");

//     Serial.printf(
//         "[CAMERA] Sample sequence: %lu\n",
//         (unsigned long)sampleSeq
//     );

//     camera_fb_t* frame = esp_camera_fb_get();

//     if (frame == nullptr) {
//         Serial.println("[CAMERA] Failed to capture image!");

//         sendError(sampleSeq);

//         return;
//     }

//     // Save information before releasing framebuffer.
//     const size_t imageSize = frame->len;
//     const uint16_t imageWidth = frame->width;
//     const uint16_t imageHeight = frame->height;

//     const bool validJPEG = isValidJPEG(frame);

//     Serial.printf(
//         "[CAMERA] Resolution: %u x %u\n",
//         imageWidth,
//         imageHeight
//     );

//     Serial.printf(
//         "[CAMERA] Image size: %u bytes\n",
//         (unsigned int)imageSize
//     );

//     if (!validJPEG || imageSize > MAX_IMAGE_SIZE) {
//         Serial.println("[CAMERA] Invalid JPEG or image too large!");

//         esp_camera_fb_return(frame);

//         sendError(sampleSeq);

//         return;
//     }

//     Serial.println("[CAMERA] JPEG validation successful.");

//     // Keep framebuffer alive until transmission completes.
//     bool success = sendImage(
//         sampleSeq,
//         frame->buf,
//         imageSize
//     );

//     // Release framebuffer after transmission.
//     esp_camera_fb_return(frame);

//     if (success) {
//         captureCount++;

//         Serial.printf(
//             "[CAMERA] Capture completed. Total: %lu\n",
//             (unsigned long)captureCount
//         );
//     } else {
//         Serial.println("[CAMERA] Image transmission failed.");
//     }

//     Serial.println("====================================");
// }

// // =====================================================
// // PROCESS COMMAND
// // =====================================================

// void processCommand(const char* command) {
//     Serial.printf("[UART] Received command: %s\n", command);

//     constexpr char COMMAND_PREFIX[] = "CAPTURE,";

//     if (strncmp(
//         command,
//         COMMAND_PREFIX,
//         sizeof(COMMAND_PREFIX) - 1
//     ) != 0) {
//         Serial.println("[UART] Unknown command.");
//         return;
//     }

//     const char* sequenceText =
//         command + sizeof(COMMAND_PREFIX) - 1;

//     if (*sequenceText == '\0') {
//         Serial.println("[UART] Missing sample sequence.");
//         return;
//     }

//     char* endPointer = nullptr;

//     unsigned long parsedSequence = strtoul(
//         sequenceText,
//         &endPointer,
//         10
//     );

//     if (
//         endPointer == sequenceText ||
//         *endPointer != '\0'
//     ) {
//         Serial.println("[UART] Invalid sample sequence.");
//         return;
//     }

//     uint32_t sampleSeq = (uint32_t)parsedSequence;

//     captureImage(sampleSeq);
// }

// // =====================================================
// // READ UART COMMANDS
// // =====================================================

// void receiveCommands() {
//     while (cameraSerial.available() > 0) {
//         char receivedChar = (char)cameraSerial.read();

//         if (receivedChar == '\r') {
//             continue;
//         }

//         if (receivedChar == '\n') {
//             commandBuffer[commandIndex] = '\0';

//             if (commandIndex > 0) {
//                 processCommand(commandBuffer);
//             }

//             commandIndex = 0;

//             continue;
//         }

//         if (commandIndex >= COMMAND_BUFFER_SIZE - 1) {
//             Serial.println("[UART] Command buffer overflow!");

//             commandIndex = 0;

//             continue;
//         }

//         commandBuffer[commandIndex++] = receivedChar;
//     }
// }

// // =====================================================
// // SETUP
// // =====================================================

// void setup() {
//     Serial.begin(115200);

//     delay(1000);

//     Serial.println();
//     Serial.println("====================================");
//     Serial.println("       ESP32-CAM UART SERVER");
//     Serial.println("       AI-THINKER + OV2640");
//     Serial.println("====================================");

//     // Initialize camera first.
//     if (!initializeCamera()) {
//         Serial.println("[FATAL] Camera initialization failed!");

//         while (true) {
//             delay(1000);
//         }
//     }

//     // Initialize UART1.
//     cameraSerial.begin(
//         UART_BAUD_RATE,
//         SERIAL_8N1,
//         UART_RX_PIN,
//         UART_TX_PIN
//     );

//     Serial.println("[UART] Initialized successfully.");
//     Serial.printf("[UART] Baud rate: %lu\n", UART_BAUD_RATE);
//     Serial.printf("[UART] RX pin: GPIO%d\n", UART_RX_PIN);
//     Serial.printf("[UART] TX pin: GPIO%d\n", UART_TX_PIN);

//     Serial.println();
//     Serial.println("[SYSTEM] Camera server is ready.");
//     Serial.println("[SYSTEM] Waiting for CAPTURE commands...");
// }

// // =====================================================
// // LOOP
// // =====================================================

// void loop() {
//     receiveCommands();
// }
// // 