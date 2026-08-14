"""
LightBurn UDP Communication Package

Provides UDP communication with LightBurn software on Windows, macOS and Linux.
"""

from .lightburn_udp import (
    LightBurnUDPCommunication,
    find_lightburn,
    __version__,
    __author__,
)

__all__ = ["LightBurnUDPCommunication", "find_lightburn", "__version__"]
