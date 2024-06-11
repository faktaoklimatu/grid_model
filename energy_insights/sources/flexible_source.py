"""
Provides structures for flexible power sources in the grid.
"""

import enum
from copy import deepcopy
from dataclasses import dataclass, fields
from typing import Any, Iterable, Optional, Union

from ..color_map import ColorMap
from ..grid_opex_utils import get_flexible_source_cost_params
from .basic_source import Source, fix_source_params, NUCLEAR_FUEL_PRICE_EUR_MWH_EL
from .economics import SourceEconomics, extract_economics_params, usd_to_eur_2022
from .heat_source import BackPressureTurbine, ExtractionTurbine, HeatSource
from .input_costs import InputCosts, get_input_costs


@enum.unique
class FlexibleSourceType(enum.Enum):
    BIOGAS = "biogas"
    """General biogass/biomethane-fired plant"""
    BIOGAS_PEAK = "bio_peak"
    """Biogass-fired OCGT plant"""
    COAL = "coal"
    """Hard coal-fired power plant"""
    COAL_BACKPRESSURE = "coal_bp"
    """Hard coal-fired power plant with a back-pressure turbine for
    heat co-generation"""
    COAL_EXTRACTION = "coal_ex"
    """Hard coal-fired power plant with a steam extraction turbine for
    heat co-generation"""
    GAS = "gas"
    """General natural gas-fired power plant"""
    GAS_CCGT = "gas_ccgt"
    """Natural gas-fired CCGT power plant"""
    GAS_CCGT_CCS = "gas_ccgt_ccs"
    """Natural gas-fired CCGT power plant with carbon capture"""
    GAS_CHP = "gas_chp"
    """Natural gas-fired CCGT power plant with extraction turbine
    heat co-generation"""
    GAS_ENGINE = "gas_eng"
    """Natural gas-fired engine"""
    GAS_PEAK = "gas_peak"
    """Peaking gas-fired OCGT power plant"""
    LIGNITE = "lig"
    """General lignite-fired power plant"""
    LIGNITE_BACKPRESSURE = "lig_bp"
    """Lignite-fired power plant with a back-pressure turbine for heat
    co-generation"""
    LIGNITE_EXTRACTION = "lig_ex"
    """Lignite-fired power plant with a steam extraction turbine for
    heat co-generation"""
    LOSS_OF_LOAD = "eens"
    """Expected energy not served. Virtual source of power when load is
    higher than production."""
    MAZUT = "mazut"
    """Mazut-fired power plant"""
    FOSSIL_OIL = "oil"
    """Power generation from burning fossil-based fuel oils."""
    OTHER_RES = "o_RES"
    """Abstract 'other renewables' power source. Equivalent to biogas
    for now."""
    SOLID_BIOMASS = "bio"
    """General solid biomass-burning power plant"""
    SOLID_BIOMASS_CHP = "b_CHP"
    """Combined heat and power solid biomass-burning power plant"""
    WASTE = "waste"
    """Waste-burning power plant"""
    SMR = "smr"
    """Small modular nuclear"""
    DSR = "dsr"
    """Demand reduction when generation is not sufficient (very expensive)"""


@dataclass
class FlexibleSource(Source):
    type: FlexibleSourceType
    max_total_twh: Optional[float]
    """Maximum allowed annual production in TWh in terms of electricity
    equivalent (in the case of CHP)."""
    ramp_rate: float
    """Power (expressed as ratio of `capacity_mw`) by which the current
    production can change up or down in one hour."""
    ramp_up_cost_mw_eur: float
    """Fixed cost for increasing output by 1 MW."""
    heat: Optional[HeatSource]
    """Type of heat production, if any."""

    def __add__(self, other):
        basic_source = Source.__add__(self, other)
        source_dict = {
            field.name: getattr(basic_source, field.name) for field in fields(Source)
        }

        assert self.ramp_rate == other.ramp_rate, f"ramp rates != for {self.type} and {other.type}"
        assert self.ramp_up_cost_mw_eur == other.ramp_up_cost_mw_eur, \
            f"ramp_up_cost_mw_eur != for {self.type} and {other.type}"
        assert self.heat == other.heat, f"heat != for {self.type} and {other.type}"

        max_total_twh = self.max_total_twh
        if max_total_twh is None:
            max_total_twh = other.max_total_twh
        elif other.max_total_twh is not None:
            max_total_twh += other.max_total_twh

        return FlexibleSource(
            **source_dict,
            max_total_twh=max_total_twh,
            ramp_rate=self.ramp_rate,
            ramp_up_cost_mw_eur=self.ramp_up_cost_mw_eur,
            heat=self.heat)


