
#include <Arduino.h>
#include "esp_camera.h"
#include "img_converters.h"
#include "esp_heap_caps.h"
#include "rotate_ccw.h"
#include "capture_error_protocol.h"
CaptureError rotationError = CaptureError::JpegEncodeFailed;
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <climits>

#include "camera_board.h"

HardwareSerial cameraSerial(1);

// =====================================================
// CAMERA CONFIGURATION
// =====================================================

constexpr size_t MAX_IMAGE_SIZE = 256 * 1024;

constexpr framesize_t CAMERA_FRAME_SIZE = FRAMESIZE_SVGA;
constexpr int CAMERA_JPEG_QUALITY = 15;

// =====================================================
// COMMAND CONFIGURATION
// =====================================================

constexpr size_t COMMAND_BUFFER_SIZE = 64;

char commandBuffer[COMMAND_BUFFER_SIZE] = {};
bool discardCommand = false;
bool cameraReady = false;
size_t commandIndex = 0;

uint32_t captureCount = 0;

// =====================================================
// INITIALIZE CAMERA
// =====================================================

bool initializeCamera() {
    camera_config_t config = {};

    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;

    config.pin_d0 = CAMERA_PIN_Y2;
    config.pin_d1 = CAMERA_PIN_Y3;
    config.pin_d2 = CAMERA_PIN_Y4;
    config.pin_d3 = CAMERA_PIN_Y5;
    config.pin_d4 = CAMERA_PIN_Y6;
    config.pin_d5 = CAMERA_PIN_Y7;
    config.pin_d6 = CAMERA_PIN_Y8;
    config.pin_d7 = CAMERA_PIN_Y9;

    config.pin_xclk = CAMERA_PIN_XCLK;
    config.pin_pclk = CAMERA_PIN_PCLK;
    config.pin_vsync = CAMERA_PIN_VSYNC;
    config.pin_href = CAMERA_PIN_HREF;

    config.pin_sccb_sda = CAMERA_PIN_SIOD;
    config.pin_sccb_scl = CAMERA_PIN_SIOC;

    config.pin_pwdn = CAMERA_PIN_PWDN;
    config.pin_reset = CAMERA_PIN_RESET;

    config.xclk_freq_hz = CAMERA_XCLK_HZ;
    config.pixel_format = PIXFORMAT_JPEG;

    config.frame_size = CAMERA_FRAME_SIZE;
    config.jpeg_quality = CAMERA_JPEG_QUALITY;

    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    Serial.println("[CAMERA] Initializing...");

    if (!psramFound()) {
        Serial.println("[CAMERA] ERROR: PSRAM not found!");
        return false;
    }

    esp_err_t error = esp_camera_init(&config);

    if (error != ESP_OK) {
        Serial.printf(
            "[CAMERA] Initialization failed: 0x%x\n",
            error
        );

        return false;
    }

    Serial.println("[CAMERA] Initialization successful!");
    Serial.println("[CAMERA] Resolution: SVGA 800x600");

    return true;
}

// =====================================================
// SEND UINT32 - LITTLE ENDIAN
// =====================================================

bool sendUInt32(uint32_t value) {
    uint8_t buffer[4];

    buffer[0] = value & 0xFF;
    buffer[1] = (value >> 8) & 0xFF;
    buffer[2] = (value >> 16) & 0xFF;
    buffer[3] = (value >> 24) & 0xFF;

    return cameraSerial.write(buffer, sizeof(buffer))
        == sizeof(buffer);
}

// =====================================================
// SEND ERROR PACKET
// =====================================================

void sendError(uint32_t sampleSeq, CaptureError reason) {
    Serial.printf(
        "[CAMERA] Sending error response. Sequence: %lu\n",
        (unsigned long)sampleSeq
    );

    Serial.printf("[CAMERA] ERROR seq=%lu code=%lu reason=%s\n", (unsigned long)sampleSeq,
        (unsigned long)reason, captureErrorName(static_cast<uint32_t>(reason)));
    cameraSerial.write("ERR1", 4);
    sendUInt32(sampleSeq);
    sendUInt32(static_cast<uint32_t>(reason));

    cameraSerial.flush();
}

