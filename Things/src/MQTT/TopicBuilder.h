#pragma once

#include <Arduino.h>

class TopicBuilder {
public:
    static String telemetryTopic(
        const String& siteId,
        const String& deviceId
    );
};