# Efficiency constants
__efficiency_ccgt = 0.56
__efficiency_ccgt_ccs = 0.51
# Central estimate for 2020 from Danish Energy Agency
# https://ens.dk/en/our-services/projections-and-models/technology-data/technology-data-generation-electricity-and
__efficiency_ocgt = 0.4
__efficiency_coal = 0.4
__efficiency_coal_bp = 0.3
__efficiency_biogas = 0.45

# Irrelevant at the moment as fuel price is fixed for MWh_el (the efficiency assumes some
# IV-gen improvements).
__efficiency_nuclear_smr = 0.4


def _get_coal_cost_params(fuel_price_getter, efficiency_el: float, emissions: float):
    return get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=5,
                                           wear_cost_per_mw_eur=50,
                                           ramp_fuel_per_mw_gj=18,
                                           fuel_price_per_mwh_LHV_eur_getter=fuel_price_getter,
                                           efficiency_el=efficiency_el,
                                           emissions_per_mwh_LHV_t=emissions)


# Emission factors
__emissions_gas_per_MWh_LHV_t = 0.22
__emissions_gas_ccs_per_MWh_LHV_t = 0.15 * __emissions_gas_per_MWh_LHV_t  # 85% CCS
__emissions_hard_coal_per_MWh_LHV_t = 0.76 * __efficiency_coal
__emissions_lignite_per_MWh_LHV_t = 1 * __efficiency_coal

__coal = {
    "color": ColorMap.COAL,
    "construction_time_years": 4,
    "lifetime_years": 40,
    "fixed_o_m_costs_per_kw_eur": 80,
    "capacity_mw": 0,
    "min_capacity_mw": 0,
}

__lignite = __coal | {
    "type": FlexibleSourceType.LIGNITE,
    "ramp_rate": .1,
    "overnight_costs_per_kw_eur": 3000,
} | _get_coal_cost_params(lambda cost: cost.lignite_price_per_mwh_LHV_eur, __efficiency_coal,
                          __emissions_lignite_per_MWh_LHV_t)

__lignite_extraction = __lignite | {
    "type": FlexibleSourceType.LIGNITE_EXTRACTION,
    "extraction_turbine": ExtractionTurbine.canonical(),
}

__lignite_back_pressure = __lignite | {
    "type": FlexibleSourceType.LIGNITE_BACKPRESSURE,
    "back_pressure_turbine": BackPressureTurbine.canonical(),
} | _get_coal_cost_params(lambda cost: cost.lignite_price_per_mwh_LHV_eur, __efficiency_coal_bp,
                          __emissions_lignite_per_MWh_LHV_t)

__hard_coal = __coal | {
    "type": FlexibleSourceType.COAL,
    "ramp_rate": .15,
    # IEA's World Energy Outlook 2023 for Europe (estimates stay constant in time and per scenario).
    # https://iea.blob.core.windows.net/assets/2b0ded44-6a47-495b-96d9-2fac0ac735a8/WorldEnergyOutlook2023.pdf
    "overnight_costs_per_kw_eur": usd_to_eur_2022(2000),
} | _get_coal_cost_params(lambda cost: cost.hard_coal_price_per_mwh_LHV_eur, __efficiency_coal,
                          __emissions_hard_coal_per_MWh_LHV_t)

__hard_coal_extraction = __hard_coal | {
    "type": FlexibleSourceType.COAL_EXTRACTION,
    "extraction_turbine": ExtractionTurbine.canonical(),
}

__hard_coal_back_pressure = __hard_coal | {
    "type": FlexibleSourceType.COAL_BACKPRESSURE,
    "back_pressure_turbine": BackPressureTurbine.canonical(),
} | _get_coal_cost_params(lambda cost: cost.hard_coal_price_per_mwh_LHV_eur, __efficiency_coal_bp,
                          __emissions_hard_coal_per_MWh_LHV_t)

__waste = __hard_coal | {
    "type": FlexibleSourceType.WASTE,
    "overnight_costs_per_kw_eur": 3500,
    "uptime_ratio": 0.5,
    # TODO: fix fuel and allowances costs and ramp up cost if different from coal.
}

# For simplicity as this is quite rare.
__mazut = __hard_coal | {
    "type": FlexibleSourceType.MAZUT,
}

