# Cloud public API reference

Import the public interface from `Cloud`. Protocol adapters are internal details;
consumers do not need to import `blynk_http` or `blynk_mqtt`.

```python
from Cloud import CloudClient, CloudConfig, CloudError, PumpCommand
```

See the [setup guide](README.md#setup) for dependencies and credentials, and
[live examples](README.md#verification-and-live-examples) for runnable scripts.

## CloudConfig

An immutable configuration object. Invalid configuration raises `ValueError`.

```python
CloudConfig(
    server: str,
    auth_token: str,
    http_timeout: float = 10.0,
    command_lifetime: float = 5.0,
    mqtt_port: int = 8883,
    mqtt_keepalive: int = 45,
)
CloudConfig.from_env() -> CloudConfig
```

| Parameter | Meaning and validation |
| --- | --- |
| `server` | Lowercase regional hostname such as `sgp1.blynk.cloud`; no scheme, path, or port. |
| `auth_token` | Nonempty device token without whitespace or nonprintable characters; excluded from `repr`. |
| `http_timeout` | Positive finite timeout in seconds, passed to Requests and also used for MQTT connection establishment. It is not a total upload deadline. |
| `command_lifetime` | Positive finite seconds from local receipt until command expiry; expiry must fit Python's datetime range. |
| `mqtt_port` | Must be integer `8883`; TLS verification is enabled. |
| `mqtt_keepalive` | Integer seconds from 1 to 65535. |

`from_env()` reads `BLYNK_SERVER` and `BLYNK_AUTH_TOKEN` from the repository-root
`.env`, independently of the working directory. Process environment values take
precedence, including empty values, which fail validation. Loading does not
modify the process environment. Other settings retain their defaults; override
them explicitly:

```python
from dataclasses import replace

config = replace(CloudConfig.from_env(), http_timeout=3.0, command_lifetime=5.0)
```

## CloudClient lifecycle

```python
CloudClient(config: CloudConfig, *, node_id: str)
```

`node_id` must be a nonempty string. One client binds one edge node to the Blynk
device identified by its token. Construction opens no network connection.
Every upload checks that its `node_id` exactly matches the constructor value;
this is a local identity check, not a field sent to Blynk.

| Member | Return | Behavior |
| --- | --- | --- |
| `start()` | `None` | Starts MQTT reception in a background thread. Does not wait for connection or subscription. Not required for HTTP uploads. |
| `commands_ready` | `bool` | True only while connected with an accepted command subscription. Does not indicate physical pump readiness or HTTP availability. |
| `last_command_error` | `CloudError \| None` | Most recent MQTT failure, cleared after successful subscription. `None` alone does not mean connected. |
| `poll_command()` | `PumpCommand \| None` | Consumes the latest unexpired pending request without waiting for network I/O. |
| `close()` | `None` | Stops MQTT, clears pending requests, and releases HTTP resources; waits for an in-flight upload to finish. |

Repeated `start()` calls do nothing on an already-started client. Repeated
`close()` calls do nothing. A closed client cannot restart or upload and raises
`CloudError(category="closed")` for those operations; polling returns `None`.
After a terminal MQTT error, close the client, correct configuration, and create
a new client. Calling `start()` again does not reset a terminal error.

Transient MQTT connection failures retry automatically. Authentication,
subscription, and redirect failures stop reception and are exposed through
`last_command_error`. See the [connection behavior](README.md#commands-intent-versus-confirmed-execution).

## Upload methods

All upload arguments are **keyword-only**: use `node_id="node-1"`, not a
positional argument. Uploads are synchronous, serialized, and return `None`
after a successful response. Schedule them outside MQTT callbacks and
time-sensitive edge work. There is no automatic HTTP retry or offline buffer.

### send_snapshot

```python
cloud.send_snapshot(
    *, node_id: str,
    soil_index_pct: int | float | None = None,
    temperature_c: int | float | None = None,
    air_humidity_pct: int | float | None = None,
    plant_status: str | None = None,
    predicted_moisture_pct: int | float | None = None,
    pump_is_on: bool | None = None,
) -> None
```

Uploads all supplied fields in one HTTP batch. `None` omits a field; it does not
clear its dashboard value. An empty snapshot makes no request. `0`, `False`,
and an empty string are supplied values, not omissions.

| Parameter | Unit/type | Pin | Validation/conversion |
| --- | --- | --- | --- |
| `soil_index_pct` | Percentage | V0 | 0–100; rounded to integer, halves upward. |
| `temperature_c` | Celsius | V1 | Finite number; sent as a float. |
| `air_humidity_pct` | Percentage | V6 | 0–100; rounded to integer, halves upward. |
| `plant_status` | String | V2 | Caller supplies assessment text. |
| `predicted_moisture_pct` | Percentage | V5 | 0–100; rounded to integer, halves upward. |
| `pump_is_on` | Boolean | V4 | Physically confirmed state; converted to `0`/`1`. |

Booleans are rejected as measurements. NaN, infinity, and out-of-range
percentages are rejected before sending. Blynk-specific dashboard limits can
still cause an HTTP rejection. V3 is never uploaded. These are current-value
updates; preserve original sample timestamps in the edge's own records.

### Convenience methods

These delegate to the same snapshot validation and HTTP path:

```python
cloud.send_telemetry(
    *, node_id: str,
    soil_index_pct: int | float | None = None,
    temperature_c: int | float | None = None,
    air_humidity_pct: int | float | None = None,
) -> None

cloud.send_ai_results(
    *, node_id: str,
    plant_status: str | None = None,
    predicted_moisture_pct: int | float | None = None,
) -> None

cloud.report_pump_status(*, node_id: str, is_on: bool) -> None
```

Use `send_snapshot` when one processing cycle produces both readings and AI
results, avoiding separate requests. `report_pump_status` requires a boolean;
`None` is not accepted. Call it only after the device confirms its actual state.

### Errors

- `ValueError`: invalid values or a node ID that does not match the binding.
- `CloudError`: closed client, timeout, connection failure, redirect, or HTTP error.
- `TypeError`: missing required arguments or unsupported argument names.

If an upload times out, the server may already have received the values; the
exception does not prove the update was rolled back.

```python
cloud = CloudClient(CloudConfig.from_env(), node_id="node-1")
try:
    cloud.send_snapshot(
        node_id="node-1", soil_index_pct=42.5, temperature_c=28.5,
        air_humidity_pct=65, plant_status="Healthy", predicted_moisture_pct=40,
    )
except ValueError as error:
    print(f"Invalid upload: {error}")
except CloudError as error:
    print(f"Upload failed: {error.category}, HTTP status: {error.status_code}")
finally:
    cloud.close()
```

## PumpCommand and polling

Obtain immutable `PumpCommand` objects from `poll_command()`; consumers should
not construct them manually or use the private `_deadline` field.

| Member | Type | Meaning |
| --- | --- | --- |
| `node_id` | `str` | The client's bound target node. |
| `requested_on` | `bool` | User intent: `True` for ON, `False` for OFF. |
| `received_at` | `datetime` | Timezone-aware UTC time of local receipt. |
| `expires_at` | `datetime` | UTC expiry for display and logging. |
| `is_expired()` | `bool` | Checks a monotonic deadline, unaffected by wall-clock changes. |

Only the latest pending state is retained. ON then OFF before polling yields
OFF only. A successful poll consumes the request; another poll returns `None`
unless a new request arrived. Expired requests, retained messages, and malformed
payloads are not returned. Disconnects clear pending requests, and commands sent
while disconnected are not guaranteed to arrive later.

Example insertion into an existing edge loop (the `controller` is supplied by
the edge application, not by Cloud):

```python
# Once during application startup:
cloud.start()

# On each iteration of the edge's processing loop:
command = cloud.poll_command()
if command is not None and not command.is_expired():
    controller.consider_request(command)

# Separately, in the device-confirmation handling path:
cloud.report_pump_status(node_id=confirmed_node_id, is_on=confirmed_state)
```

The controller must check expiry again immediately before actuation, apply
authorization and duration limits, and handle duplicate requests safely.
Repeated ON requests must not restart or extend watering automatically.
Polling is not an execution acknowledgement, and the package cannot recall an
already-polled request. Receipt-time expiry is not a guarantee of server-side
freshness or exactly-once delivery. Never report V4 directly from V3.

## CloudError

`CloudError` is an exception with a sanitized message, a string `category`, and
`status_code: int | None`. HTTP operations raise it; asynchronous MQTT failures
are normally available through `last_command_error`. Immediate MQTT startup
failures can also be raised by `start()`.

| Category | Meaning / caller action |
| --- | --- |
| `http` | Blynk rejected an upload; inspect `status_code` and configuration/quota. |
| `timeout` | HTTP request timed out; caller decides whether to retry. |
| `network` | HTTP failed or MQTT is disconnected/retrying; inspect which operation reported it. |
| `redirect` | Set the correct regional `BLYNK_SERVER`; redirects are not followed. |
| `authentication` | MQTT credentials rejected; correct the token. |
| `connection` | Terminal MQTT connection refusal; check device configuration. |
| `subscription` | MQTT subscription failed; check configuration/permissions. |
| `startup` | MQTT network loop could not start. |
| `closed` | Create a new client if further operations are required. |

For MQTT terminal errors, close the old client before creating its replacement.
Only one MQTT connection may use a device token at a time. Messages omit
credentials and request URLs; do not add token-bearing configuration to logs.
