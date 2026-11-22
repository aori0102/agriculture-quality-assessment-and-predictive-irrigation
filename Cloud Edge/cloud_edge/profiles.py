"""Pure profile contracts shared with Edge; no persistence or irrigation policy."""

from dataclasses import dataclass
import math
from typing import Literal, Protocol

from .datastreams import PROFILE_FIELDS, BlynkPins


PRESET_KEY = "illustrative_lettuce"
# Adapted from the repository's example, NOT agronomically established values.
PRESET_THRESHOLDS = (0.10, 0.184, 0.22, 0.41)


def _text(value: str, name: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or len(value) > 128 or any(
        not char.isprintable() for char in value
    ) or (not allow_empty and not value.strip()):
        raise ValueError(f"{name} must be printable text of at most 128 characters.")


@dataclass(frozen=True)
class PlantProfile:
    node_id: str
    node_description: str
    plant_name: str
    permanent_wilting_point: float
    management_allowed_depletion_point: float
    field_capacity: float
    saturation_point: float
    mode: Literal["custom", "preset"] = "custom"
    preset_key: str | None = None

    def __post_init__(self) -> None:
        _text(self.node_id, "node_id")
        _text(self.node_description, "node_description", allow_empty=True)
        _text(self.plant_name, "plant_name")
        for name in THRESHOLD_FIELDS:
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite number between 0 and 1.")
        if not 0 <= self.permanent_wilting_point < self.madp < self.field_capacity < self.saturation_point <= 1:
            raise ValueError("Require 0 <= PWP < MADP < FC < SP <= 1.")
        if self.mode == "preset":
            if self.preset_key != PRESET_KEY:
                raise ValueError("Unknown profile preset.")
            if not all(math.isclose(getattr(self, name), expected, rel_tol=1e-9, abs_tol=1e-12)
                       for name, expected in zip(THRESHOLD_FIELDS, PRESET_THRESHOLDS)):
                raise ValueError("Switch to Custom to use edited thresholds.")
        elif self.mode != "custom" or self.preset_key is not None:
            raise ValueError("Custom profiles must not specify a preset key.")

    @property
    def madp(self) -> float:
        return self.management_allowed_depletion_point

    def to_datastreams(self) -> dict[str, str | float | int]:
        values = {pin: getattr(self, name) for pin, name in PROFILE_FIELDS.items()}
        values[BlynkPins.PROFILE_PRESET] = int(self.mode == "preset")
        return values


THRESHOLD_FIELDS = (
    "permanent_wilting_point", "management_allowed_depletion_point",
    "field_capacity", "saturation_point",
)


def validate_snapshot(values: dict, *, node_id: str) -> PlantProfile:
    """Validate Blynk's scalar JSON values without converting strings/arrays."""
    if not isinstance(values, dict) or not set(PROFILE_FIELDS).union({BlynkPins.PROFILE_PRESET}) <= values.keys():
        raise ValueError("Profile snapshot is missing required datastreams.")
    if values[BlynkPins.NODE_ID] != node_id:
        raise ValueError("node_id does not match this device's binding.")
    selector = values[BlynkPins.PROFILE_PRESET]
    if type(selector) is not int or selector not in (0, 1):
        raise ValueError("profile_preset must be 0 (Custom) or 1 (Illustrative Lettuce).")
    return PlantProfile(
        **{name: values[pin] for pin, name in PROFILE_FIELDS.items()},
        mode="preset" if selector else "custom",
        preset_key=PRESET_KEY if selector else None,
    )


def illustrative_preset(node_id: str, description: str = "") -> PlantProfile:
    return PlantProfile(node_id, description, "Illustrative Lettuce", *PRESET_THRESHOLDS,
                        mode="preset", preset_key=PRESET_KEY)


@dataclass(frozen=True)
class ActiveProfile:
    """Confirmed state. saved_only distinguishes storage from irrigation adoption."""
    profile: PlantProfile
    revision: int | None
    irrigation_note: str = "Irrigation eligibility not reported by Edge"

    metadata_complete: bool = True
    saved_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.profile, PlantProfile):
            raise ValueError("active profile must contain a validated PlantProfile.")
        if self.revision is not None and (type(self.revision) is not int or self.revision < 1):
            raise ValueError("revision must be a positive integer.")
        _text(self.irrigation_note, "irrigation_note")


@dataclass(frozen=True)
class ProfileApplyResult:
    """A failed operation returns the previous active state, if any.

    Messages must be safe for dashboard display (no credentials or internals).
    """
    outcome: Literal["applied", "saved", "rejected", "sync_failed"]
    active: ActiveProfile | None
    message: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in ("applied", "saved", "rejected", "sync_failed"):
            raise ValueError("Unknown profile application outcome.")
        if self.active is not None and not isinstance(self.active, ActiveProfile):
            raise ValueError("active must be an ActiveProfile or None.")
        if self.outcome in ("applied", "saved") and self.active is None:
            raise ValueError("Successful application requires Edge-confirmed active state.")
        _text(self.message, "message", allow_empty=True)


class ProfileBackend(Protocol):
    """Integration boundary, implemented by an adapter or Edge-supplied backend.

    Called on a Cloud worker, never an MQTT callback.

    Operations must be bounded and thread-safe across node workers. Persist
    atomically when supported; report uncertainty when legacy storage fails. Make
    identical applications idempotent. These methods must not actuate hardware.
    """

    def get_active(self, node_id: str) -> ActiveProfile | None: ...

    def apply_profile(self, profile: PlantProfile) -> ProfileApplyResult: ...


def profiles_match(left: PlantProfile, right: PlantProfile) -> bool:
    """Compare round-tripped fractions without discarding metadata differences."""
    return all(getattr(left, key) == getattr(right, key) for key in (
        "node_id", "node_description", "plant_name", "mode", "preset_key"
    )) and all(math.isclose(getattr(left, key), getattr(right, key), rel_tol=1e-9, abs_tol=1e-12)
               for key in THRESHOLD_FIELDS)
