import json
from datetime import datetime

def serialize_config(config):
    """Serialize config dict to JSON string."""
    return json.dumps(config)
