"""Adapter to existing Edge callables; never imports AI or writes its storage."""

from dataclasses import replace
import json
import math
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Callable

from .profiles import ActiveProfile, PlantProfile, ProfileApplyResult

DEFAULT_METADATA_PATH = Path(__file__).resolve().parents[1] / 'runtime' / 'profile_metadata.json'
NUMERIC_KEYS = ('pwp', 'madp', 'fc', 'sp')


def _matches(left: dict, right: dict) -> bool:
    if any(left.get(key) != right.get(key) for key in ('node_id', 'plant')):
        return False
    return all(type(left.get(key)) in (int, float) and type(right.get(key)) in (int, float)
               and math.isclose(left[key], right[key], rel_tol=1e-9, abs_tol=1e-12)
               for key in NUMERIC_KEYS)


def _core(profile: PlantProfile) -> dict:
    return dict(node_id=profile.node_id, plant=profile.plant_name,
                pwp=profile.permanent_wilting_point, madp=profile.madp,
                fc=profile.field_capacity, sp=profile.saturation_point)


class EdgeProfileBackend:
    """Share ONE instance among clients, serializing our access to the Edge log.

    The lock cannot synchronize independent AI writers. A failed write may have
    changed Edge state: we report uncertainty rather than promising rollback.
    """

    def __init__(self, update_node_profile: Callable, load_log: Callable,
                 metadata_path: Path = DEFAULT_METADATA_PATH, *, operation_lock=None):
        if not callable(update_node_profile) or not callable(load_log):
            raise ValueError('Edge update and read functions must be callable.')
        self._update = update_node_profile
        self._load_log = load_log
        self._metadata_path = Path(metadata_path)
        self._lock = operation_lock if operation_lock is not None else RLock()

    def _record(self, node_id: str) -> dict | None:
        log = self._load_log()
        if not isinstance(log, dict) or not isinstance(log.get('node_profile'), dict):
            raise ValueError('Edge operation log has an invalid profile collection.')
        record = log['node_profile'].get(node_id)
        if record is not None and (not isinstance(record, dict) or record.get('node_id') != node_id):
            raise ValueError('Edge returned an invalid node record.')
        return record

    def _decode(self, node_id: str, record: dict) -> PlantProfile:
        for key in NUMERIC_KEYS:
            if type(record.get(key)) not in (int, float) or not 0 <= record[key] <= 1:
                raise ValueError('Saved Edge thresholds must be finite fractions; units cannot be inferred.')
        pwp, madp, fc, sp = (record[key] for key in NUMERIC_KEYS)
        if not 0 <= pwp < madp < fc < sp <= 1:
            raise ValueError('Saved Edge thresholds have invalid ordering.')
        mad = (fc - madp) / (fc - pwp)
        return PlantProfile(node_id, '', record.get('plant'), pwp, mad, fc, sp)

    def _load_metadata(self) -> dict:
        try:
            data = json.loads(self._metadata_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {'version': 1, 'nodes': {}}
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('nodes'), dict):
            raise ValueError('Invalid integration metadata document.')
        return data

    def _confirmed(self, profile: PlantProfile, *, complete: bool) -> ActiveProfile:
        return ActiveProfile(profile, None, 'Saved values verified; irrigation adoption and VWC eligibility unverified',
                             metadata_complete=complete, saved_only=True)

    def get_active(self, node_id: str) -> ActiveProfile | None:
        with self._lock:
            record = self._record(node_id)
            if record is None:
                return None
            profile = self._decode(node_id, record)
            try:
                entry = self._load_metadata()['nodes'].get(node_id)
                if isinstance(entry, dict) and isinstance(entry.get('edge_values'), dict) and _matches(entry['edge_values'], _core(profile)):
                    profile = replace(profile, node_description=entry['node_description'],
                                      mode=entry['mode'], preset_key=entry['preset_key'])
                    return self._confirmed(profile, complete=True)
            except (OSError, ValueError, KeyError, TypeError):
                pass  # Verified core values remain usable; UI metadata is incomplete.
            return self._confirmed(profile, complete=False)

    def _save_metadata(self, profile: PlantProfile, record: dict) -> None:
        data = self._load_metadata()  # Do not silently overwrite a corrupt document.
        data['nodes'][profile.node_id] = {
            'node_description': profile.node_description, 'mode': profile.mode,
            'preset_key': profile.preset_key,
            'edge_values': {key: record[key] for key in ('node_id', 'plant', *NUMERIC_KEYS)},
        }
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=self._metadata_path.parent,
                                    prefix='.profile-', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._metadata_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def apply_profile(self, profile: PlantProfile) -> ProfileApplyResult:
        if not isinstance(profile, PlantProfile):
            raise ValueError('A validated PlantProfile is required.')
        with self._lock:
            try:
                before = self._record(profile.node_id)
                if before is None:
                    return ProfileApplyResult('rejected', None, 'Unknown Edge node; register it in Edge first')
                if not _matches(before, _core(profile)):
                    self._update(
                        node_id=profile.node_id, plant=profile.plant_name,
                        permanent_wilting_point=profile.permanent_wilting_point,
                        management_allowable_depletion=profile.management_allowed_depletion,
                        field_capacity=profile.field_capacity, saturation_point=profile.saturation_point,
                    )
                record = self._record(profile.node_id)
                if record is None:
                    raise ValueError('Node disappeared during update.')
                verified = self._decode(profile.node_id, record)
                if not _matches(_core(verified), _core(profile)):
                    raise ValueError('Edge readback differs from submitted values.')
            except Exception:
                return ProfileApplyResult('sync_failed', None,
                    'Edge write/readback failed; saved state is uncertain. Inspect Edge before retrying')
            try:
                self._save_metadata(profile, record)
            except Exception:
                return ProfileApplyResult('sync_failed', self._confirmed(verified, complete=False),
                    'Edge values saved; profile metadata incomplete')
            acknowledged = replace(verified, node_description=profile.node_description,
                                   mode=profile.mode, preset_key=profile.preset_key)
            return ProfileApplyResult('saved', self._confirmed(acknowledged, complete=True))