// =====================================================
// CHECK JPEG VALIDITY
// =====================================================

bool isValidJPEG(const camera_fb_t* frame) {
    if (frame == nullptr || frame->buf == nullptr) {
        return false;
    }

    if (frame->len < 4) {
        return false;
    }

    return frame->format == PIXFORMAT_JPEG
        && frame->buf[0] == 0xFF
        && frame->buf[1] == 0xD8
        && frame->buf[frame->len - 2] == 0xFF
        && frame->buf[frame->len - 1] == 0xD9;
}

// =====================================================
// SEND IMAGE TO MAIN ESP32
// =====================================================

bool sendImage(
    uint32_t sampleSeq,
    const uint8_t* imageData,
    size_t imageSize
) {
    if (imageData == nullptr || imageSize == 0) {
        return false;
    }

    if (imageSize > MAX_IMAGE_SIZE) {
        Serial.printf(
            "[CAMERA] Image too large: %u bytes\n",
            (unsigned int)imageSize
        );

        return false;
    }

    // Header: IMG0
    if (cameraSerial.write("IMG0", 4) != 4) {
        return false;
    }

    // Sample sequence
    if (!sendUInt32(sampleSeq)) {
        return false;
    }

    // Image size
    if (!sendUInt32((uint32_t)imageSize)) {
        return false;
    }

    // JPEG data
    constexpr size_t CHUNK_SIZE = 1024;

    size_t sentBytes = 0;

    while (sentBytes < imageSize) {
        size_t remaining = imageSize - sentBytes;

        size_t chunkSize =
            remaining < CHUNK_SIZE
                ? remaining
                : CHUNK_SIZE;

        size_t written = cameraSerial.write(
            imageData + sentBytes,
            chunkSize
        );

        if (written != chunkSize) {
            Serial.println("[CAMERA] UART transmission failed!");
            return false;
        }

        sentBytes += written;
    }

    cameraSerial.flush();

    Serial.printf(
        "[CAMERA] Image sent successfully: %u bytes\n",
        (unsigned int)sentBytes
    );

    return true;
}

// Software JPEG encoder quality uses 1..100 (higher is better), unlike the sensor.
constexpr uint8_t ROTATED_JPEG_QUALITY = 85;
struct JpegOutput {
    uint8_t* data;
    size_t length = 0;
    bool overflow = false;
};
size_t writeRotatedJpeg(void* arg, size_t index, const void* data, size_t length) {
    auto* output = static_cast<JpegOutput*>(arg);
    if (index > MAX_IMAGE_SIZE || length > MAX_IMAGE_SIZE - index) {
        output->overflow = true;
        return 0;
    }
    if (length) memcpy(output->data + index, data, length);
    output->length = index + length;
    return length;
}

