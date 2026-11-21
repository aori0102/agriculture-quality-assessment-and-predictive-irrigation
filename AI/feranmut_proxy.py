"""
A proxy on the Edge IT side providing and interface for the Blynk-Cloud Dashboard
for updating nodes information.
"""
from operation_log_manager import operation_log_manager

def update_node_profile(
    node_id: str,
    permanent_wilting_point: float = None,
    management_allowed_depletion_point: float = None,
    field_capacity: float = None,
    saturation_point: float = None,
    plant: str = None):
    """
    Handle the update of a node profile sent from Blynk Dashboard. Any of the parameters
    can be not provided (None), in which case they will not be updated.
    """
    
    operation_log_manager.log_node_profile(
        node_id=node_id,
        permanent_wilting_point=permanent_wilting_point,
        management_allowed_depletion_point=management_allowed_depletion_point,
        field_capacity=field_capacity,
        saturation_point=saturation_point,
        plant=plant
    )
    
    
def enable_irrigation_on_node(node_id: str):
    """
    Allow future automatic irrigation on this node.
    """
    operation_log_manager.log_irrigation_status(node_id=node_id, status=True)
    
    
def disable_irrigation_on_node(node_id: str):
    """
    Disable future automatic irrigation; do not stop an active pump.
    """
    operation_log_manager.log_irrigation_status(node_id=node_id, status=False)