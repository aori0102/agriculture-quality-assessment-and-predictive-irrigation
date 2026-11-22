"""Per-node configuration, heartbeat, durable fallback audit and reconnect gate.

No hardware or model imports. All methods are called by the Edge main loop.
"""
from datetime import datetime, timezone, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4
import json
import math
import os


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, allow_nan=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class NodeFallback:
    def __init__(self, client, edge_id, profiles, policy_path, state_path, clock=monotonic):
        self.client, self.edge_id, self.profiles = client, edge_id, profiles
        self.policy_path, self.state_path = Path(policy_path), Path(state_path)
        self.clock = clock
        self.session = uuid4().hex
        self.pending, self.ready, self.last_seen = {}, {}, {}
        self.sequence = 0
        self.last_tick = -float('inf')
        self.state = json.loads(self.state_path.read_text(encoding='utf-8')) if self.state_path.exists() else {}
        self.state.setdefault('configs', {})
        self.state.setdefault('events', {})
        self.state.setdefault('quarantine_until', {})

    def publish(self, node, suffix, payload, qos=1):
        topic = f'iot/{self.edge_id}/{node}/{suffix}'
        return self.client.publish(topic, json.dumps(payload, allow_nan=False), qos=qos, retain=False).rc == 0

    def snapshot(self, node, profile, options):
        def integer(name, default, low, high):
            value = options.get(name, default)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(name)
            return value
        enabled = options.get('enabled', False)
        if type(enabled) is not bool:
            raise ValueError('enabled')
        emergency = options.get('emergency_threshold_pct', (profile.pwp + profile.madp) / 2)
        rearm = options.get('rearm_threshold_pct', profile.madp)
        thresholds = (profile.pwp, emergency, rearm, profile.madp, profile.fc)
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in thresholds):
            raise ValueError('thresholds')
        if not (0 <= profile.pwp < emergency < rearm <= profile.fc <= 100 and emergency < profile.madp < profile.fc):
            raise ValueError('threshold ordering')
        return dict(schema_version=1, edge_id=self.edge_id, node_id=node,
                    fallback_enabled=enabled and profile.auto_irrigation_enabled,
                    pwp_pct=profile.pwp, madp_pct=profile.madp, fc_pct=profile.fc,
                    emergency_threshold_pct=emergency, rearm_threshold_pct=rearm,
                    duration_ms=integer('duration_ms', 6000, 1, 60000),
                    cooldown_ms=integer('cooldown_ms', 300000, 1000, 3600000),
                    required_low_samples=integer('required_low_samples', 3, 1, 100),
                    max_sample_age_ms=integer('max_sample_age_ms', 30000, 1000, 300000),
                    edge_timeout_ms=integer('edge_timeout_ms', 30000, 15000, 300000),
                    max_runs_per_hour=integer('max_runs_per_hour', 3, 1, 32),
                    max_total_duration_ms_per_hour=integer('max_total_duration_ms_per_hour', 60000, 1, 1920000),
                    unlock_generation=integer('unlock_generation', 0, 0, 2147483647))

    def disconnected(self):
        self.ready.clear()
        self.last_seen.clear()
        self.pending.clear()

    def tick(self):
        now = self.clock()
        if now - self.last_tick < 5:
            return
        self.last_tick = now
        try:
            options = json.loads(self.policy_path.read_text(encoding='utf-8')) if self.policy_path.exists() else {'nodes': {}}
            if not isinstance(options, dict) or not isinstance(options.get('nodes'), dict):
                raise ValueError('Expected a nodes object')
        except (OSError, ValueError):
            options = {'nodes': {}}
        for node in self.profiles.read_all_recorded_nodes():
            profile = self.profiles.get_node_profile(node)
            if profile is None:
                continue
            try:
                body = self.snapshot(node, profile, options.get('nodes', {}).get(node, {}))
            except (ValueError, TypeError):
                # Invalid edited policy revokes the previous enabled snapshot.
                body = self.snapshot(node, profile, {'enabled': False})
            old = self.state['configs'].get(node)
            if old is None or old['body'] != body:
                old = {'version': (old['version'] + 1 if old else 1), 'body': body}
                self.state['configs'][node] = old
                atomic_json(self.state_path, self.state)
                self.ready.pop(node, None)
                self.pending.pop(node, None)
            if now - self.last_seen.get(node, -float('inf')) > 15:
                self.ready.pop(node, None)
            nonce = self.pending.setdefault(node, uuid4().hex)
            self.publish(node, 'config', dict(body, config_version=old['version'], session_id=self.session, sync_nonce=nonce))
            self.sequence += 1
            self.publish(node, 'heartbeat', dict(edge_id=self.edge_id, node_id=node,
                         session_id=self.session, sequence=self.sequence), qos=0)

    def receive(self, message, node):
        if message.retain or len(message.payload) > 8192:
            return False
        try:
            data = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(data, dict) or data.get('node_id') != node or data.get('edge_id') != self.edge_id:
            return False
        kind = data.get('event')
        if kind == 'config_rejected':
            self.ready.pop(node, None)
            return True
        if kind == 'config_applied':
            config = self.state['configs'].get(node)
            if (config and data.get('session_id') == self.session
                    and data.get('config_version') == config['version']
                    and data.get('sync_nonce') == self.pending.get(node) and node in self.pending):
                self.last_seen[node] = self.clock()
                if data.get('journal_pending') == 0 and data.get('pump_running') is False:
                    self.ready[node] = True
                    self.pending.pop(node, None)
                else:
                    self.ready.pop(node, None)
                return True
        if kind == 'fallback_audit':
            event_id = data.get('event_id')
            if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
                return False
            if (data.get('source') not in ('edge', 'local_fallback', 'interrupted_run')
                    or data.get('phase') not in ('start_reserved', 'start_failed', 'stopped', 'interrupted_unknown_duration')
                    or type(data.get('actual_duration_ms')) is not int
                    or not 0 <= data['actual_duration_ms'] <= 4294967295):
                return False
            key = node + '/' + event_id
            if key not in self.state['events']:
                # Preserve the source record. Never fabricate an irrigation window.
                self.state['events'][key] = data
                if data.get('source') in ('local_fallback', 'interrupted_run'):
                    self.state['quarantine_until'][node] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
                atomic_json(self.state_path, self.state)
            self.ready.pop(node, None)
            self.pending.pop(node, None)
            self.publish(node, 'fallback_ack', dict(edge_id=self.edge_id, node_id=node, event_id=event_id))
            return True
        return False

    def safe_for_prediction(self, node):
        if node not in self.ready or self.clock() - self.last_seen.get(node, -float('inf')) > 15:
            return False
        cutoff = self.state['quarantine_until'].get(node)
        return cutoff is None or datetime.now(timezone.utc) >= datetime.fromisoformat(cutoff)


active_fallback = None


def control_ready(node):
    manager = active_fallback
    return manager is None or (node in manager.ready and manager.clock() - manager.last_seen.get(node, -float('inf')) <= 15)


def prediction_allowed(node):
    return active_fallback is None or active_fallback.safe_for_prediction(node)
