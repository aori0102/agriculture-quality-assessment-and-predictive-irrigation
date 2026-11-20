"""Same-process composition of existing AI and Cloud Edge; no import-time I/O."""

import argparse
from configparser import ConfigParser
from contextlib import ExitStack
import importlib
import json
import os
from pathlib import Path
import sys
from threading import RLock
from dotenv import dotenv_values

from .client import CloudClient
from .config import CloudConfig, load_device_bindings
from .edge_profiles import EdgeProfileBackend
from .edge_pump import EdgePumpBackend, PumpDispatcher
from .device_registry import RuntimeDeviceRegistry
from .device_store import DeviceStore
from .provisioning import DeviceProvisioningWorker, stable_name
from .blynk_provisioner import BlynkProvisioner

REPOSITORY = Path(__file__).resolve().parents[2]


def check_edge_config(ai_directory: Path) -> None:
    cfg = ConfigParser()
    try:
        with (ai_directory / "config.cfg").open(encoding="utf-8-sig") as stream:
            cfg.read_file(stream)
        raw = cfg.get("nodes", "default_node_profile", fallback=None)
        if raw is None:
            raise ValueError(
                "AI/config.cfg needs [nodes] default_node_profile before the existing Edge proxy can initialize."
            )
        default = json.loads(raw)
        if (
            not isinstance(default, dict)
            or not {"plant", "pwp", "madp", "fc", "sp"} <= default.keys()
        ):
            raise ValueError(
                "AI default_node_profile requires plant, pwp, madp, fc, and sp."
            )
    except (OSError, json.JSONDecodeError):
        raise ValueError(
            "AI/config.cfg is unreadable or default_node_profile is not valid JSON."
        ) from None


def run_with_clients(
    edge_main,
    backend,
    bindings,
    *,
    client_factory=CloudClient,
    pump_backend=None,
    dispatcher_factory=PumpDispatcher,
    provisioning=None,
    telemetry_handler=None,
):
    """One shared adapter across all devices; close even after partial startup."""
    with ExitStack() as resources:
        clients = RuntimeDeviceRegistry()
        resources.callback(clients.close)
        for binding in bindings:
            client = client_factory(
                binding.config, node_id=binding.node_id, profile_backend=backend
            )
            try:
                client.start()
                clients.add(binding.node_id, client)
            except Exception:
                client.close()
                raise
        worker = None
        if provisioning is not None:
            store = DeviceStore(provisioning["device_store"])
            persisted = store.load()
            if set(binding.config.auth_token for binding in bindings) & {
                record.token for record in persisted.values()
            }:
                raise ValueError("Static and persisted device tokens conflict.")
            for node_id, record in persisted.items():
                if record.stable_name != stable_name(provisioning["edge_id"], node_id):
                    raise ValueError(
                        "Persisted device binding belongs to a different Edge identity."
                    )
                if clients.get(node_id) is not None:
                    raise ValueError("Static and persisted device bindings conflict.")
                client = client_factory(
                    CloudConfig(record.server, record.token),
                    node_id=node_id,
                    profile_backend=backend,
                )
                try:
                    client.start()
                    clients.add(node_id, client)
                except Exception:
                    client.close()
                    raise
            worker = DeviceProvisioningWorker(
                edge_id=provisioning["edge_id"],
                approved=provisioning["approved"],
                server=provisioning["server"],
                store=store,
                registry=clients,
                provisioner=BlynkProvisioner(provisioning["oauth_store"]),
                client_factory=client_factory,
                profile_backend=backend,
            )
            resources.callback(worker.close)  # Stop before closing clients.
            worker.start()
            if telemetry_handler is not None:
                telemetry_handler.on_accepted = worker.observe_node
                resources.callback(setattr, telemetry_handler, "on_accepted", None)
        if pump_backend is not None:
            dispatcher = dispatcher_factory(clients, pump_backend)
            resources.callback(dispatcher.close)  # Stop before closing clients.
            dispatcher.start()
        return edge_main()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run existing AI and Cloud Edge in one process."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration only; no AI imports or connections",
    )
    args = parser.parse_args(argv)
    ai_directory = REPOSITORY / "AI"
    try:
        if ensure_env_file():
            print(f"Created configuration template: {ENV_FILE}", file=sys.stderr)
            print(
                "Edit BLYNK_DEVICES_JSON (node_id/server) and fill in the referenced Auth Token "
                "variables in that file, then run python -m run_edge again. Startup stopped.",
                file=sys.stderr,
            )
            return 2
        check_edge_config(ai_directory)
        values = {**dotenv_values(REPOSITORY / ".env", interpolate=False), **os.environ}
        dynamic = values.get("BLYNK_PROVISIONING_ENABLED") == "1"
        bindings = load_device_bindings(values, allow_empty=dynamic)
        provisioning = None
        if dynamic:
            cfg = ConfigParser()
            cfg.read(ai_directory / "config.cfg", encoding="utf-8-sig")
            edge_id = cfg.get("edge", "edge_id").strip()
            approved = [
                node.strip()
                for node in values.get("BLYNK_APPROVED_NODES", "").split(",")
                if node.strip()
            ]
            server = (values.get("BLYNK_PROVISION_SERVER") or "").strip()
            if not edge_id or not approved or not server:
                raise ValueError(
                    "Dynamic provisioning requires edge_id, BLYNK_APPROVED_NODES, and BLYNK_PROVISION_SERVER."
                )
            CloudConfig(server, "validation-placeholder")
            provisioning = {
                "edge_id": edge_id,
                "approved": approved,
                "server": server,
                "oauth_store": REPOSITORY / "Cloud Edge/runtime/mcp_oauth.json",
                "device_store": REPOSITORY / "Cloud Edge/runtime/blynk_devices.json",
            }
    except Exception as error:
        # Only the known validation messages above and sanitized CloudConfig errors
        # are displayed. Unexpected parser failures must not expose config values.
        message = (
            str(error)
            if isinstance(error, ValueError)
            else "Configuration could not be loaded."
        )
        print(f"Startup configuration error: {message}", file=sys.stderr)
        print(f"Check configuration in: {ENV_FILE}", file=sys.stderr)
        return 2
    if args.check:
        print(
            "Configuration structure valid; model dependencies and live services were not checked."
        )
        return 0
    sys.path.insert(
        0, str(ai_directory)
    )  # Existing AI uses script-style sibling imports.
    try:
        proxy = importlib.import_module("feranmut_proxy")
        app = importlib.import_module("app")
    except Exception:
        print(
            "AI initialization failed; verify AI configuration, dependencies, and model files.",
            file=sys.stderr,
        )
        return 2
    operation_lock = RLock()
    backend = EdgeProfileBackend(
        proxy.update_node_profile,
        proxy.operation_log_manager.load_log,
        operation_lock=operation_lock,
    )
    pump_backend = EdgePumpBackend(
        proxy.enable_irrigation_on_node,
        proxy.disable_irrigation_on_node,
        proxy.operation_log_manager.load_log,
        operation_lock=operation_lock,
    )
    try:
        handler = importlib.import_module(
            "IrrigationPrediction.telemetry_handler"
        ).telemetry_handler
        run_with_clients(
            app.main,
            backend,
            bindings,
            pump_backend=pump_backend,
            provisioning=provisioning,
            telemetry_handler=handler,
        )
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(
            "Edge runtime failed; clients were closed. Inspect Edge configuration and local diagnostics.",
            file=sys.stderr,
        )
        return 1
    return 0
