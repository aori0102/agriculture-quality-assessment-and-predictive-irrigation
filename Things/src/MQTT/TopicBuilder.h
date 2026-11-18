
#pragma once

#include <Arduino.h>

class TopicBuilder {
public:
    static String telemetryTopic(
        const String& edgeId,
        const String& deviceId
    );
};
