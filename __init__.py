from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from . import zoom

# Zoom Crop / Zoom Paste (11/11/2026): a second, sharper pass of a detection or matting model
NODE_CLASS_MAPPINGS = {**NODE_CLASS_MAPPINGS, **zoom.NODE_CLASS_MAPPINGS}
NODE_DISPLAY_NAME_MAPPINGS = {**NODE_DISPLAY_NAME_MAPPINGS, **zoom.NODE_DISPLAY_NAME_MAPPINGS}

# the menu of the Ollama / LM Studio models (js/prompt_series.js)
WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
