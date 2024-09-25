import os
from copy import deepcopy
from functools import cache
from typing import Literal, Optional, Union
from warnings import warn
import warnings

from ..loaders import Pemmdb2023Loader
# FIXME: Avoid private access.
from ..params_library.storage import __heat_distribution
from ..params_library.interconnectors import InterconnectorsDict
from ..region import *
from ..solver_util import Solver
from ..sources.basic_source import BasicSourceType
from ..sources.flexible_source import FlexibleSourceType
from ..sources.input_costs import get_input_costs
from ..sources.reserves import Reserves
from ..sources.storage import StorageType


CoalCapexOptimization = Union[Literal["all"], Literal["cz"], Literal["none"]]

FLEXIBLE_COAL_TYPES = [
    FlexibleSourceType.COAL,
    FlexibleSourceType.COAL_BACKPRESSURE,
    FlexibleSourceType.COAL_EXTRACTION,
    FlexibleSourceType.COAL_SUPERCRITICAL,
    FlexibleSourceType.LIGNITE,
    FlexibleSourceType.LIGNITE_BACKPRESSURE,
    FlexibleSourceType.LIGNITE_EXTRACTION,
    FlexibleSourceType.LIGNITE_OLD,
    FlexibleSourceType.LIGNITE_SUPERCRITICAL,
]

# Assumed lignite prices circa 2025 for coal scenarios modelling.
# Source: ENTSO-E TYNDP 2022 Scenario Building Guidelines, April 2022
# https://2022.entsos-tyndp-scenarios.eu/building-guidelines/
_lignite_price_groups = [
    {
        "countries": [BULGARIA, CZECHIA, NORTH_MACEDONIA],
        "lignite_price_per_mwh_LHV_eur": 5.04,
    },
    {
        "countries": [
            BOSNIA_HERZEGOVINA, GERMANY, GREAT_BRITAIN, IRELAND, MONTENEGRO, POLAND, SERBIA,
            SLOVAKIA,
        ],
        "lignite_price_per_mwh_LHV_eur": 6.48,
    },
    {
        "countries": [HUNGARY, ROMANIA, SLOVENIA],
        "lignite_price_per_mwh_LHV_eur": 8.53,
    },
    {
        "countries": [GREECE, TURKEY],
        "lignite_price_per_mwh_LHV_eur": 11.16,
    },
]

_default_input_costs = "2025"
# Derating factors to accont for the difference in net output compared
# to nominal capacity. Approximated according to figures in ERÚ reports
# (self-consumption for electricity and heat production).
_coal_net_capacity_derating = .88
_ccgt_net_capacity_derating = .98
_ocgt_net_capacity_derating = .93  # Applies to both OCGT and engines.

# Condensing turbine (no heat) hard coal-fired power plants:
#   Dětmarovice (600)
hard_coal_condensing_capacity_mw = 600
# Backpressure turbine hard coal-fired CHP:
#   Kopřivnice (19)
hard_coal_backpressure_capacity_mw = 19
# Extraction turbine hard coal-fired CHP:
#   Třebovice (174), Třinec (100), Karviná (79)
hard_coal_extraction_capacity_mw = 353

# Back-pressure turbine lignite-fired CHP:
#   Mělník I (120), Unipetrol (112), Komořany (67),
#   České Budějovice (66), Mondi Štětí (65), Opatovice (60),
#   Otrokovice (50), Lovochemie (40), Strakonice (30),
#   Trmice (29), Zlín (25), Tisová I (13), Písek (8)
lignite_backpressure_chp_capacity_mw = 685
# Condensing turbine (no heat) lignite-fired power plants:
#   Tušimice II (800), Mělník II (220), Opatovice (180),
#   Tisová II (105), Tisová I (57), Mondi Štětí (48),
#   Komořany (30), Trmice (30)
lignite_condensing_capacity_mw = 1470
# Old, inefficient condensing lignite-fired:
#   Počerady (1000), Chvaletice (820)
lignite_old_condensing_capacity_mw = 1820
# New, efficient supercritical condensing lignite-fired:
#   Ledvice IV (660)
lignite_sc_condensing_capacity_mw = 660
# Extraction turbine lignite-fired CHP:
#   Prunéřov II (750), Kladno (404), Plzeň (252), Vřesová (239),
#   Komořany (142), Opatovice (123), Mělník I (120), Tisová I (114),
#   Ledvice III (110), Ško-Energo (90), Synthesia (75), Hodonín (57),
#   Olomouc (49), Zlín (39), Trmice (30)
lignite_extraction_chp_capacity_mw = 2594

