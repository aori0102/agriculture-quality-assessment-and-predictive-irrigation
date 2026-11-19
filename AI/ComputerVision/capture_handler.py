from time import monotonic
from datetime import time, datetime, timezone
from action_index import ActionIndex
from command_proxy import command_proxy
from pathlib import Path
from configparser import ConfigParser
from operation_log_manager import operation_log_manager
import json

CONFIG_FILE = "config.cfg"

class CaptureHandler:
    def __init__(self):
        config_path = Path(__file__).resolve().parents[1] / CONFIG_FILE
        config = ConfigParser()
        with config_path.open(encoding="utf-8-sig") as cfg_file:
            config.read_file(cfg_file)
            
        self.capture_handling_interval = config.getint("capture", "capture_handling_interval_seconds", fallback=10)
        self.previous_handling_date = None
        self.unsampled_times = set([])
            
        capture_window = json.loads(config.get("capture", "capture_window"))
        capture_window_time_delta = config.getint("capture", "capture_window_time_delta", fallback=0)
            
        self.capture_window = [
            time(
                hour=(entry["hour"] - capture_window_time_delta + 24) % 24,
                minute=(entry["minute"] + 60) % 60)
            for entry in capture_window
        ]
        
        print("Capture Handler initialized. The following time window will be used for sending capturing commands:")
        for t in self.capture_window:
            print(f"  {t}")
            
        self.previous_capture_time = monotonic()
        
    def loop(self):
        this_millis = monotonic()
        if this_millis - self.previous_capture_time < self.capture_handling_interval:
            return
        self.previous_capture_time = this_millis
        
        now = datetime.now(timezone.utc)
        if self.previous_handling_date != now.date():
            self.unsampled_times = set(self.capture_window).copy()
            self.previous_handling_date = now.date()

        time_fit = any(slot.hour == now.hour and slot.minute == now.minute
               for slot in self.unsampled_times)
        if not time_fit:
            return
        
        self.unsampled_times = {slot for slot in self.unsampled_times if not (slot.hour == now.hour and slot.minute == now.minute)}

        print(f"Sending capture commands at {now.isoformat()}")
        
        for node_id in operation_log_manager.read_all_recorded_nodes():
            command_proxy.send_command(node_id, action=ActionIndex.Capture, ttl_ms=60000, duration_ms=1234)
            
capture_handler = CaptureHandler()