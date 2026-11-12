
#include "CameraUARTClient.h"

#include <cstdlib>
#include <cstring>

// =====================================================
// 1. INITIALIZATION
// =====================================================

void CameraUARTClient::begin(
    HardwareSerial& serial,
    uint32_t baudRate,
    int rxPin,
    int txPin
) {
    m_serial = &serial;

    m_serial->begin(
        baudRate,
        SERIAL_8N1,
        rxPin,
        txPin
    );

    reset();

    Serial.println("[CAMERA] UART initialized!");
}

// =====================================================
// 2. REQUEST CAPTURE
// =====================================================

bool CameraUARTClient::requestCapture(uint32_t sampleSeq) {

    if (m_serial == nullptr) {
        Serial.println("[CAMERA] UART is not initialized!");
        return false;
    }

    if (isBusy()) {
        Serial.println(
            "[CAMERA] Previous request is still pending!"
        );

        return false;
    }

    char command[32];

    int length = snprintf(
        command,
        sizeof(command),
        "CAPTURE,%lu\n",
        static_cast<unsigned long>(sampleSeq)
    );

    if (length <= 0 || length >= sizeof(command)) {
        return false;
    }

    size_t written = m_serial->write(
        reinterpret_cast<const uint8_t*>(command),
        static_cast<size_t>(length)
    );

    if (written != static_cast<size_t>(length)) {
        Serial.println("[CAMERA] Failed to send command!");
        return false;
    }

    m_sampleSeq = sampleSeq;

    m_waitingForResponse = true;
    m_requestTime = millis();

    Serial.printf(
        "[CAMERA] Capture requested. Sample: %lu\n",
        static_cast<unsigned long>(sampleSeq)
    );

    return true;
}

// =====================================================
// 3. UART RECEIVE LOOP
// =====================================================

void CameraUARTClient::loop() {

    if (m_serial == nullptr) {
        return;
    }

    if (m_imageReady || m_captureError) {
        return;
    }

    // Check whether camera response has timed out.
    handleTimeout();

    if (m_captureError) {
        return;
    }

    while (m_serial->available() > 0) {

        int received = m_serial->read();

        if (received < 0) {
            break;
        }

        processByte(static_cast<uint8_t>(received));

        if (m_imageReady || m_captureError) {
            break;
        }
    }
}

// =====================================================
// 4. PROCESS RECEIVED BYTE
// =====================================================

void CameraUARTClient::processByte(uint8_t data) {

    switch (m_state) {

        // ---------------------------------------------
        // WAIT FOR PACKET HEADER
        // ---------------------------------------------

        case ReceiveState::WAIT_HEADER: {

            m_header[m_headerIndex++] = data;

            if (m_headerIndex == HEADER_SIZE) {
                processHeader();
            }

            break;
        }

        // ---------------------------------------------
        // READ SAMPLE SEQUENCE
        // ---------------------------------------------

        case ReceiveState::READ_SAMPLE_SEQ: {

            m_integerBuffer[m_integerIndex++] = data;

            if (m_integerIndex == 4) {

                processInteger(m_sampleSeq);

                m_integerIndex = 0;

                m_state = ReceiveState::READ_IMAGE_SIZE;
            }

            break;
        }

        // ---------------------------------------------
        // READ IMAGE SIZE
        // ---------------------------------------------

        case ReceiveState::READ_IMAGE_SIZE: {

            m_integerBuffer[m_integerIndex++] = data;

            if (m_integerIndex == 4) {

                processInteger(m_imageSize);

                m_integerIndex = 0;

                // Handle camera error packet.
                if (m_errorPacket) {

                    if (m_imageSize != 0) {
                        Serial.println(
                            "[CAMERA] Invalid error packet!"
                        );
                    } else {
                        Serial.printf(
                            "[CAMERA] Capture failed. Sample: %lu\n",
                            static_cast<unsigned long>(m_sampleSeq)
                        );
                    }

                    m_waitingForResponse = false;
                    m_captureError = true;

                    resetParser();

                    return;
                }

                // Validate image size.
                if (
                    m_imageSize == 0 ||
                    m_imageSize > MAX_IMAGE_SIZE
                ) {
                    Serial.printf(
                        "[CAMERA] Invalid image size: %lu\n",
                        static_cast<unsigned long>(m_imageSize)
                    );

                    m_waitingForResponse = false;
                    m_captureError = true;

                    resetParser();

                    return;
                }

                // Allocate image buffer.
                m_imageBuffer = static_cast<uint8_t*>(
                    malloc(m_imageSize)
                );

                if (m_imageBuffer == nullptr) {

                    Serial.println(
                        "[CAMERA] Failed to allocate image buffer!"
                    );

                    // Discard the remaining image bytes.
                    // This keeps the UART stream aligned.
                    m_receivedBytes = 0;
                    m_state = ReceiveState::DISCARD_IMAGE_DATA;

                    return;
                }

                m_receivedBytes = 0;

                m_state = ReceiveState::READ_IMAGE_DATA;
            }

            break;
        }

        // ---------------------------------------------
        // READ JPEG DATA
        // ---------------------------------------------

        case ReceiveState::READ_IMAGE_DATA: {

            m_imageBuffer[m_receivedBytes++] = data;

            if (m_receivedBytes >= m_imageSize) {

                if (isValidJPEG()) {

                    m_imageReady = true;
                    m_waitingForResponse = false;

                    Serial.printf(
                        "[CAMERA] Image received. Sample: %lu | Size: %lu bytes\n",
                        static_cast<unsigned long>(m_sampleSeq),
                        static_cast<unsigned long>(m_imageSize)
                    );

                } else {

                    Serial.println(
                        "[CAMERA] Invalid JPEG data!"
                    );

                    releaseImageBuffer();

                    m_waitingForResponse = false;
                    m_captureError = true;

                    resetParser();
                }
            }

            break;
        }

        // ---------------------------------------------
        // DISCARD IMAGE DATA
        // ---------------------------------------------

        case ReceiveState::DISCARD_IMAGE_DATA: {

            m_receivedBytes++;

            if (m_receivedBytes >= m_imageSize) {

                Serial.println(
                    "[CAMERA] Image discarded because buffer allocation failed."
                );

                m_waitingForResponse = false;
                m_captureError = true;

                resetParser();
            }

            break;
        }
    }
}

