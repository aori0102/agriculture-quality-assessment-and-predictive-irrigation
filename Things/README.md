# Things configuration

The web configuration portal includes a pump PWM slider from 0 to 255, default
200. The current numeric duty is displayed beside it. Save Configuration writes
`pump_duty` to NVS and restarts the node; startup applies this value to the pump
controller. Adjusting the slider alone does not run the pump. PWM 0 prevents a
pump start. The server rejects missing/non-integer/out-of-range PWM values before
saving; compile-time checks cover boundaries, malformed values and overflow.

Defaults for a new node (or absent NVS keys): MQTT host `broker.hivemq.com`,
port `1883`, telemetry interval `10000` ms, pump PWM `200`. Existing valid NVS
settings are retained; use the portal to change an already configured device.
Empty host, zero port and zero interval fall back to defaults. First boot also
initializes defaults when the NVS namespace is not yet present.

Build with `pio run`, then upload the Things firmware. Hardware PWM behavior and
browser slider interaction require a device test; no board is flashed by a build.

## Status LED (GPIO23)

- One flash: WiFi disconnected.
- Two flashes: WiFi connected, MQTT disconnected.
- Three flashes: configuration portal/AP is open (highest priority).
- Steady on: WiFi and MQTT connected while the portal is closed.

Each flash is 150 ms on / 150 ms off, followed by an additional 1000 ms pause
between groups. Opening/closing the portal resets the pattern. The portal
pattern also updates during startup WiFi/NTP waiting loops. Save restarts the
node, returning the LED to its current connection state. This indicates portal
availability, not whether a phone is connected or viewing the page.

## Close configuration with the button

Hold GPIO25 for five seconds to open the configuration portal. Release it, then
press and release once (less than one second) to close the portal/AP. Releasing
the original opening hold does not close it. Closing stops DNS/HTTP and the AP;
the station/MQTT connection can continue, and the LED returns to connection status.
Closing does not save form edits or restart the device. Existing restart and
sleep hold gestures remain unchanged.
