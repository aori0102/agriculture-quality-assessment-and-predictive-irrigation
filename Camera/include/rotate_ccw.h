#pragma once
#include <stddef.h>

// Input (x,y) -> output (y, width-1-x); output dimensions are height x width.
constexpr size_t ccwIndex(size_t x, size_t y, size_t width, size_t height) {
    return (width - 1 - x) * height + y;
}
// Non-square fixture: [A B C; D E F] must become [C F; B E; A D].
static_assert(ccwIndex(2, 0, 3, 2) == 0, "CCW top-left must be C");
static_assert(ccwIndex(2, 1, 3, 2) == 1, "CCW top-right must be F");
static_assert(ccwIndex(1, 0, 3, 2) == 2, "CCW middle-left must be B");
static_assert(ccwIndex(1, 1, 3, 2) == 3, "CCW middle-right must be E");
static_assert(ccwIndex(0, 0, 3, 2) == 4, "CCW bottom-left must be A");
static_assert(ccwIndex(0, 1, 3, 2) == 5, "CCW bottom-right must be D");
