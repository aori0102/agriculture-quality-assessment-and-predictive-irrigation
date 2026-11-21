# Freenove ESP32-S3 + OV5640 UART camera

Independent PlatformIO firmware for the camera board. `../Things` remains the
regular ESP32 sensor/pump/MQTT firmware. The former commented
`Things/src/Camera/Camera.cpp` is replaced by this project's `src/main.cpp`;
Things keeps `CameraUARTClient.cpp/.h` to communicate with this board.

## Build and upload

```powershell
cd "D:/Uni/IoT/Final/Camera"
pio run
pio run -t upload --upload-port COMx
pio device monitor --port COMx --baud 115200
```

Replace COMx with the camera board's USB-to-UART port. No fixed COM port is
configured, and building does not flash a board. This environment uses Arduino
on ESP32-S3, QIO flash with an 8 MB layout, and OPI PSRAM. Larger flash modules
can use this 8 MB layout. The firmware refuses camera initialization without
PSRAM. Use the board's USB-to-UART connector for default upload/debug output.

## Wiring

| Things ESP32 | Camera ESP32-S3 |
| --- | --- |
| GPIO17 TX | GPIO1 RX |
| GPIO16 RX | GPIO2 TX |
| GND | GND |

UART is 921600 baud, 8N1, 3.3 V logic. Each board needs its normal power supply.
These UART pins are intentionally separate from the camera connector,
USB pins, and OPI PSRAM pins. Change `include/camera_board.h` if your wiring differs.
Camera pins follow the Freenove ESP32S3_EYE mapping documented in the
[official board configuration](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board/blob/main/C/Sketches/Sketch_07.1_CameraWebServer/board_config.h)
and [camera pin table](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board/blob/main/C/Sketches/Sketch_07.3_Camera_SDcard/camera_pins.h).

## Protocol (compatible with Things)

Request: ASCII `CAPTURE,<uint32 sequence>\n` (CRLF also accepted).

Successful response: `IMG0` + sequence (4 bytes little endian) + JPEG length
(4 bytes little endian) + JPEG bytes. Capture failure: `ERR0` + sequence
(4 bytes little endian) + zero length (4 bytes). Logs never go onto UART1.
Malformed/overflowed command lines are discarded through the next newline.
Capture uses SVGA 800x600, JPEG quality 15, one PSRAM buffer, XCLK 20 MHz,
and a 256 KiB transmit limit. A queued old frame is discarded before each
capture. The framebuffer remains allocated until UART transmission finishes.

Things receives/validates the JPEG, uploads it on `/image`, then publishes
`capture_succeeded` on `/events` after the image upload is queued successfully.
This camera firmware has no MQTT client and does not publish an event itself.
It never sends an `IMG0` packet for an invalid/oversized image or failed capture.
If camera initialization fails, valid requests receive `ERR0`; reset after
fixing the camera connection or PSRAM settings. Transmission failure is handled
by the existing Things timeout (30 seconds), not a false success response.

## Verification

PlatformIO build passed for `freenove_esp32_s3_camera` (Espressif32 7.1.3).
The UART packet layout has been checked against Things' receiver. Actual sensor
startup, image color, UART wiring and successful image reception require a
physical test with the S3 and Things boards; no firmware is automatically flashed.

## Image orientation

Each captured 800x600 JPEG is decoded in PSRAM, rotated **90 degrees counterclockwise**,
and re-encoded as a **600x800 JPEG** before the IMG0 UART packet is sent.
Sensor JPEG quality remains 15; software encoder quality is 85/100 (a different
scale). Temporary RGB buffers use about 2.75 MiB of PSRAM. The encoded image is
bounded to 256 KiB. Allocation/decode/encode/size failures produce ERR0, never an
unrotated fallback image. The rotation log includes elapsed milliseconds for
checking the existing Things 30-second response timeout. This processing adds
latency and JPEG recompression; actual timing and color require a board test.
Compile-time non-square fixtures check the CCW coordinate mapping.
