
#include "TopicBuilder.h"

String TopicBuilder::telemetryTopic(
    const String& edgeId,
    const String& deviceId
) {
    return "iot/" + edgeId + "/" + deviceId + "/telemetry";
}
