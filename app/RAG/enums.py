"""Enums for chatbot configuration and appearance settings."""

from enum import Enum


class Theme(str, Enum):
    """Appearance themes."""
    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"


class Position(str, Enum):
    """Chatbot widget positions."""
    BOTTOM_RIGHT = "bottom_right"
    BOTTOM_LEFT = "bottom_left"
    TOP_RIGHT = "top_right"
    TOP_LEFT = "top_left"
    CENTER = "center"