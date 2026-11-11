#include "TopicBuilder.h"

String TopicBuilder::telemetryTopic(
    const String& siteId,
    const String& deviceId
) {
    return "iot/" + siteId + "/" + deviceId + "/telemetry";
}