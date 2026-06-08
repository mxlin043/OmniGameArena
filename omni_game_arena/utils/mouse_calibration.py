"""Mouse rotation calibration shared by Lumine prompts and adapters."""

MOUSE_UNITS_PER_360_DEG = 2057.0
DEGREES_PER_MOUSE_UNIT = 360.0 / MOUSE_UNITS_PER_360_DEG


def degrees_to_mouse_units(degrees: float) -> float:
    return float(degrees) / DEGREES_PER_MOUSE_UNIT


def mouse_units_to_degrees(units: float) -> float:
    return float(units) * DEGREES_PER_MOUSE_UNIT