bool rotateJpegCCW(const camera_fb_t* frame, uint8_t** jpeg, size_t* length) {
    *jpeg = nullptr;
    *length = 0;
    // Bound allocations to the configured capture dimensions.
    if (frame->width != 800 || frame->height != 600) { rotationError = CaptureError::UnexpectedResolution; return false; }
    const size_t bytes = static_cast<size_t>(frame->width) * frame->height * 3;
    auto* decoded = static_cast<uint8_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    auto* rotated = static_cast<uint8_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!decoded || !rotated) {
        rotationError = CaptureError::RgbAllocationFailed;
        free(decoded); free(rotated);
        return false;
    }
    if (!fmt2rgb888(frame->buf, frame->len, PIXFORMAT_JPEG, decoded)) {
        rotationError = CaptureError::JpegDecodeFailed;
        free(decoded); free(rotated);
        return false;
    }
    for (size_t y = 0; y < frame->height; ++y) {
        for (size_t x = 0; x < frame->width; ++x) {
            memcpy(rotated + ccwIndex(x, y, frame->width, frame->height) * 3,
                   decoded + (y * frame->width + x) * 3, 3);
        }
        if ((y & 15) == 0) delay(1);
    }
    free(decoded);
    JpegOutput output;
    output.data = static_cast<uint8_t*>(heap_caps_malloc(MAX_IMAGE_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!output.data) { rotationError = CaptureError::OutputAllocationFailed; free(rotated); return false; }
    const bool encoded = fmt2jpg_cb(rotated, bytes, frame->height, frame->width,
        PIXFORMAT_RGB888, ROTATED_JPEG_QUALITY, writeRotatedJpeg, &output);
    free(rotated);
    if (!encoded || output.overflow || output.length < 4 ||
        output.data[0] != 0xff || output.data[1] != 0xd8 ||
        output.data[output.length - 2] != 0xff || output.data[output.length - 1] != 0xd9) {
        rotationError = output.overflow ? CaptureError::OutputTooLarge :
            (!encoded ? CaptureError::JpegEncodeFailed : CaptureError::InvalidRotatedJpeg);
        free(output.data);
        return false;
    }
    *jpeg = output.data;
    *length = output.length;
    return true;
}

// =====================================================
// CAPTURE IMAGE
// =====================================================

void captureImage(uint32_t sampleSeq) {
    Serial.println();
    Serial.println("========== CAMERA CAPTURE ==========");

    Serial.printf(
        "[CAMERA] Sample sequence: %lu\n",
        (unsigned long)sampleSeq
    );

    if (!cameraReady) {
        sendError(sampleSeq, CaptureError::CameraNotReady);
        return;
    }
    // Discard the frame queued while idle so the requested capture is fresh.
    camera_fb_t* stale = esp_camera_fb_get();
    if (stale != nullptr) esp_camera_fb_return(stale);
    camera_fb_t* frame = esp_camera_fb_get();

    if (frame == nullptr) {
        Serial.println("[CAMERA] Failed to capture image!");

        sendError(sampleSeq, CaptureError::FrameUnavailable);

        return;
    }

    // Save information before releasing framebuffer.
    const size_t imageSize = frame->len;
    const uint16_t imageWidth = frame->width;
    const uint16_t imageHeight = frame->height;

    const bool validJPEG = isValidJPEG(frame);

    Serial.printf(
        "[CAMERA] Resolution: %u x %u\n",
        imageWidth,
        imageHeight
    );

    Serial.printf(
        "[CAMERA] Image size: %u bytes\n",
        (unsigned int)imageSize
    );

    if (!validJPEG || imageSize > MAX_IMAGE_SIZE) {
        const CaptureError error = !validJPEG ? CaptureError::InvalidSourceJpeg : CaptureError::SourceTooLarge;
        Serial.println("[CAMERA] Invalid JPEG or image too large!");

        esp_camera_fb_return(frame);

        sendError(sampleSeq, error);

        return;
    }

    Serial.println("[CAMERA] JPEG validation successful.");

    uint8_t* rotatedJpeg = nullptr;
    size_t rotatedLength = 0;
    const uint32_t rotationStart = millis();
    const bool rotated = rotateJpegCCW(frame, &rotatedJpeg, &rotatedLength);
    esp_camera_fb_return(frame);
    if (!rotated) {
        Serial.println("[CAMERA] 90-degree CCW rotation/encoding failed; no image sent.");
        sendError(sampleSeq, rotationError);
        return;
    }
    Serial.printf("[CAMERA] Rotated 90 CCW: 600 x 800, %u bytes, %lu ms\n",
        static_cast<unsigned int>(rotatedLength),
        static_cast<unsigned long>(millis() - rotationStart));
    const bool success = sendImage(sampleSeq, rotatedJpeg, rotatedLength);
    free(rotatedJpeg);

    if (success) {
        captureCount++;

        Serial.printf(
            "[CAMERA] Capture completed. Total: %lu\n",
            (unsigned long)captureCount
        );
    } else {
        Serial.println("[CAMERA] Image transmission failed.");
    }

    Serial.println("====================================");
}

