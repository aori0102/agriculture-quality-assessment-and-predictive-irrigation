"""Same-process composition of existing AI and Cloud Edge; no import-time I/O."""

import argparse
from configparser import ConfigParser
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import sys
from threading import RLock

from .client import CloudClient
from .config import load_device_bindings
from .edge_profiles import EdgeProfileBackend
from .edge_pump import EdgePumpBackend, PumpDispatcher

REPOSITORY = Path(__file__).resolve().parents[2]


def check_edge_config(ai_directory: Path) -> None:
    cfg = ConfigParser()
    try:
        with (ai_directory / 'config.cfg').open(encoding='utf-8-sig') as stream:
            cfg.read_file(stream)
        raw = cfg.get('nodes', 'default_node_profile', fallback=None)
        if raw is None:
            raise ValueError('AI/config.cfg needs [nodes] default_node_profile before the existing Edge proxy can initialize.')
        default = json.loads(raw)
        if not isinstance(default, dict) or not {'plant', 'pwp', 'madp', 'fc', 'sp'} <= default.keys():
            raise ValueError('AI default_node_profile requires plant, pwp, madp, fc, and sp.')
    except (OSError, json.JSONDecodeError):
        raise ValueError('AI/config.cfg is unreadable or default_node_profile is not valid JSON.') from None


def run_with_clients(edge_main, backend, bindings, *, client_factory=CloudClient,
                     pump_backend=None, dispatcher_factory=PumpDispatcher):
    """One shared adapter across all devices; close even after partial startup."""
    with ExitStack() as resources:
        clients = {}
        for binding in bindings:
            client = client_factory(binding.config, node_id=binding.node_id, profile_backend=backend)
            resources.callback(client.close)
            client.start()
            clients[binding.node_id] = client
        if pump_backend is not None:
            dispatcher = dispatcher_factory(clients, pump_backend)
            resources.callback(dispatcher.close)  # Stop before closing clients.
            dispatcher.start()
        return edge_main()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Run existing AI and Cloud Edge in one process.')
    parser.add_argument('--check', action='store_true', help='Validate configuration only; no AI imports or connections')
    args = parser.parse_args(argv)
    ai_directory = REPOSITORY / 'AI'
    try:
        check_edge_config(ai_directory)
        bindings = load_device_bindings()
    except Exception as error:
        # Only the known validation messages above and sanitized CloudConfig errors
        # are displayed. Unexpected parser failures must not expose config values.
        message = str(error) if isinstance(error, ValueError) else 'Configuration could not be loaded.'
        print(f'Startup configuration error: {message}', file=sys.stderr)
        return 2
    if args.check:
        print('Configuration structure valid; model dependencies and live services were not checked.')
        return 0
    sys.path.insert(0, str(ai_directory))  # Existing AI uses script-style sibling imports.
    try:
        proxy = importlib.import_module('feranmut_proxy')
        app = importlib.import_module('app')
    except Exception:
        print('AI initialization failed; verify AI configuration, dependencies, and model files.', file=sys.stderr)
        return 2
    operation_lock = RLock()
    backend = EdgeProfileBackend(proxy.update_node_profile, proxy.operation_log_manager.load_log,
                                 operation_lock=operation_lock)
    pump_backend = EdgePumpBackend(proxy.enable_irrigation_on_node, proxy.disable_irrigation_on_node,
                                   proxy.operation_log_manager.load_log, operation_lock=operation_lock)
    try:
        run_with_clients(app.main, backend, bindings, pump_backend=pump_backend)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print('Edge runtime failed; clients were closed. Inspect Edge configuration and local diagnostics.', file=sys.stderr)
        return 1
    return 0
