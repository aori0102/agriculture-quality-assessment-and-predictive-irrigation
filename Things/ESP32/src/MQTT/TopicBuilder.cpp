#include "TopicBuilder.h"

namespace {
    constexpr const char* TOPIC_PREFIX = "iot";
    constexpr const char* TELEMETRY_SUFFIX = "telemetry";
    constexpr const char* COMMAND_SUFFIX = "command";
    constexpr const char* ACK_SUFFIX = "ack";
}

String TopicBuilder::telemetryTopic(
    const String& edgeId,
    const String& nodeId
) {
    return String(TOPIC_PREFIX) + "/" +
           edgeId + "/" +
           nodeId + "/" +
           TELEMETRY_SUFFIX;
}

String TopicBuilder::commandTopic(
    const String& edgeId,
    const String& nodeId
) {
    return String(TOPIC_PREFIX) + "/" +
           edgeId + "/" +
           nodeId + "/" +
           COMMAND_SUFFIX;
}

String TopicBuilder::ackTopic(
    const String& edgeId,
    const String& nodeId
) {
    return String(TOPIC_PREFIX) + "/" +
           edgeId + "/" +
           nodeId + "/" +
           ACK_SUFFIX;
}