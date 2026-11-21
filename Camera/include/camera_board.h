#pragma once

// Freenove ESP32-S3 WROOM camera connector (ESP32S3_EYE pin mapping).
// https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board/blob/main/C/Sketches/Sketch_07.1_CameraWebServer/board_config.h
#if !defined(CONFIG_IDF_TARGET_ESP32S3)
#error "This firmware targets ESP32-S3, not the AI-Thinker ESP32-CAM."
#endif
#define CAMERA_PIN_PWDN  -1
#define CAMERA_PIN_RESET -1
#define CAMERA_PIN_XCLK  15
#define CAMERA_PIN_SIOD  4
#define CAMERA_PIN_SIOC  5
#define CAMERA_PIN_Y2    11
#define CAMERA_PIN_Y3    9
#define CAMERA_PIN_Y4    8
#define CAMERA_PIN_Y5    10
#define CAMERA_PIN_Y6    12
#define CAMERA_PIN_Y7    18
#define CAMERA_PIN_Y8    17
#define CAMERA_PIN_Y9    16
#define CAMERA_PIN_VSYNC 6
#define CAMERA_PIN_HREF  7
#define CAMERA_PIN_PCLK  13

// Dedicated UART1, separate from debug UART0/USB; camera pins 13/14 from
// the old AI-Thinker sketch must NOT be reused on this board.
constexpr int UART_RX_PIN = 1;
constexpr int UART_TX_PIN = 2;
constexpr uint32_t UART_BAUD_RATE = 921600;
constexpr uint32_t CAMERA_XCLK_HZ = 20000000;
