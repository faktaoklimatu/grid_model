"""
A unified structure for passing around the set of prices of fuels.
"""

from dataclasses import dataclass
from typing import Union

# FIXME: Borrowed from sources.flexible_source
__efficiency_biogas = 0.45
__efficiency_ccgt = 0.56
__efficiency_coal = 0.4
__efficiency_ocgt = 0.4


@dataclass
class InputCosts:
    emission_price_per_t_eur: float
    """Price of carbon emissions in EUR per metric ton of CO₂"""
    # Prices per MWh of lower heating value:
    lignite_price_per_mwh_LHV_eur: float
    biomass_price_per_mwh_LHV_eur: float
    biogas_price_per_mwh_LHV_eur: float
    hard_coal_price_per_mwh_LHV_eur: float
    heating_oil_price_per_mwh_LHV_eur: float
    fossil_gas_price_per_mwh_LHV_eur: float
    biomethane_price_per_mwh_LHV_eur: float
    solid_waste_per_mwh_LHV_eur: float


__2022_cost = {
    "emission_price_per_t_eur": 90,
    # Average lignite price approximately in line with ERAA 2023 methodology.
    "lignite_price_per_mwh_LHV_eur": 6,
    # This assumes FiP around 100 EUR/MWh el.
    "biomass_price_per_mwh_LHV_eur": 20 * __efficiency_biogas,
    "biogas_price_per_mwh_LHV_eur": 20 * __efficiency_biogas,
    # Computed from price per 1000 tons of coal (which is 8.141 MWh thermal energy).
    "hard_coal_price_per_mwh_LHV_eur": 220 / 8.141,
    "heating_oil_price_per_mwh_LHV_eur": 80,
    "fossil_gas_price_per_mwh_LHV_eur": 70,
    "biomethane_price_per_mwh_LHV_eur": 50 * __efficiency_ocgt,
    # Wild guesstimate based on heat prices in Brno.
    "solid_waste_per_mwh_LHV_eur": 12,
}

__2023_cost = __2022_cost | {
    "emission_price_per_t_eur": 80,
    # Average front-contract TTF price throughout 2023.
    "fossil_gas_price_per_mwh_LHV_eur": 41,
    # Approximately the average of front-contract API2 Rotterdam
    # throughout 2023.
    "hard_coal_price_per_mwh_LHV_eur": 120 / 8.141,
}

__2025_cost = __2022_cost | {
    "emission_price_per_t_eur": 100,
    # This assumes a feed-in price around 60 €/MWh el.
    "biomass_price_per_mwh_LHV_eur": 60 * __efficiency_biogas,
    "biogas_price_per_mwh_LHV_eur": 60 * __efficiency_biogas,
    # Approximately in line with TYNDP 2024 scenarios methodology which
    # assumes 18.8 €/GJ = 67.68 €/MWh in 2030.
    "biomethane_price_per_mwh_LHV_eur": 68,
    # Assume the same price for gas in all the coal study scenarios
    # for comparability.
    # Based on Dutch TTF futures for 2025 in June 2024.
    "fossil_gas_price_per_mwh_LHV_eur": 35,
    # Based on API2 Rotterdam futures for 2025 around June 2024.
    "hard_coal_price_per_mwh_LHV_eur": 120 / 8.141,
}

__2030_cost = {
    "emission_price_per_t_eur": 120,
    "lignite_price_per_mwh_LHV_eur": 10,
    # This assumes FiP around 100 EUR/MWh el.
    "biomass_price_per_mwh_LHV_eur": 20 * __efficiency_biogas,
    "biogas_price_per_mwh_LHV_eur": 20 * __efficiency_biogas,
    # Computed from price per 1000 tons of coal (which is 8.141 MWh thermal energy).
    "hard_coal_price_per_mwh_LHV_eur": 120 / 8.141,
    "heating_oil_price_per_mwh_LHV_eur": 33,
    "fossil_gas_price_per_mwh_LHV_eur": 25,
    "biomethane_price_per_mwh_LHV_eur": 50 * __efficiency_ocgt,
    # Wild guesstimate based on heat prices in Brno.
    "solid_waste_per_mwh_LHV_eur": 15,
}

_input_costs: dict[str, dict[str, float]] = {
    "current": __2022_cost,
    "2023": __2023_cost,
    "2025": __2025_cost,
    "2025-cheap-ets": __2025_cost | {
        "emission_price_per_t_eur": 60,
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


def get_input_costs(input_costs: Union[InputCosts, str, dict[str, float]]) -> InputCosts:
    if isinstance(input_costs, InputCosts):
        return input_costs

    if isinstance(input_costs, str):
        if input_costs not in _input_costs:
            raise KeyError(f"Input costs key {input_costs} not defined")
        input_costs = _input_costs[input_costs]

    return InputCosts(**input_costs)