__gas = {
    "type": FlexibleSourceType.GAS,
    "color": ColorMap.GAS,
    "ramp_rate": .5,
    "capacity_mw": 0,
    "min_capacity_mw": 0,
}

__gas_ccgt = __gas | {
    "type": FlexibleSourceType.GAS_CCGT,
    "construction_time_years": 3,
    "lifetime_years": 30,
    "fixed_o_m_costs_per_kw_eur": 15,
    # IEA's World Energy Outlook 2023 for Europe (estimates stay constant in time and per scenario).
    # https://iea.blob.core.windows.net/assets/2b0ded44-6a47-495b-96d9-2fac0ac735a8/WorldEnergyOutlook2023.pdf
    "overnight_costs_per_kw_eur": usd_to_eur_2022(1000),
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=4,
                                    wear_cost_per_mw_eur=25,
                                    ramp_fuel_per_mw_gj=7.6,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.fossil_gas_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_ccgt,
                                    emissions_per_mwh_LHV_t=__emissions_gas_per_MWh_LHV_t)

__gas_chp = __gas_ccgt | {
    "type": FlexibleSourceType.GAS_CHP,
    # Central estimates for 2030 from the Danish Energy Agency,
    # September 2023 edition, source type "Gas turbine,
    # combined cycle - extraction - natural gas - large".
    "overnight_costs_per_kw_eur": 880,
    "fixed_o_m_costs_per_kw_eur": 30,
    "extraction_turbine": ExtractionTurbine.canonical(),
}

__ocgt = {
    "ramp_rate": .5,
    "construction_time_years": 2,
    "lifetime_years": 30,
    "fixed_o_m_costs_per_kw_eur": 8,
    # Central estimate for 2020 from Danish Energy Agency
    # https://ens.dk/en/our-services/projections-and-models/technology-data/technology-data-generation-electricity-and
    "overnight_costs_per_kw_eur": 480,
    "capacity_mw": 0,
    "min_capacity_mw": 0,
}

__gas_ocgt = __ocgt | __gas | {
    "type": FlexibleSourceType.GAS_PEAK,
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=4,
                                    wear_cost_per_mw_eur=20,
                                    ramp_fuel_per_mw_gj=0.2,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.fossil_gas_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_ocgt,
                                    emissions_per_mwh_LHV_t=__emissions_gas_per_MWh_LHV_t)

__gas_engines = __gas_ocgt | {
    "type": FlexibleSourceType.GAS_ENGINE,
}

__gas_ccgt_ccs = __gas_ccgt | {
    "type": FlexibleSourceType.GAS_CCGT_CCS,
    "color": ColorMap.GAS_WITH_CCS,
    "fixed_o_m_costs_per_kw_eur": 40,
    # Mid of the range of Lazard's 2023 Levelized Cost Of Energy+:
    # https://www.lazard.com/research-insights/2023-levelized-cost-of-energyplus/
    "overnight_costs_per_kw_eur": usd_to_eur_2022(2300),
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=10,
                                    wear_cost_per_mw_eur=43,
                                    ramp_fuel_per_mw_gj=7.6,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.fossil_gas_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_ccgt_ccs,
                                    emissions_per_mwh_LHV_t=__emissions_gas_ccs_per_MWh_LHV_t)

__fossil_oil = __gas_ocgt | {
    "type": FlexibleSourceType.FOSSIL_OIL,
    "color": ColorMap.GAS_PEAKERS,
}

__biomass = {
    "renewable": True,
    "color": ColorMap.BIOMASS,
    "capacity_mw": 0,
    "min_capacity_mw": 0,
}

__solid_biomass = __biomass | {  # Biomass burning/co-burning
    "type": FlexibleSourceType.SOLID_BIOMASS,
    "ramp_rate": .15,
    "construction_time_years": 4,
    "lifetime_years": 40,
    "fixed_o_m_costs_per_kw_eur": 40,
    "overnight_costs_per_kw_eur": 2500,
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=10,
                                    wear_cost_per_mw_eur=50,
                                    ramp_fuel_per_mw_gj=18,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.biomass_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_coal)

__solid_biomass_chp = __solid_biomass | {  # Biomass burning/co-burning CHP.
    "type": FlexibleSourceType.SOLID_BIOMASS_CHP,
    # Central estimate for 2020 from Danish Energy Agency (rough average between wood chips and pellets)
    # https://ens.dk/en/our-services/projections-and-models/technology-data/technology-data-generation-electricity-and
    "overnight_costs_per_kw_eur": 3000,
    "extraction_turbine": ExtractionTurbine.canonical(),
}

