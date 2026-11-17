class BlynkPins:
    SOIL_MOISTURE = "v0"
    TEMPERATURE = "v1"
    PLANT_STATUS = "v2"
    PUMP_COMMAND = "v3"
    PUMP_STATUS = "v4"
    PREDICTED_MOISTURE = "v5"
    AIR_HUMIDITY = "v6"
    NODE_ID = "v7"
    NODE_DESCRIPTION = "v8"
    PLANT_NAME = "v9"
    PERMANENT_WILTING_POINT = "v10"
    MANAGEMENT_ALLOWED_DEPLETION = "v11"
    FIELD_CAPACITY = "v12"
    SATURATION_POINT = "v13"
    APPLY_PROFILE = "v14"
    PROFILE_STATUS = "v15"
    PROFILE_PRESET = "v16"


class BlynkTopics:
    PUMP_COMMAND = "downlink/ds/Pump Command"
    REDIRECT = "downlink/redirect"
    APPLY_PROFILE = "downlink/ds/apply_profile"
    PROFILE_PRESET = "downlink/ds/profile_preset"


PROFILE_FIELDS = {
    BlynkPins.NODE_ID: "node_id",
    BlynkPins.NODE_DESCRIPTION: "node_description",
    BlynkPins.PLANT_NAME: "plant_name",
    BlynkPins.PERMANENT_WILTING_POINT: "permanent_wilting_point",
    BlynkPins.MANAGEMENT_ALLOWED_DEPLETION: "management_allowed_depletion",
    BlynkPins.FIELD_CAPACITY: "field_capacity",
    BlynkPins.SATURATION_POINT: "saturation_point",
}
