"""
Rapidpower robot dog control library.

Original Python implementation reconstructed from reverse-engineering
the com.zhongrun.robotdog Android app for interoperability research.
"""

from .protocol import encode, encode_feed, encode_legs, COMMANDS, FEED_COMMANDS
from .controller import RobodogBLE

__all__ = ['encode', 'encode_feed', 'encode_legs', 'COMMANDS', 'FEED_COMMANDS', 'RobodogBLE']
__version__ = '0.1.0'
