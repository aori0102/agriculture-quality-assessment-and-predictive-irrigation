
#ifndef CAMERA_UART_CLIENT_H
#define CAMERA_UART_CLIENT_H

#include <Arduino.h>

struct CameraImage {
    uint32_t sampleSeq;
    uint32_t imageSize;
    uint8_t* data;
};

class CameraUARTClient {

public:

    void begin(
        HardwareSerial& serial,
        uint32_t baudRate,
        int rxPin,
        int txPin
    );

    bool requestCapture(uint32_t sampleSeq);

    void loop();

    bool hasImage() const;

    bool hasError() const;

    bool isBusy() const;

    bool takeImage(CameraImage& image);

    void reset();

private:

    enum class ReceiveState {
        WAIT_HEADER,
        READ_SAMPLE_SEQ,
        READ_IMAGE_SIZE,
        READ_IMAGE_DATA,
        DISCARD_IMAGE_DATA
    };

    static constexpr size_t HEADER_SIZE = 4;
    static constexpr size_t MAX_IMAGE_SIZE = 256 * 1024;

    static constexpr uint32_t RESPONSE_TIMEOUT_MS = 10000;

    HardwareSerial* m_serial = nullptr;

    ReceiveState m_state = ReceiveState::WAIT_HEADER;

    uint8_t m_header[HEADER_SIZE] = {};
    size_t m_headerIndex = 0;

    uint8_t m_integerBuffer[4] = {};
    size_t m_integerIndex = 0;

    uint32_t m_sampleSeq = 0;
    uint32_t m_imageSize = 0;

    uint8_t* m_imageBuffer = nullptr;
    size_t m_receivedBytes = 0;

    bool m_imageReady = false;
    bool m_captureError = false;
    bool m_waitingForResponse = false;

    bool m_errorPacket = false;

    uint32_t m_requestTime = 0;

    void processByte(uint8_t data);

    void processHeader();

    void processInteger(uint32_t& value);

    void resetParser();

    void releaseImageBuffer();

    void handleTimeout();

    bool isValidJPEG() const;
};

#endif