// =====================================================
// 5. PROCESS PACKET HEADER
// =====================================================

void CameraUARTClient::processHeader() {

    // Successful image packet: IMG0
    if (memcmp(m_header, "IMG0", HEADER_SIZE) == 0) {

        m_errorPacket = false;

        m_state = ReceiveState::READ_SAMPLE_SEQ;

        m_integerIndex = 0;
        m_headerIndex = 0;

        return;
    }

    // Camera error packet: ERR0
    if (memcmp(m_header, "ERR0", HEADER_SIZE) == 0) {

        m_errorPacket = true;

        m_state = ReceiveState::READ_SAMPLE_SEQ;

        m_integerIndex = 0;
        m_headerIndex = 0;

        return;
    }

    // Unknown header.
    // Shift one byte and continue searching.
    memmove(
        m_header,
        m_header + 1,
        HEADER_SIZE - 1
    );

    m_headerIndex = HEADER_SIZE - 1;
}

// =====================================================
// 6. CONVERT LITTLE-ENDIAN UINT32
// =====================================================

void CameraUARTClient::processInteger(uint32_t& value) {

    value =
        static_cast<uint32_t>(m_integerBuffer[0]) |
        (static_cast<uint32_t>(m_integerBuffer[1]) << 8) |
        (static_cast<uint32_t>(m_integerBuffer[2]) << 16) |
        (static_cast<uint32_t>(m_integerBuffer[3]) << 24);
}

// =====================================================
// 7. VALIDATE JPEG
// =====================================================

bool CameraUARTClient::isValidJPEG() const {

    if (m_imageBuffer == nullptr || m_imageSize < 4) {
        return false;
    }

    // JPEG start marker: FF D8
    // JPEG end marker: FF D9

    return
        m_imageBuffer[0] == 0xFF &&
        m_imageBuffer[1] == 0xD8 &&
        m_imageBuffer[m_imageSize - 2] == 0xFF &&
        m_imageBuffer[m_imageSize - 1] == 0xD9;
}

// =====================================================
// 8. IMAGE ACCESS
// =====================================================

bool CameraUARTClient::hasImage() const {
    return m_imageReady;
}

bool CameraUARTClient::hasError() const {
    return m_captureError;
}

bool CameraUARTClient::isBusy() const {
    return m_waitingForResponse || m_imageReady || m_captureError;
}

bool CameraUARTClient::takeImage(CameraImage& image) {

    if (!m_imageReady || m_imageBuffer == nullptr) {
        return false;
    }

    image.sampleSeq = m_sampleSeq;
    image.imageSize = m_imageSize;
    image.data = m_imageBuffer;

    // Transfer ownership to caller.
    // Caller must free(image.data).
    m_imageBuffer = nullptr;

    m_imageReady = false;
    m_waitingForResponse = false;

    resetParser();

    return true;
}

// =====================================================
// 9. TIMEOUT
// =====================================================

void CameraUARTClient::handleTimeout() {

    if (!m_waitingForResponse) {
        return;
    }

    if (
        millis() - m_requestTime <
        RESPONSE_TIMEOUT_MS
    ) {
        return;
    }

    Serial.println("[CAMERA] Response timeout!");

    releaseImageBuffer();

    m_waitingForResponse = false;
    m_captureError = true;

    resetParser();
}

// =====================================================
// 10. MEMORY MANAGEMENT
// =====================================================

void CameraUARTClient::releaseImageBuffer() {

    if (m_imageBuffer != nullptr) {

        free(m_imageBuffer);

        m_imageBuffer = nullptr;
    }
}

// =====================================================
// 11. RESET
// =====================================================

void CameraUARTClient::resetParser() {

    m_state = ReceiveState::WAIT_HEADER;

    m_headerIndex = 0;
    m_integerIndex = 0;

    m_imageSize = 0;
    m_receivedBytes = 0;

    m_errorPacket = false;
}

void CameraUARTClient::reset() {

    releaseImageBuffer();

    m_imageReady = false;
    m_captureError = false;
    m_waitingForResponse = false;

    m_sampleSeq = 0;

    m_requestTime = 0;

    resetParser();
}
