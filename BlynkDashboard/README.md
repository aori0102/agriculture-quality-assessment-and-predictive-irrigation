# Plant profile HTML widget

This widget follows `Cloud Edge/cloud_edge/datastreams.py` and the existing
`ProfileSync` backend. Keep those streams unchanged. Paste/upload `widget.html`
to Blynk's HTML Widget, choose **Specific datastreams**, and assign this order:

| Widget index | Existing pin | Exact datastream name | Type / range |
| --- | --- | --- | --- |
| 0 | V11 | management_allowed_depletion_point | Double 0–1 |
| 1 | V12 | field_capacity | Double 0–1 |
| 2 | V10 | permanent_wilting_point | Double 0–1 |
| 3 | V13 | saturation_point | Double 0–1 |
| 4 | V2 | plant_status | String |
| 5 | V14 | apply_profile | Integer 0–1 |
| 6 | V15 | profile_status | String |
| 7 | V16 | profile_preset | Integer 0–1 |
| 8 | V7 | node_id | String |
| 9 | V8 | node_description | String |
| 10 | V9 | plant_name | String |
| 11 (optional) | V19 | node_catalog | String, read-only navigation catalog |

The widget positions are not virtual pin numbers. Do not use **All datastreams**.
Show at least three decimal places on the threshold streams. Do not change the
backend's 0–1 range to 0–100: the widget converts `.35` to `35%` for display.
All thresholds are moisture values: 0 <= PWP < MADP < FC < SAT <= 1. Slider edits use steps of
0.1 percentage point and preserve unedited fractional precision.

## Device mapping and navigation

No node IDs or Blynk internal device IDs need to be configured in the HTML.
The widget reads V7 `node_id` from the device whose dashboard hosts it. Any
nonempty printable node ID of at most 128 characters is accepted, with its exact
value preserved. V7 is read-only in this widget. Empty/invalid V7 blocks Apply
with a clear message, while slider and plant-name edits remain local and usable.

Configure the node-to-device-token mapping once in Cloud Edge's root `.env`
using `BLYNK_DEVICES_JSON` (see `Cloud Edge/.env.example`). The existing Cloud
Edge profile restoration publishes V7. This UI does not register nodes in Edge;
the node must already exist there for its profile to be saved.

The first valid V7 value identifies this page session. If V7 changes or becomes
invalid later, including during Apply readback, reload before submitting. The
widget never transfers an existing draft to a different node. Missing V7 may be
populated later without a reload when no identity has yet been established.

The description below the plant name displays V8 `node_description`. When V8
is empty or unavailable, it displays: “Monitor soil moisture and adjust irrigation
settings for this plant.” This is a display-only fallback; it does not overwrite
V8 or count as an unapplied change.
The Blynk template default is not a guarantee that a device has a current V8
value. An assigned V8 with an unset/null value is treated as the allowed empty
description when applying a profile; a missing widget binding still blocks Apply.

Plant name loads from V9; use the small **Edit** button beside it to edit a local
draft. **Done** or Enter closes the editor without sending anything; Escape
cancels that editing session and Reset restores all loaded fields. **Apply Changes**
writes the new name to V9 `plant_name`, verifies readback, then triggers the
existing V14 Edge save. Names require 1–128 printable characters.

Unreported species/growth information is not invented
in live mode. Ensure the Edge has restored a valid profile, plant name (or enter one locally),
and description (which may be empty).

Bind V19 at index 11 to populate **Go to node…** from the Edge registry. Edge
publishes only node IDs, numeric Blynk device IDs, and regional hosts; no tokens.
Newly provisioned nodes appear after Edge accepts their telemetry and creates a
Blynk binding. Without V19, the dropdown shows only the current node. Static
bindings can include an optional numeric `device_id` in `BLYNK_DEVICES_JSON` to
make their cross-node links available. The current node can use Blynk's own
device info when its static ID is omitted. The BTL dashboard route ID remains
configured in `widget.html`; changing the dashboard route requires updating it.
The catalog has a 1024-byte limit and shows an explicit error when exceeded.
Apply or Reset local edits before selecting another node. Opening a node uses
its Blynk dashboard in a new tab; the editor remains bound to its current node.

