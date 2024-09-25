"""
Provides parameters for basic sources in the grid.
"""

from dataclasses import dataclass

@dataclass
class Reserves:
    additional_load_mw: float = 0
    """Additional demand in each hour in MW. Used for implicit
    modelling of balancing reserves."""
    hydro_capacity_reduction_mw: float = 0
    """Blanket reduction of dispatchable hydropower capacity in MW.
    Used for implicit modelling of balancing reserves."""

    def __add__(self, other):
        assert isinstance(other, Reserves), "Cannot add a non-Reserves object to Reserves"

        return Reserves(
            additional_load_mw=self.additional_load_mw + other.additional_load_mw,
            hydro_capacity_reduction_mw=self.hydro_capacity_reduction_mw +
                other.hydro_capacity_reduction_mw
        )