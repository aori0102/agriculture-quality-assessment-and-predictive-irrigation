#pragma once
#include <stdint.h>

// ERR1 + uint32 sample sequence + uint32 error code, all integers little-endian.
enum class CaptureError : uint32_t {
    CameraNotReady = 1, FrameUnavailable = 2, InvalidSourceJpeg = 3,
    SourceTooLarge = 4, UnexpectedResolution = 5, RgbAllocationFailed = 6,
    JpegDecodeFailed = 7, OutputAllocationFailed = 8, JpegEncodeFailed = 9,
    OutputTooLarge = 10, InvalidRotatedJpeg = 11
};
inline const char* captureErrorName(uint32_t code) {
    switch (static_cast<CaptureError>(code)) {
        case CaptureError::CameraNotReady: return "camera_not_ready";
        case CaptureError::FrameUnavailable: return "camera_frame_unavailable";
        case CaptureError::InvalidSourceJpeg: return "source_jpeg_invalid";
        case CaptureError::SourceTooLarge: return "source_jpeg_too_large";
        case CaptureError::UnexpectedResolution: return "rotation_requires_800x600";
        case CaptureError::RgbAllocationFailed: return "rotation_rgb_psram_allocation_failed";
        case CaptureError::JpegDecodeFailed: return "jpeg_decode_failed";
        case CaptureError::OutputAllocationFailed: return "jpeg_output_psram_allocation_failed";
        case CaptureError::JpegEncodeFailed: return "rotated_jpeg_encode_failed";
        case CaptureError::OutputTooLarge: return "rotated_jpeg_exceeds_256KiB";
        case CaptureError::InvalidRotatedJpeg: return "rotated_jpeg_invalid";
        default: return "unknown_camera_error_code";
    }
}
