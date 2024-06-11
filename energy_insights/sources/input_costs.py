"""
A unified structure for passing around the set of prices of fuels.
"""

from dataclasses import dataclass
from typing import Union

__efficiency_coal = 0.4


@dataclass
class InputCosts:
    emission_price_per_t_eur: float
    """Price of carbon emissions in EUR per metric ton of CO₂"""
    # Prices per MWh of lower heating value:
    lignite_price_per_mwh_LHV_eur: float
    biomass_price_per_mwh_LHV_eur: float
    biogas_price_per_mwh_LHV_eur: float
    hard_coal_price_per_mwh_LHV_eur: float
    fossil_gas_price_per_mwh_LHV_eur: float
    biomethane_price_per_mwh_LHV_eur: float


__2030_cost = {
    "emission_price_per_t_eur": 120,
    "lignite_price_per_mwh_LHV_eur": 10 * __efficiency_coal,
    # This assumes FiP around 100 EUR/MWh el.
    "biomass_price_per_mwh_LHV_eur": 20 * __efficiency_coal,
    "biogas_price_per_mwh_LHV_eur": 20 * __efficiency_coal,
    # Computed from price per 1000 tons of coal (which is 8.141 MWh thermal energy).
    "hard_coal_price_per_mwh_LHV_eur": 120 / 8.141,
    "fossil_gas_price_per_mwh_LHV_eur": 25,
    "biomethane_price_per_mwh_LHV_eur": 50,
}

_input_costs: dict[str, dict[str, float]] = {
    "current": {
        "emission_price_per_t_eur": 90,
        "lignite_price_per_mwh_LHV_eur": 10,
        # This assumes FiP around 100 EUR/MWh el.
        "biomass_price_per_mwh_LHV_eur": 20 * __efficiency_coal,
        "biogas_price_per_mwh_LHV_eur": 20 * __efficiency_coal,
        # Computed from price per 1000 tons of coal (which is 8.141 MWh thermal energy).
        "hard_coal_price_per_mwh_LHV_eur": 220 / 8.141,
        "fossil_gas_price_per_mwh_LHV_eur": 70,
        "biomethane_price_per_mwh_LHV_eur": 50,
    },
    "2030": __2030_cost,
    "2030-cheap-ets": __2030_cost | {
        "emission_price_per_t_eur": 40,
    },
    "2030-cheap-ets-expensive-gas": __2030_cost | {
        "emission_price_per_t_eur": 40,
        "fossil_gas_price_per_mwh_LHV_eur": 80,
    },
    "2030-higher-ets": __2030_cost | {
        "emission_price_per_t_eur": 200,
    },
    "2050-SEK": __2030_cost | {
        # Roughly EC recommended level for modelling for 2050.
        "emission_price_per_t_eur": 500,
        "fossil_gas_price_per_mwh_LHV_eur": 30,
        # This assumes no FiP neither for biomass, nor for biogas (biogas replaced by biomethane).
        "biomass_price_per_mwh_LHV_eur": 100 * __efficiency_coal,
        "biogas_price_per_mwh_LHV_eur": 150 * __efficiency_coal,
    },
}


def get_input_costs(input_costs: Union[str, dict[str, float]]) -> InputCosts:
    if isinstance(input_costs, str):
        if input_costs not in _input_costs:
            raise KeyError(f"Input costs key {input_costs} not defined")
        input_costs = _input_costs[input_costs]
    return InputCosts(**input_costs)