# Combined-cycle natural gas-fired plants with power production only:
#   Počerady II (840)
gas_ccgt_capacity_mw = 840
# Open-cycle natural gas-fired peaking plant:
#   Gama/UECD Prostějov (108), Decci Vraňany (30)
gas_ocgt_capacity_mw = 138
# Combined-cycle (or similar) natural gas-fired CHP:
#   Vřesová (400),
#   Kladno (110), Červený mlýn (95), Kyjov (23),
#   Špitálka (80), Synthos Kralupy (67)
gas_chp_capacity_mw = 400 + 228 + 147
# Natural gas-fired combustion engine CHP.
#   C-Energy Planá (45) and many small CHPs < 5 MWe
gas_engine_chp_capacity_mw = 395

# Zero capex for coal plants to incentivize continued operation
# of existing plants + no additional maintenance costs are assumed
# in order to steelman the position of coal.
coal_capex_eur_per_kw = 0
# 1/10 of the default capex (3000 €/kW) for coal CHP – some maintenance
# is assumed to be required, in contrast with power-only coal.
coal_chp_capex_eur_per_kw = 300

# Maximum allowed annual capacity factor for biomass and biogas.
bioenergy_max_capacity_factor = .8
# Maximum capacity factor for natural gas and coal (hard and lignite).
fossil_gas_max_capacity_factor = .85
coal_max_capacity_factor = .85

