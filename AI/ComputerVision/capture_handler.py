from time import monotonic
from action_index import ActionIndex
from command_proxy import command_proxy
from acquired_node_handler import AcquiredNodeHandler

class CaptureHandler:
    def __init__(self):
        self.previous_capture_time = monotonic()
        
    def loop(self):
        now = monotonic()
        if now - self.previous_capture_time < 20:
            return
        
        self.previous_capture_time = now
        for node_id in AcquiredNodeHandler.read_all_nodes():
            command_proxy.send_command(node_id, action=ActionIndex.Capture, ttl_ms=60000, duration_ms=1234)
            
capture_handler = CaptureHandler()