__biogas = __biomass | {  # Biogas burning.
    "type": FlexibleSourceType.BIOGAS,
    "ramp_rate": .5,
    "construction_time_years": 3,
    "lifetime_years": 30,
    "fixed_o_m_costs_per_kw_eur": 40,
    # Price for the actual biogas engine. Cost of production of biogas must be in the biogas price.
    # Central estimate for 2020 from Danish Energy Agency
    # https://ens.dk/en/our-services/projections-and-models/technology-data/technology-data-generation-electricity-and
    "overnight_costs_per_kw_eur": 1010,
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=10,
                                    wear_cost_per_mw_eur=20,
                                    ramp_fuel_per_mw_gj=0.2,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.biogas_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_biogas)

__biomethane_ocgt = __biomass | __ocgt | {
    "type": FlexibleSourceType.BIOGAS_PEAK,
    "color": ColorMap.BIOMASS_PEAKERS,
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=4,
                                    wear_cost_per_mw_eur=20,
                                    ramp_fuel_per_mw_gj=0.2,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        cost.biomethane_price_per_mwh_LHV_eur,
                                    efficiency_el=__efficiency_ocgt)

__smr = {
    "type": FlexibleSourceType.SMR,
    "color": ColorMap.SMR,
    "capacity_mw": 0,
    "min_capacity_mw": 0,
    # Roughly based on https://www.researchgate.net/publication/337548100_ANS_2019_Winter_Meeting_Presentation_Assessment_of_Small_Modular_Reactors_SMRs_for_Load-Following_Capabilities.
    "ramp_rate": .5,
    # Based on https://www.mpo.cz/assets/en/guidepost/for-the-media/press-releases/2023/11/Czech-SMR-Roadmap_EN.pdf.
    "lifetime_years": 60,
    # Based on https://www.eia.gov/outlooks/aeo/assumptions/pdf/elec_cost_perf.pdf, but assuming
    # technological optimism factor of 1.4, instead.
    "construction_time_years": 6,
    "overnight_costs_per_kw_eur": usd_to_eur_2022(10626),
    "fixed_o_m_costs_per_kw_eur": 100,
} | get_flexible_source_cost_params(variable_o_m_per_mwh_el_eur=4,
                                    # Based on PEMMDB - standard nuclear.
                                    wear_cost_per_mw_eur=21,
                                    ramp_fuel_per_mw_gj=8,
                                    fuel_price_per_mwh_LHV_eur_getter=lambda cost:
                                        NUCLEAR_FUEL_PRICE_EUR_MWH_EL * __efficiency_nuclear_smr,
                                    efficiency_el=__efficiency_nuclear_smr)

__dsr = {
    "type": FlexibleSourceType.DSR,
    "variable_costs_per_mwh_eur": 2_000,
    "color": ColorMap.DSR,
}

__loss_of_load = {
    "type": FlexibleSourceType.LOSS_OF_LOAD,
    "capacity_mw": 1_000_000,  # artificial to cover extra load
    "min_capacity_mw": 1_000_000,  # no need to optimize capex (there are anyway no costs).
    "variable_costs_per_mwh_eur": 4_000,  # Reflects value of lost load from MAF CZ 2022.
    "color": ColorMap.LOSS_OF_LOAD,
    "virtual": True,
    # Allow EENS/LOLE to produce heat as well.
    "extraction_turbine": ExtractionTurbine(
        base_ratio_heat_mw_per_el_mw=0,
        # Doesn't matter for thise use case.
        heat_mw_per_decreased_el_mw=1,
        # Allow "producing" heat shortage without necessarily inducing
        # power shortage.
        min_ratio_el=0
    ),
}

