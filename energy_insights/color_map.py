"""
Provides a color map for different types of information.
"""


class ColorMap:
    # Fakta lighter gray.
    GRAY = "#818b98"

    LABELS = GRAY

    LOAD = "#333333"
    LOAD_BEFORE_FLEXIBILITY = "#333333"
    LOAD_BEFORE_FLEXIBILITY_BACKGROUND = "#ffffff80"
    LOAD_WITH_ACCUMULATION = GRAY
    LOAD_WITH_ACCUMULATION_BACKGROUND = "white"
    HEAT_PUMPS = "teal"

    # Interconnection, import/export
    INTERCONNECTORS = "#cfcfcf"
    INTERCONNECTORS_BORDER = GRAY
    EXPORT_PRICE = "darkcyan"
    IMPORT_PRICE = "tomato"

    # Basic sources.
    SOLAR = "#f2b130"
    WIND = "#2291e6"
    NUCLEAR = "#036080"
    SMR = "#3F78A1"
    HYDRO = "#18c9f5"

    # Flexible sources.
    COAL = "#993d76"
    GAS = "#cb485e"
    GAS_WITH_CCS = "#d68d8b"
    BIOMASS = "#d1ae7f"

    GAS_PEAKERS = "#cb485e"
    BIOMASS_PEAKERS = "#d1ae7f"
    DSR = "palevioletred"
    LOSS_OF_LOAD = "crimson"

    # Grid storage
    PUMPED_HYDRO = "#4bbbc9"
    BATTERY = "#3dbd6b"
    HYDROGEN = "#94e8d9"
    E_CARS = "#71c9a9"
    HEAT_DISTRIBUTION = "#c67861"
    # A generic color for all storage technologies (in summary stats).
    STORAGE = "#2dc4a8"
