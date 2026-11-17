"""
A proxy on the Edge IT side providing and interface for the Blynk-Cloud Dashboard
for updating nodes information.
"""
from operation_log_manager import operation_log_manager

def update_node_profile(
    node_id: str,
    permanent_wilting_point: float = None,
    management_allowable_depletion: float = None,
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
        management_allowable_depletion=management_allowable_depletion,
        field_capacity=field_capacity,
        saturation_point=saturation_point,
        plant=plant
    )
    
    
def enable_irrigation_on_node(node_id: str):
    """
    Enable irrigation on a specific node.
    """
    operation_log_manager.log_irrigation_status(node_id=node_id, status=True)
    
    
def disable_irrigation_on_node(node_id: str):
    """
    Disable irrigation on a specific node.
    """
    operation_log_manager.log_irrigation_status(node_id=node_id, status=False)