flexible_source_defaults: dict[FlexibleSourceType, dict] = {
    FlexibleSourceType.BIOGAS: __biogas,
    FlexibleSourceType.BIOGAS_PEAK: __biomethane_ocgt,
    FlexibleSourceType.COAL: __hard_coal,
    FlexibleSourceType.COAL_BACKPRESSURE: __hard_coal_back_pressure,
    FlexibleSourceType.COAL_EXTRACTION: __hard_coal_extraction,
    FlexibleSourceType.FOSSIL_OIL: __fossil_oil,
    FlexibleSourceType.GAS: __gas,
    FlexibleSourceType.GAS_CCGT: __gas_ccgt,
    FlexibleSourceType.GAS_CCGT_CCS: __gas_ccgt_ccs,
    FlexibleSourceType.GAS_CHP: __gas_chp,
    FlexibleSourceType.GAS_ENGINE: __gas_engines,
    FlexibleSourceType.GAS_PEAK: __gas_ocgt,
    FlexibleSourceType.LIGNITE: __lignite,
    FlexibleSourceType.LIGNITE_BACKPRESSURE: __lignite_back_pressure,
    FlexibleSourceType.LIGNITE_EXTRACTION: __lignite_extraction,
    FlexibleSourceType.LOSS_OF_LOAD: __loss_of_load,
    FlexibleSourceType.MAZUT: __mazut,
    FlexibleSourceType.OTHER_RES: __biogas | {
        "type": FlexibleSourceType.OTHER_RES
    },
    FlexibleSourceType.SOLID_BIOMASS: __solid_biomass,
    FlexibleSourceType.SOLID_BIOMASS_CHP: __solid_biomass_chp,
    FlexibleSourceType.WASTE: __waste,
    FlexibleSourceType.SMR: __smr,
    FlexibleSourceType.DSR: __dsr,
}

_flexible_sources: dict[str, dict[FlexibleSourceType, dict]] = {}


def _fix_flexible_source_params(flexible_source: dict, costs: InputCosts):
    # Define variable costs based on cost input.
    if 'variable_costs_per_mwh_eur_getter' in flexible_source:
        cost_getter = flexible_source.pop('variable_costs_per_mwh_eur_getter')
        flexible_source['variable_costs_per_mwh_eur'] = cost_getter(costs)

    if 'ramp_up_cost_mw_eur_getter' in flexible_source:
        cost_getter = flexible_source.pop('ramp_up_cost_mw_eur_getter')
        flexible_source['ramp_up_cost_mw_eur'] = cost_getter(costs)

    flexible_source = fix_source_params(flexible_source)

    if 'back_pressure_turbine' in flexible_source:
        back_pressure_turbine = flexible_source.pop('back_pressure_turbine')
        if isinstance(back_pressure_turbine, HeatSource):
            flexible_source['heat'] = back_pressure_turbine
        else:
            flexible_source['heat'] = BackPressureTurbine(**back_pressure_turbine)
    elif 'extraction_turbine' in flexible_source:
        extraction_turbine = flexible_source.pop('extraction_turbine')
        if isinstance(extraction_turbine, HeatSource):
            flexible_source['heat'] = extraction_turbine
        else:
            flexible_source['heat'] = ExtractionTurbine(**extraction_turbine)

    # Set trivial default values.
    flexible_source.setdefault("ramp_rate", 1.0)
    flexible_source.setdefault("ramp_up_cost_mw_eur", 0)
    assert 0 < flexible_source["ramp_rate"] <= 1, "ramp_rate must be in the interval (0, 1]"
    flexible_source.setdefault("max_total_twh", None)
    flexible_source.setdefault("heat", None)

    # Convert uptime ratio to max_total_twh.
    uptime_ratio = flexible_source.get('uptime_ratio', 1.0)
    if flexible_source["max_total_twh"] is None and uptime_ratio < 1:
        flexible_source["max_total_twh"] = \
            (flexible_source["capacity_mw"] * uptime_ratio * 8760) / 1_000_000
        del flexible_source["uptime_ratio"]

    return flexible_source


def get_flexible_sources(flexible_sources: Union[str, dict[FlexibleSourceType, dict]],
                         input_costs: Union[str, dict[str, float]]) -> Iterable[FlexibleSource]:
    cost: InputCosts = get_input_costs(input_costs)
    if isinstance(flexible_sources, str):
        sources_dict = deepcopy(_flexible_sources[flexible_sources])
    else:
        sources_dict = deepcopy(flexible_sources)

    # The default parameters for LOLE will be loaded below.
    sources_dict[FlexibleSourceType.LOSS_OF_LOAD] = {}

    def create_source(key: FlexibleSourceType, params_add: dict) -> FlexibleSource:
        source_dict = _fix_flexible_source_params(
            flexible_source_defaults[key] | params_add, cost
        )
        economics = SourceEconomics(**extract_economics_params(source_dict))
        return FlexibleSource(economics=economics, **source_dict)

    return [
        create_source(source_type, params_add)
        for source_type, params_add in sources_dict.items()
    ]
