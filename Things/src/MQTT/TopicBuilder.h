#pragma once

#include <Arduino.h>

class TopicBuilder {
public:
    static String telemetryTopic(
        const String& edgeId,
        const String& nodeId
    );

    static String commandTopic(
        const String& edgeId,
        const String& nodeId
    );

    static String ackTopic(
        const String& edgeId,
        const String& nodeId
    );
};