// =====================================================
// PROCESS COMMAND
// =====================================================

void processCommand(const char* command) {
    Serial.printf("[UART] Received command: %s\n", command);

    constexpr char COMMAND_PREFIX[] = "CAPTURE,";

    if (strncmp(
        command,
        COMMAND_PREFIX,
        sizeof(COMMAND_PREFIX) - 1
    ) != 0) {
        Serial.println("[UART] Unknown command.");
        return;
    }

    const char* sequenceText =
        command + sizeof(COMMAND_PREFIX) - 1;

    if (*sequenceText == '\0') {
        Serial.println("[UART] Missing sample sequence.");
        return;
    }

    for (const char* p = sequenceText; *p; ++p) {
        if (*p < '0' || *p > '9') {
            Serial.println("[UART] Sequence must contain decimal digits only.");
            return;
        }
    }
    errno = 0;
    char* endPointer = nullptr;

    unsigned long parsedSequence = strtoul(
        sequenceText,
        &endPointer,
        10
    );

    if (
        errno == ERANGE || parsedSequence > UINT32_MAX ||
        endPointer == sequenceText ||
        *endPointer != '\0'
    ) {
        Serial.println("[UART] Invalid sample sequence.");
        return;
    }

    uint32_t sampleSeq = (uint32_t)parsedSequence;

    captureImage(sampleSeq);
}

// =====================================================
// READ UART COMMANDS
// =====================================================

void receiveCommands() {
    while (cameraSerial.available() > 0) {
        char receivedChar = (char)cameraSerial.read();

        if (receivedChar == '\r') {
            continue;
        }

        if (receivedChar == '\n') {
            commandBuffer[commandIndex] = '\0';

            if (!discardCommand && commandIndex > 0) {
                processCommand(commandBuffer);
            }

            commandIndex = 0;
            discardCommand = false;

            continue;
        }

        if (discardCommand) continue;

        if (receivedChar == '\0') { discardCommand = true; continue; }

        if (commandIndex >= COMMAND_BUFFER_SIZE - 1) {
            Serial.println("[UART] Command buffer overflow!");
            discardCommand = true;

            commandIndex = 0;

            continue;
        }

        commandBuffer[commandIndex++] = receivedChar;
    }
}

// =====================================================
// SETUP
// =====================================================

void setup() {
    Serial.begin(115200);

    delay(1000);

    Serial.println();
    Serial.println("====================================");
    Serial.println("       ESP32-S3 UART CAMERA SERVER");
    Serial.println("       FREENOVE S3 + OV5640");
    Serial.println("====================================");

    // Initialize camera first.
    cameraReady = initializeCamera();
    if (!cameraReady) {
        Serial.println("[CAMERA] Not ready; CAPTURE requests will receive ERR0. Reset after fixing hardware.");
    }

    // Initialize UART1.
    cameraSerial.begin(
        UART_BAUD_RATE,
        SERIAL_8N1,
        UART_RX_PIN,
        UART_TX_PIN
    );

    Serial.println("[UART] Initialized successfully.");
    Serial.printf("[UART] Baud rate: %lu\n", UART_BAUD_RATE);
    Serial.printf("[UART] RX pin: GPIO%d\n", UART_RX_PIN);
    Serial.printf("[UART] TX pin: GPIO%d\n", UART_TX_PIN);

    Serial.println();
    Serial.println(cameraReady ? "[SYSTEM] Camera server is ready." : "[SYSTEM] UART ready; camera unavailable.");
    Serial.println("[SYSTEM] Waiting for CAPTURE commands...");
}

// =====================================================
// LOOP
// =====================================================

void loop() {
    receiveCommands();
    delay(1);
}
// 
