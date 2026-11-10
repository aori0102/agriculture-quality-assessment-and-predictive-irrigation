import json
import random
import IrrigationPrediction.field_constants as fc

data = {
    fc.SOIL_MOISTURE_PERCENTAGE: random.uniform(0, 100),
    fc.AIR_TEMP_C: random.uniform(5, 20),
    fc.AIR_RELATIVE_HUMIDITY_PERCENTAGE: random.uniform(0, 100),
}

payload = json.dumps(data)
print(payload)