from enum import Enum
from typing import Any, Callable, Optional


class ToolCategory(str, Enum):
    """Broad categorization of capabilities provided by tools."""
    FILESYSTEM = "FILESYSTEM"
    SHELL = "SHELL"
    GIT = "GIT"
    WEB = "WEB"
    VISION = "VISION"
    OCR = "OCR"
    CUSTOM = "CUSTOM"