For the live BTL dashboard, replace the HTML widget code with
`plant_profile_dynamic.html` from **My Widgets** (or paste this repository's
`widget.html`), assign V19 after the existing 11 streams, save the template,
and apply the dashboard template to its devices. Restart the combined Edge
process to publish the current catalog; later dynamic registrations refresh it.

V16 is a different concept: 0 Custom, 1 Illustrative Lettuce preset. This widget
edits thresholds, so it automatically switches to Custom on Apply if needed.
Preset selection can still be done using the existing native Blynk control.
Wait for that preset preview to finish before editing in this widget.

Sliders remain editable even while setup or incoming threshold values are incomplete.
Edits remain local. A message above the sliders explains any identity, binding or
metadata problem blocking Apply. Fill all four valid values before submitting;
sliders are locked only while a submission is in progress.

## Apply and confirmation

1. Moving any of the four sliders changes only the local draft. Reset restores
   the last loaded baseline. Blynk values may themselves be unsaved drafts;
   loading them is not proof of Edge persistence.
2. Apply locks editing and reads all bound values. It refuses an already-pending
   V14 or a different node identity.
3. If V16 is 1, it writes 0 and waits for a newer `DRAFT: Custom; …` status before
   uploading thresholds. Changing mode alone does not save an Edge profile.
4. It writes the four thresholds as fractions, then reads back all four values,
   Custom mode, and submitted plant name and unchanged node/description before triggering V14=1.
   It writes V9 only if the name changed and does not modify V7–V8. The backend resets V14; the widget does not send an
   immediate release that could conceal a pending operation.
5. It waits for a newer `SAVED:` or `APPLIED:` status for this node and matching
   form readback. `REJECTED:` and `SYNC_FAILED:` are displayed as failures.
   Restore messages containing `unsaved drafts discarded` are not success.
   Status timestamps must be available; stale or untimestamped statuses cannot
   confirm a save. A missing timestamp prevents triggering Apply after readback.
6. Success says **Saved to Edge** and does not claim irrigation adoption or pump
   execution. Errors/timeouts preserve the local draft. The sequence has a
   35-second polling deadline, plus any in-flight bridge read timeout.

Use **one editor per device**, including native widgets. The scalar protocol
has no request ID or atomic multi-stream transaction. Readback and newer status
checks reduce errors but cannot prove which submission produced a delayed status
or protect against a second writer. After an uncertain result, reload and inspect
Edge state before retrying; the widget never automatically retries Apply. Partial
uploads can remain in Blynk even when Apply was never triggered. Saving the Edge
profile still requires the existing Cloud Edge process and valid AI startup.
See [Cloud Edge profile setup](../Cloud%20Edge/PROFILES.md).

## Verification

Run dependency-free regression tests (Node with test-isolation support):

```sh
node --test --test-isolation=none BlynkDashboard/tests/widget.test.cjs
```

Tests execute the actual widget script with DOM/bridge doubles and verify unit
conversion, local editing, Custom sequencing, readback before Apply, stale status,
errors, and navigation. Browser preview uses illustrative data saved in memory.
Live Blynk delivery, iframe navigation, Edge persistence and actual AI runtime
startup must be checked in Blynk's Testing tab with the real configured device.

References:

- [Blynk HTML Widget](https://docs.blynk.io/en/blynk.console/widgets-console/html-widget)
- [Official BlynkBridge reference](https://github.com/blynkkk/docs/blob/main/blynk.console/widgets-console/html-widget/html-widget-developer-guide.md)

V11 is a direct MADP threshold, not MAD. The backend writes it to Edge madp
without a depletion calculation. Upload the updated HTML and refresh open tabs
when changing the Blynk V11 name/meaning.