# Assumed installed capacities in Czechia.
_cz_coal_capacities = {
    FlexibleSourceType.COAL: {
        "capacity_mw": hard_coal_condensing_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.COAL_BACKPRESSURE: {
        "capacity_mw": hard_coal_backpressure_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.COAL_EXTRACTION: {
        "capacity_mw": hard_coal_extraction_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.LIGNITE: {
        "capacity_mw": lignite_condensing_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.LIGNITE_BACKPRESSURE: {
        "capacity_mw": lignite_backpressure_chp_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.LIGNITE_EXTRACTION: {
        "capacity_mw": lignite_extraction_chp_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.LIGNITE_OLD: {
        "capacity_mw": lignite_old_condensing_capacity_mw * _coal_net_capacity_derating
    },
    FlexibleSourceType.LIGNITE_SUPERCRITICAL: {
        "capacity_mw": lignite_sc_condensing_capacity_mw * _coal_net_capacity_derating
    },
}

cz_2025_basic = _cz_coal_capacities | {
    BasicSourceType.SOLAR: {"capacity_mw": 3500},
    BasicSourceType.ONSHORE: {"capacity_mw": 340},
    # "paroplyn"
    FlexibleSourceType.GAS_CCGT: {
        "capacity_mw": gas_ccgt_capacity_mw * _ccgt_net_capacity_derating
    },
    # "paroplyn teplárny"
    FlexibleSourceType.GAS_CHP: {
        "capacity_mw": gas_chp_capacity_mw * _ccgt_net_capacity_derating
    },
    # gas peakers
    FlexibleSourceType.GAS_PEAK: {
        "capacity_mw": gas_ocgt_capacity_mw * _ocgt_net_capacity_derating
    },
    # "plynové teplárny (spalovací motory)"
    FlexibleSourceType.GAS_ENGINE_CHP: {
        "capacity_mw": gas_engine_chp_capacity_mw * _ocgt_net_capacity_derating
    },
    FlexibleSourceType.SOLID_BIOMASS: {"capacity_mw": 0},
    # "biomasa teplárny"
    FlexibleSourceType.SOLID_BIOMASS_CHP: {
        "capacity_mw": 190 * _ocgt_net_capacity_derating
    },
    # "bioplyn"
    FlexibleSourceType.BIOGAS: {"capacity_mw": 400 * _ocgt_net_capacity_derating},
    FlexibleSourceType.DSR: {"capacity_mw": 0},
    # Batteries:
    # 20 MW / 22 MWh: https://www.obnovitelne.cz/clanek/3278/nejmodernejsi-elektrarna-v-cesku-je-v-provozu-energy-nest-ukazuje-jak-si-poradime-bez-uhli
    # 10 MW / 10 MWh: https://www.cez.cz/cezes/cs/o-spolecnosti/aktuality/nejvetsi-ceska-baterie-ve-vitkovicich-zahajila-ostry-provoz.-akumulator-od-cez-esco-pomuze-stabilizovat-energetickou-soustavu-188437
    # 5 MW / 7.5 MWh: https://www.obnovitelne.cz/clanek/2297/u-sokolova-vzniklo-obri-bateriove-uloziste-pomuze-stabilizovat-sit-s-obnovitelnymi-zdroji
    # 4 MW / 2.5 MWh: https://www.c-energy.cz/energeticky-zdroj-c-energy-plana-uvadi-do-provozu-nejvetsi-bateriove-uloziste-v-cr-dodane-firmou-siemens
    # 4 MW / 2.8 MWh: https://www.cez.cz/cezes/cs/o-spolecnosti/aktuality/bateriove-uloziste-tusimice-4mw-28-mwh-92675
    StorageType.LI: {"capacity_mw": 43, "max_energy_mwh": 45},
}

cz_2028_basic = _cz_coal_capacities | {
    BasicSourceType.SOLAR: {"capacity_mw": 7000},
    BasicSourceType.ONSHORE: {"capacity_mw": 500},
    # "paroplyn"
    FlexibleSourceType.GAS_CCGT: {
        "capacity_mw": gas_ccgt_capacity_mw * _ccgt_net_capacity_derating
    },
    # "paroplyn teplárny"
    FlexibleSourceType.GAS_CHP: {
        # New: Mělník (240), Dětmarovice (10)
        "capacity_mw": (gas_chp_capacity_mw + 250) * _ccgt_net_capacity_derating
    },
    # gas peakers
    FlexibleSourceType.GAS_PEAK: {
        "capacity_mw": gas_ocgt_capacity_mw * _ocgt_net_capacity_derating
    },
    # "plynové teplárny (spalovací motory)"
    FlexibleSourceType.GAS_ENGINE_CHP: {
        # New: Prunéřov (50) and some others
        "capacity_mw": (gas_engine_chp_capacity_mw + 200) * _ocgt_net_capacity_derating
    },
    FlexibleSourceType.SOLID_BIOMASS: {"capacity_mw": 0},
    # "biomasa teplárny"
    FlexibleSourceType.SOLID_BIOMASS_CHP: {
        "capacity_mw": 300 * _ocgt_net_capacity_derating
    },
    # "bioplyn"
    FlexibleSourceType.BIOGAS: {"capacity_mw": 400 * _ocgt_net_capacity_derating},
    FlexibleSourceType.DSR: {"capacity_mw": 0},
    StorageType.LI: {"capacity_mw": 100, "max_energy_mwh": 200},
}

cz_2028_advanced = cz_2028_basic | {
    BasicSourceType.SOLAR: {"capacity_mw": 10100},
    BasicSourceType.ONSHORE: {"capacity_mw": 800},
    # "paroplyn teplárny"
    FlexibleSourceType.GAS_CHP: {
        "capacity_mw": (gas_chp_capacity_mw + 500) * _ccgt_net_capacity_derating
    },
    # "plynové teplárny (spalovací motory)"
    FlexibleSourceType.GAS_ENGINE_CHP: {
        "capacity_mw": (gas_engine_chp_capacity_mw + 500) * _ocgt_net_capacity_derating
    },
    # "biomasa teplárny"
    FlexibleSourceType.SOLID_BIOMASS_CHP: {
        "capacity_mw": 400 * _ocgt_net_capacity_derating
    },
    # "bioplyn" + "biometan"
    FlexibleSourceType.BIOGAS: {"capacity_mw": (350 + 50) * _ocgt_net_capacity_derating},
    StorageType.LI: {"capacity_mw": 300, "max_energy_mwh": 600},
}

global_adjustments: dict[FlexibleSourceType, dict] = {
    # Fossil coal sources.
    FlexibleSourceType.COAL: {
        "overnight_costs_per_kw_eur": coal_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.COAL_BACKPRESSURE: {
        "overnight_costs_per_kw_eur": coal_chp_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.COAL_EXTRACTION: {
        "overnight_costs_per_kw_eur": coal_chp_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.COAL_SUPERCRITICAL: {
        "overnight_costs_per_kw_eur": coal_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.LIGNITE: {
        "overnight_costs_per_kw_eur": coal_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.LIGNITE_BACKPRESSURE: {
        "overnight_costs_per_kw_eur": coal_chp_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.LIGNITE_EXTRACTION: {
        "overnight_costs_per_kw_eur": coal_chp_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.LIGNITE_OLD: {
        "overnight_costs_per_kw_eur": coal_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    FlexibleSourceType.LIGNITE_SUPERCRITICAL: {
        "overnight_costs_per_kw_eur": coal_capex_eur_per_kw,
        "uptime_ratio": coal_max_capacity_factor,
    },
    # Fossil gas sources.
    FlexibleSourceType.GAS_CCGT: {
        "uptime_ratio": fossil_gas_max_capacity_factor,
    },
    FlexibleSourceType.GAS_CHP: {
        "uptime_ratio": fossil_gas_max_capacity_factor,
    },
    FlexibleSourceType.GAS_PEAK: {
        "uptime_ratio": fossil_gas_max_capacity_factor,
    },
    FlexibleSourceType.GAS_ENGINE: {
        "uptime_ratio": fossil_gas_max_capacity_factor,
    },
    FlexibleSourceType.GAS_ENGINE_CHP: {
        "uptime_ratio": fossil_gas_max_capacity_factor,
    },
    # Bioenergy sources.
    FlexibleSourceType.BIOGAS: {
        "uptime_ratio": bioenergy_max_capacity_factor,
    },
    FlexibleSourceType.SOLID_BIOMASS: {
        "uptime_ratio": bioenergy_max_capacity_factor,
    },
    FlexibleSourceType.SOLID_BIOMASS_CHP: {
        "uptime_ratio": bioenergy_max_capacity_factor,
    },
}


def get_lignite_price(country: Zone) -> Optional[float]:
    for price_group in _lignite_price_groups:
        if country in price_group["countries"]:
            return price_group["lignite_price_per_mwh_LHV_eur"]

    return None


def construct_grid(pemmdb_loader: Pemmdb2023Loader,
                   pemmdb_year: int,
                   aggregation_level: Optional[str] = None,
                   include_reserves=False):
    if aggregation_level == "cz":
        return {
            "countries": {},
        }

    if aggregation_level == "ce":
        return {
            "countries": {
                AUSTRIA: pemmdb_loader.get_country(AUSTRIA, pemmdb_year, include_reserves=include_reserves),
                GERMANY: pemmdb_loader.get_country(GERMANY, pemmdb_year, include_reserves=include_reserves),
                POLAND: pemmdb_loader.get_country(POLAND, pemmdb_year, include_reserves=include_reserves),
                SLOVAKIA: pemmdb_loader.get_country(SLOVAKIA, pemmdb_year, include_reserves=include_reserves),
            },
            "interconnectors": pemmdb_loader.get_interconnectors(
                year=pemmdb_year,
                countries=[AUSTRIA, CZECHIA, GERMANY, POLAND, SLOVAKIA],
            ),
        }

    all_countries = REGION_AGGREGATION_LEVELS["none"] - {CYPRUS, MALTA}

    if aggregation_level is None:
        aggregates = set()
        aggregated_countries = set()
    elif aggregation_level not in REGION_AGGREGATION_LEVELS:
        raise KeyError(f"Uknown country aggregation level '{aggregation_level}'")
    else:
        aggregates = REGION_AGGREGATION_LEVELS[aggregation_level]
        aggregated_countries = set.union(*(get_aggregated_countries(agg) for agg in aggregates))

    standalone_countries = all_countries - aggregated_countries - {CZECHIA}

    countries_dict: dict[Region, dict] = {
        country: pemmdb_loader.get_country(
            country,
            pemmdb_year,
            include_reserves=include_reserves,
        )
        for country in standalone_countries
    }

    for aggregate in aggregates:
        aggregate_dict = pemmdb_loader.get_countries_from_aggregate(
            aggregate,
            pemmdb_year,
            include_reserves=include_reserves
        )
        countries_dict |= aggregate_dict

    return {
        "countries": countries_dict,
        "interconnectors": pemmdb_loader.get_interconnectors(
            year=pemmdb_year,
            aggregate_countries=aggregates,
        ),
    }


@cache
def get_pemmdb_loader(root_dir: str = ".") -> Pemmdb2023Loader:
    data_file_path = os.path.join(root_dir, "data/pemmdb/ERAA2023 PEMMDB Generation.xlsx")
    return Pemmdb2023Loader(data_file=data_file_path)


def make_run(name: str,
             scenarios: list[dict],
             *,
             common_year: int,
             entsoe_year: int,
             pecd_year: int,
             optimize_coal: CoalCapexOptimization = "none",
             optimize_heat=False,
             optimize_ramp_up_costs=False,
             tyndp_lignite_prices=False,
             root_dir: str = ".",
             aggregation_level: Optional[str] = None,
             solver: Optional[Solver] = None) -> dict:
    pemmdb_loader = get_pemmdb_loader(root_dir=root_dir)

    return {
        "config": {
            "analysis_name": name,
            "common_years": [common_year],
            "entsoe_years": [entsoe_year],
            "pecd_years": [pecd_year],
            "filter": {
                "week_sampling": 4,  # Plot every fourth week in the output.
                # "countries": [CZECHIA],
                # "days": [
                #     "2020-06-11", "2020-06-12", "2020-06-13",
                #     "2020-11-25", "2020-11-26", "2020-11-27"
                # ],
            },
            "output": {
                "format": "png",
                "dpi": 150,
                "heat": optimize_heat,
                "size_y_week": 0.7,
                "parts": ["titles", "weeks", "week_summary", "year_stats"],
                "regions": "separate",
            },
            "optimize_capex": optimize_coal != "none",
            "optimize_heat": optimize_heat,
            "optimize_ramp_up_costs": optimize_ramp_up_costs,
            # "load_previous_solution": True,
            # "include_transmission_loss_in_price": True,
            "store_model": False,
            "solver": solver,
        },
        "scenarios": [
            make_scenario(
                scenario_spec,
                pemmdb_loader=pemmdb_loader,
                aggregation_level=aggregation_level,
                optimize_coal=optimize_coal,
                optimize_heat=optimize_heat,
                tyndp_lignite_prices=tyndp_lignite_prices,
            )
            for scenario_spec in scenarios
        ],
    }


def _make_adjustments(region_spec: dict, adjustments: Optional[dict] = None) -> None:
    if not adjustments:
        return

    for source_type, new_params in adjustments.items():
        if source_type in BasicSourceType:
            # Ignore economics-only adjustment unless the source is
            # already specified in the country.
            if source_type not in region_spec["basic_sources"] and \
                    "capacity_mw" not in new_params:
                continue

            old_params = region_spec["basic_sources"].get(source_type, {})
            region_spec["basic_sources"][source_type] = old_params | new_params

            # Overwrite minimum capacity if max is specified but min isn't.
            if "capacity_mw" in new_params and "min_capacity_mw" not in new_params:
                region_spec["basic_sources"][source_type]["min_capacity_mw"] = \
                    new_params["capacity_mw"]
        elif source_type in FlexibleSourceType:
            # Ignore economics-only adjustment unless the source is
            # already specified in the country.
            if source_type not in region_spec["flexible_sources"] and \
                    "capacity_mw" not in new_params:
                continue

            old_params = region_spec["flexible_sources"].get(source_type, {})
            region_spec["flexible_sources"][source_type] = old_params | new_params

            # Overwrite minimum capacity if max is specified but min isn't.
            if "capacity_mw" in new_params and "min_capacity_mw" not in new_params:
                region_spec["flexible_sources"][source_type]["min_capacity_mw"] = \
                    new_params["capacity_mw"]
        elif source_type in StorageType:
            for storage in region_spec["storage"]:
                if storage["type"] == source_type:
                    storage |= new_params

                    # Overwrite minimum capacity if max is specified but min isn't.
                    if "capacity_mw" in new_params and "min_capacity_mw" not in new_params:
                        storage["min_capacity_mw"] = new_params["capacity_mw"]
                    if "capacity_mw" in new_params and "capacity_mw_charging" not in new_params:
                        storage["capacity_mw_charging"] = new_params["capacity_mw"]
                        storage["min_capacity_mw_charging"] = new_params["capacity_mw"]

                    break
            else:
                warn(f"Storage type {source_type} not modified because it's not present")
        else:
            raise KeyError(f"Unknown source type '{source_type}'")


def make_scenario(scenario_spec: dict,
                  *,
                  pemmdb_loader: Pemmdb2023Loader,
                  aggregation_level: Optional[str] = None,
                  optimize_coal: CoalCapexOptimization = "none",
                  optimize_heat=False,
                  include_reserves=False,
                  tyndp_lignite_prices=False) -> dict:
    pemmdb_year: int = scenario_spec["year"]
    cz_adjustments = scenario_spec.get("adjustments")
    global_adjustments = scenario_spec.get("global_adjustments")
    global_input_costs = scenario_spec.get("input_costs", _default_input_costs)
    optimize_capex = scenario_spec.get("optimize_capex")

    czechia = pemmdb_loader.get_country(CZECHIA, pemmdb_year)
    _make_adjustments(czechia, cz_adjustments)

    if optimize_heat:
        heat_storage = __heat_distribution | {
            "nominal_mw": 5_000,
            "max_energy_mwh": 25_000
        }
        czechia["storage"].append(heat_storage)
        czechia["heat_demand"] = True
        czechia["temperatures"] = "Turany.csv"

    if include_reserves:
        # Manually set the required reserves for Czechia to a somewhat
        # arbitrary but non-negligible amount. These values very roughly
        # correspond to the typical range of required capacity for
        # FCR and FRR.
        czechia["reserves"] = Reserves(
            additional_load_mw=0,
            hydro_capacity_reduction_mw=300
        )

    # Uniformly lower the demand in Czechia by 10%
    # for better calibration.
    czechia["load_factors"]["load_base"] = .9

    if aggregation_level == "cz_only":
        context_grid = {
            "countries": {
                CZECHIA: czechia,
            },
        }
    else:
        context_grid = construct_grid(
            pemmdb_loader,
            pemmdb_year,
            aggregation_level=aggregation_level,
            include_reserves=include_reserves
        )

        # Make sure Czechia is plotted first for faster debugging.
        context_grid["countries"] = {CZECHIA: czechia} | context_grid["countries"]

    # Adjust lignite prices to match ENTSO-E TYNDP assumptions.
    if tyndp_lignite_prices:
        # TYNDP lignite prices stratification is only supported for
        # no aggregation and fine aggregation level.
        if aggregation_level is not None and aggregation_level != "fine":
            warnings.warn(
                "TYNDP lignite prices requested, but aggregation level must be ‘none’ or ‘fine’"
            )
        else:
            for country_code, country_spec in context_grid["countries"].items():
                if lignite_price := get_lignite_price(country_code):
                    country_spec["input_costs"] = get_input_costs(
                        country_spec.get("input_costs", global_input_costs)
                    )
                    country_spec["input_costs"].lignite_price_per_mwh_LHV_eur = lignite_price

    # Ad-hoc globally-applied country-specific adjustments.
    if croatia := context_grid["countries"].get(CROATIA):
        # The "Others non-renewable" category in Croatia most likely
        # constitutes hard coal-fired CHP.
        croatia["flexible_sources"][FlexibleSourceType.COAL_EXTRACTION] = \
            deepcopy(croatia["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del croatia["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    if france := context_grid["countries"].get(FRANCE):
        # The "Others non-renewable" category in France constitutes
        # gas plants rather than the default lignite CHP.
        france["flexible_sources"][FlexibleSourceType.GAS_PEAK] = \
            deepcopy(france["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del france["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    if germany := context_grid["countries"].get(GERMANY):
        # About 8.26 GW of the lignite-fired and 6.74 GW of the hard/
        # /bituminous coal-fired plants in Germany have supercritical
        # combustion (after 2025, according to the Global Coal Plant
        # Tracker). This is around 56% and 52% of the total, resp.
        # We assume the ratio will stay the same.
        coal_de = germany["flexible_sources"][FlexibleSourceType.COAL]
        lig_de = germany["flexible_sources"][FlexibleSourceType.LIGNITE]

        coal_sc_de = {"capacity_mw": coal_de["capacity_mw"] * .52}
        lig_sc_de = {"capacity_mw": lig_de["capacity_mw"] * .56}

        germany["flexible_sources"][FlexibleSourceType.COAL_SUPERCRITICAL] = \
            coal_sc_de
        germany["flexible_sources"][FlexibleSourceType.LIGNITE_SUPERCRITICAL] = \
            lig_sc_de

        coal_de["capacity_mw"] -= coal_sc_de["capacity_mw"]
        lig_de["capacity_mw"] -= lig_sc_de["capacity_mw"]

    if italy := context_grid["countries"].get(ITALY):
        # The "Others non-renewable" category in Italy constitutes
        # gas plants rather than the default lignite CHP.
        italy["flexible_sources"][FlexibleSourceType.GAS_PEAK] = \
            deepcopy(italy["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del italy["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    if netherlands := context_grid["countries"].get(NETHERLANDS):
        # All of the Netherlands' coal-fired plants are supercritical.
        netherlands["flexible_sources"][FlexibleSourceType.COAL_SUPERCRITICAL] = \
            deepcopy(netherlands["flexible_sources"][FlexibleSourceType.COAL])
        del netherlands["flexible_sources"][FlexibleSourceType.COAL]
        # The "Others non-renewable" category in the Netherlands constitutes
        # gas plants rather than the default lignite CHP.
        netherlands["flexible_sources"][FlexibleSourceType.GAS_PEAK] = \
            deepcopy(netherlands["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del netherlands["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    if norway := context_grid["countries"].get(NORWAY):
        # The "Others non-renewable" category in Norway constitutes
        # gas plants rather than the default lignite CHP.
        norway["flexible_sources"][FlexibleSourceType.GAS_PEAK] = \
            deepcopy(norway["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del norway["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    if poland := context_grid["countries"].get(POLAND):
        # Poland has about 2.9 GW of supercritical lignite-fired and
        # 3.2 GW of supercritical hard coal-fired plants (after 2025,
        # according to the Global Coal Plant Tracker). This corresponds
        # to about 45% and 22%, respectively.
        coal_pl = poland["flexible_sources"][FlexibleSourceType.COAL]
        lig_pl = poland["flexible_sources"][FlexibleSourceType.LIGNITE]

        coal_sc_pl = {"capacity_mw": coal_pl["capacity_mw"] * .22}
        lig_sc_pl = {"capacity_mw": lig_pl["capacity_mw"] * .45}

        poland["flexible_sources"][FlexibleSourceType.COAL_SUPERCRITICAL] = \
            coal_sc_pl
        poland["flexible_sources"][FlexibleSourceType.LIGNITE_SUPERCRITICAL] = \
            lig_sc_pl

        coal_pl["capacity_mw"] -= coal_sc_pl["capacity_mw"]
        lig_pl["capacity_mw"] -= lig_sc_pl["capacity_mw"]

    if spain := context_grid["countries"].get(SPAIN):
        # Spain reported no capacity in natural gas in an earlier
        # version of PEMMDB. Fall back to the capacity as of 2023
        # (according to Energy-Charts).
        spain["flexible_sources"][FlexibleSourceType.GAS_CCGT] = {
            "capacity_mw": 29_800,
            "min_capacity_mw": 29_800,
        }
        # The "Others non-renewable" sources are presumably fossil
        # gas-fired.
        spain["flexible_sources"][FlexibleSourceType.GAS_PEAK] = \
            deepcopy(spain["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION])
        del spain["flexible_sources"][FlexibleSourceType.LIGNITE_EXTRACTION]

    # Apply source adjustments to all regions if requested.
    if global_adjustments:
        for country in context_grid["countries"].values():
            _make_adjustments(country, global_adjustments)

    # Optimize coal capacity in Czechia only.
    if optimize_coal == "cz" and optimize_capex is not False:
        flexible_sources = czechia["flexible_sources"]
        for source_type in FLEXIBLE_COAL_TYPES:
            if source_type in flexible_sources:
                flexible_sources[source_type]["min_capacity_mw"] = 0
    # Optimize coal capacity across all the zones.
    elif optimize_coal == "all" and optimize_capex is not False:
        for country in context_grid["countries"].values():
            flexible_sources = country["flexible_sources"]
            for source_type in FLEXIBLE_COAL_TYPES:
                if source_type in flexible_sources:
                    flexible_sources[source_type]["min_capacity_mw"] = 0

    scenario_out = context_grid | {
        "name": scenario_spec.get("name", "default"),
        "input_costs": global_input_costs,
        "pecd_target_year": pemmdb_year,
    }

    if optimize_capex is not None:
        scenario_out["optimize_capex"] = optimize_capex

    return scenario_out


def minimize_sources(sources1: dict, sources2: dict, buildout_factor: float) -> None:
    for source, source_spec in sources1.items():
        capacity1 = source_spec["capacity_mw"]
        capacity2 = sources2.get(source, {}).get("capacity_mw", 0)
        capacity_mw = capacity2
        # Reduce if new capacity should get build out.
        if capacity2 > capacity1:
            capacity_mw = capacity1 + (capacity2 - capacity1) * buildout_factor

        source_spec["capacity_mw"] = capacity_mw
        source_spec["min_capacity_mw"] = capacity_mw


def scale_intercon_capacities(scenario: dict, factor: float) -> dict:
    interconnectors: InterconnectorsDict = scenario["interconnectors"]

    for destination_map in interconnectors.values():
        for link_spec in destination_map.values():
            link_spec["capacity_mw"] *= factor

    return scenario


def make_pessimistic_scenario(name: str,
                              base_scenario: dict,
                              future_scenario: dict,
                              buildout_factor: float = 1.0,
                              demand_increase: float = 1.0) -> dict:
    # We start from the future in order to capture future demand.
    scenario_min = deepcopy(future_scenario)
    scenario_min["name"] = name
    # Reduce interconnection capacities uniformly by 20% compared
    # to the base scenario (2025).
    if "interconnectors" in scenario_min:
        scenario_min["interconnectors"] = deepcopy(base_scenario["interconnectors"])
        scale_intercon_capacities(scenario_min, .8)

    for country, spec_min in scenario_min["countries"].items():
        spec_base = base_scenario["countries"][country]

        # Reconcile basic sources.
        minimize_sources(spec_base["basic_sources"], spec_min["basic_sources"], buildout_factor)

        # Reconcile flexible sources.
        minimize_sources(spec_base["flexible_sources"],
                         spec_min["flexible_sources"], buildout_factor)

        # Keep storage at 2025 levels. We assume it's not going to
        # decrease anywhere.

    # Reduce interconnection capacities uniformly by 20%.
    if "interconnectors" in scenario_min:
        scale_intercon_capacities(scenario_min, .8)

    # Increase Czech electricity demand uniformly by a given factor
    # (compared to the base scenarios, which was decreased to 90% of
    # the original PECD demand).
    scenario_min["countries"][CZECHIA]["load_factors"]["load_base"] = 0.9 * demand_increase

    return scenario_min
