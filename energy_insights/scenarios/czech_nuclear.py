from copy import deepcopy
from enum import Enum
from typing import Optional

from ..loaders.ember_ng import EmberNgLoader
from ..region import *
from ..sources.basic_source import BasicSourceType
from ..sources.economics import usd_to_eur_2022
from ..sources.flexible_source import FlexibleSourceType
from ..sources.storage import StorageType

# Make import available for power sector for 3 EUR per kg (not super cheap).
# This is roughly 100% more the cost of what Czech national hydrogen strategy assumes.
_default_h2_price_per_MWh = 3 * 30  # 3 EUR per kg of hydrogen.

# Temelin netto capacity (https://cs.wikipedia.org/wiki/Jadern%C3%A1_elektr%C3%A1rna_Temel%C3%ADn).
_existing_nuclear_mw = 2060
_nuclear_0_mw = _existing_nuclear_mw
# Assuming 1 new blocks with 1100 netto MW.
_nuclear_1_mw = _existing_nuclear_mw + 1100
# Assuming 2 new blocks with 1100 netto MW each.
_nuclear_2_mw = _existing_nuclear_mw + 2200
# Assuming 3 new blocks with 1100 netto MW each (plus one SMR, specified elsewhere).
_nuclear_3_mw = _existing_nuclear_mw + 3300
# Assuming 4 new blocks with 1100 netto MW each (plus one SMRs, specified elsewhere).
_nuclear_4_mw = _existing_nuclear_mw + 4400

# 7 new large reactors should be enough for basically unlimited optimization.
_nuclear_7_mw = _existing_nuclear_mw + 7700

_discount_rate_smr = 1.06
_one_smr_mw = 350


class RESPrices(Enum):
    DEFAULT = 0
    HIGHER = 1
    LOWER = 2

_higher_h2_price_per_MWh = 3.5 * 30
_lower_h2_price_per_MWh = 2.5 * 30

def _modify_coutry_by_RES_prices(country: dict, RES_prices: RESPrices) -> None:
    def modify_basic(source, higher_value_USD, lower_value_USD):
        if RES_prices == RESPrices.HIGHER:
            source["overnight_costs_per_kw_eur"] = usd_to_eur_2022(higher_value_USD)
        elif RES_prices == RESPrices.LOWER:
            source["overnight_costs_per_kw_eur"] = usd_to_eur_2022(lower_value_USD)
    # Sensitivity values are in the middle between IEA 2030 estimate and the 2022 (higher) and
    # 2050 (lower) estimates.
    modify_basic(country["basic_sources"][BasicSourceType.SOLAR], 795, 505)
    modify_basic(country["basic_sources"][BasicSourceType.ONSHORE], 1700, 1610)
    modify_basic(country["basic_sources"][BasicSourceType.OFFSHORE], 2810, 1870)

    for storage in country["storage"]:
        if storage["type"] == StorageType.HYDROGEN or storage["type"] == StorageType.HYDROGEN_PEAK:
            if RES_prices == RESPrices.HIGHER:
                storage["cost_sell_buy_mwh_eur"] = _higher_h2_price_per_MWh
                storage["separate_charging"]["overnight_costs_per_kw_eur"] = usd_to_eur_2022(
                    990)
            elif RES_prices == RESPrices.LOWER:
                storage["cost_sell_buy_mwh_eur"] = _lower_h2_price_per_MWh
                storage["separate_charging"]["overnight_costs_per_kw_eur"] = usd_to_eur_2022(
                    530)
        elif storage["type"] == StorageType.LI_2H:
            if RES_prices == RESPrices.HIGHER:
                storage["overnight_costs_per_kw_eur"] = 500
            elif RES_prices == RESPrices.LOWER:
                storage["overnight_costs_per_kw_eur"] = 300


def make_original_scenarios(ember_loader: EmberNgLoader,
                            ember_scenario: str,
                            target_year: int) -> list[dict]:
    czechia: dict = ember_loader.get_country(ember_scenario, target_year, CZECHIA)
    return [
        {
            "name": "original",
            "countries": {CZECHIA: czechia},
        }
    ]


def make_nuclear_scenario(base_czechia_profile: dict,
                          min_nuclear_capacity_mw: int,
                          max_nuclear_capacity_mw: int,
                          allow_smrs: int = 0) -> dict:
    profile = deepcopy(base_czechia_profile)
    profile["basic_sources"][BasicSourceType.NUCLEAR]["capacity_mw"] = max_nuclear_capacity_mw
    profile["basic_sources"][BasicSourceType.NUCLEAR]["min_capacity_mw"] = min_nuclear_capacity_mw
    profile["flexible_sources"][FlexibleSourceType.SMR]["capacity_mw"] = allow_smrs * _one_smr_mw
    return profile


def make_nuclear_scenarios(ember_loader: EmberNgLoader,
                           ember_scenario: str,
                           target_year_cz: int,
                           RES_prices: RESPrices,
                           optimize_nuclear=False,
                           fix_nuclear_capacity_mw=Optional[int],
                           higher_limits=False) -> list[dict]:
    # Base all scenarios in CZ from TECHNOLOGY_DRIVEN.
    # Do not normalize PECD production in Czechia as PECD is more trustworthy than Ember.
    czechia: dict = ember_loader.get_country(
        EmberNgLoader.TECHNOLOGY_DRIVEN_BATTERY, target_year_cz, country=CZECHIA,
        allow_capex_optimization=True, normalize_pecd=False)

    czechia["basic_sources"][BasicSourceType.SOLAR]["capacity_mw"] = 70_000
    if higher_limits:
        czechia["basic_sources"][BasicSourceType.ONSHORE]["capacity_mw"] = 30_000
    else:
        czechia["basic_sources"][BasicSourceType.ONSHORE]["capacity_mw"] = 10_000

    # Override WACC for nuclear to 4 % to account for full state assistance.
    nuclear = czechia["basic_sources"][BasicSourceType.NUCLEAR]
    nuclear["discount_rate"] = 1.04
    # That is NOAK, add a 50% FOAK premium, relevant for CZ.
    nuclear["overnight_costs_per_kw_eur"] = usd_to_eur_2022(6600 * 1.5)
    # nuclear["construction_time_years"] = 10
    # Mark existing nuclear capacity as for free (payed off).
    nuclear["paid_off_capacity_mw"] = _existing_nuclear_mw
    # Fix installed for nuclear for scenarios with no nuclear (in 2050).
    czechia["installed_gw"][BasicSourceType.NUCLEAR] = 4.047

    if ember_scenario == EmberNgLoader.NO_GAS_WITH_CCS:
        czechia["flexible_sources"][FlexibleSourceType.GAS_CCGT_CCS]["capacity_mw"] = 0
    else:
        czechia["flexible_sources"][FlexibleSourceType.GAS_CCGT_CCS]["capacity_mw"] = 10_000
        czechia["flexible_sources"][FlexibleSourceType.GAS_CCGT_CCS]["max_total_twh"] = 10

    # Add potential for SMRs to all scenarios (with government-backed 6% WACC).
    # Capacity is added later.
    czechia["flexible_sources"][FlexibleSourceType.SMR] = {"discount_rate": _discount_rate_smr}

    # Force at least some biomass CHP capacity.
    czechia["flexible_sources"][FlexibleSourceType.SOLID_BIOMASS_CHP]["min_capacity_mw"] = 700
    # Theoretically at most 15 TWh per year, in practice usually much less (because it is costly).
    czechia["flexible_sources"][FlexibleSourceType.SOLID_BIOMASS_CHP]["capacity_mw"] = 2446
    czechia["flexible_sources"][FlexibleSourceType.SOLID_BIOMASS_CHP]["uptime_ratio"] = 0.7

    # Add further types of peaker sources with high emissions. Ignoring biomethane (as it's capacity
    # is low and thus saved costs are relatively small).
    czechia["flexible_sources"][FlexibleSourceType.GAS_PEAK] = {
        "capacity_mw": 10_000,
        "overnight_costs_per_kw_eur": 470}

    for storage in czechia["storage"]:
        if storage["type"] == StorageType.LI_2H:
            # Set the minimum ratio to 15% of VRE installed capacity.
            storage["min_charging_capacity_ratio_to_VRE"] = 0.15
            # To this end, set an arbitrarily high limit so that it has always feasible solution.
            storage["nominal_mw"] = 40_000
        if storage["type"] == StorageType.HYDROGEN:
            storage["capacity_mw"] = 20_000
            storage["capacity_mw_charging"] = 20_000
            # Make arbitrary import available for power sector.
            storage["min_final_energy_mwh"] = 0
            storage["cost_sell_buy_mwh_eur"] = _default_h2_price_per_MWh
        if storage["type"] == StorageType.HYDROGEN_PEAK:
            storage["capacity_mw"] = 20_000
            storage["min_final_energy_mwh"] = 0
            storage["cost_sell_buy_mwh_eur"] = _default_h2_price_per_MWh
        if (storage["type"] == StorageType.ROR or storage["type"] == StorageType.RESERVOIR or
            storage["type"] == StorageType.PUMPED or storage["type"] == StorageType.PUMPED_OPEN):
            # Assume Czech hydro already payed off.
            storage["paid_off_capacity_mw"] = storage["capacity_mw"]

    _modify_coutry_by_RES_prices(czechia, RES_prices)

    if optimize_nuclear:
        return [
            {
                "name": "optimize-nuclear",
                "countries": {CZECHIA: make_nuclear_scenario(
                    czechia,
                    _nuclear_0_mw,
                    _nuclear_7_mw if higher_limits else _nuclear_4_mw,
                    allow_smrs=4)}
            },
        ]
    if fix_nuclear_capacity_mw is not None:
        return [
            {
                "name": "fix-nuclear",
                "countries": {CZECHIA: make_nuclear_scenario(
                    czechia,
                    fix_nuclear_capacity_mw,
                    fix_nuclear_capacity_mw,
                    allow_smrs=4)}
            },
        ]
    return [
        {
            "name": "nuclear-0",
            "countries": {CZECHIA: make_nuclear_scenario(czechia, _nuclear_0_mw, _nuclear_0_mw,
                                                         allow_smrs=0)}
        },
        {
            "name": "nuclear-1",
            "countries": {CZECHIA: make_nuclear_scenario(czechia, _nuclear_1_mw, _nuclear_1_mw,
                                                         allow_smrs=2)}
        },
        {
            "name": "nuclear-2",
            "countries": {CZECHIA: make_nuclear_scenario(czechia, _nuclear_2_mw, _nuclear_2_mw,
                                                         allow_smrs=4)}
        },
        {
            "name": "nuclear-3",
            "countries": {CZECHIA: make_nuclear_scenario(czechia, _nuclear_3_mw, _nuclear_3_mw,
                                                         allow_smrs=4)}
        },
        {
            "name": "nuclear-4",
            "countries": {CZECHIA: make_nuclear_scenario(czechia, _nuclear_4_mw, _nuclear_4_mw,
                                                         allow_smrs=4)}
        },
    ]


def _increase_max_capacities(RES_factor: float,
                             offshore_factor: float,
                             dispatchable_factor: float,
                             allow_extra_smrs: bool,
                             RES_prices: RESPrices,
                             countries: dict[Zone, dict]) -> dict[Zone, dict]:
    for c, country in countries.items():
        for type, params in country["basic_sources"].items():
            if type == BasicSourceType.OFFSHORE:
                params["capacity_mw"] *= offshore_factor
                params["min_capacity_mw"] /= offshore_factor
                # params["min_capacity_mw"] = 0
            elif type != BasicSourceType.NUCLEAR:
                params["capacity_mw"] *= RES_factor
                params["min_capacity_mw"] /= RES_factor
                # params["min_capacity_mw"] = 0

        sum_vre_mw = sum(source["capacity_mw"] for type, source in country["basic_sources"].items()
                         if type != BasicSourceType.NUCLEAR)

        # Make H2 turbine potential flexible. Set it on par with Czechia w.r.t. import.
        for storage in country["storage"]:
            if storage["type"] == StorageType.HYDROGEN or storage["type"] == StorageType.HYDROGEN_PEAK:
                storage["cost_sell_buy_mwh_eur"] = _default_h2_price_per_MWh
                # Allow a lot more energy to get consumed. In practice, electrolysers will stay as
                # there is surplus renewable electricity.
                storage["min_final_energy_mwh"] = 0
                storage["capacity_mw"] *= dispatchable_factor
                storage["min_capacity_mw"] = 0
                storage["min_capacity_mw_charging"] = 0

        _modify_coutry_by_RES_prices(country, RES_prices)

        # Make potential for other dispatchable sources flexible.
        flexible = country["flexible_sources"]
        if FlexibleSourceType.GAS_CCGT_CCS in flexible:
            flexible[FlexibleSourceType.GAS_CCGT_CCS]["capacity_mw"] *= dispatchable_factor
            flexible[FlexibleSourceType.GAS_CCGT_CCS]["min_capacity_mw"] = 0

        if FlexibleSourceType.GAS_CCGT in flexible:
            flexible[FlexibleSourceType.GAS_CCGT]["capacity_mw"] *= dispatchable_factor
            flexible[FlexibleSourceType.GAS_CCGT]["min_capacity_mw"] = 0

        if FlexibleSourceType.GAS_PEAK in flexible:
            flexible[FlexibleSourceType.GAS_PEAK]["capacity_mw"] *= dispatchable_factor
            flexible[FlexibleSourceType.GAS_PEAK]["min_capacity_mw"] = 0
        else:
            # As a heuristic, allow 1/40 of VRE capacity for gas peaking.
            max_gas_peak_mw = sum_vre_mw / 40
            flexible[FlexibleSourceType.GAS_PEAK] = {
                "capacity_mw": max_gas_peak_mw,
                "overnight_costs_per_kw_eur": 470}

        # As a heuristic, allow 1/40 of VRE capacity for SMRs.
        max_smr_mw = sum_vre_mw / 40
        if FlexibleSourceType.SMR in flexible:
            capacity_mw = max(flexible[FlexibleSourceType.SMR]["capacity_mw"] * dispatchable_factor,
                              max_smr_mw)
            flexible[FlexibleSourceType.SMR]["capacity_mw"] = capacity_mw
            flexible[FlexibleSourceType.SMR]["min_capacity_mw"] = 0
            flexible[FlexibleSourceType.SMR]["discount_rate"] = _discount_rate_smr
        elif allow_extra_smrs:
            flexible[FlexibleSourceType.SMR] = {"capacity_mw": max_smr_mw,
                                                "discount_rate": _discount_rate_smr}
    return countries


def construct_grid(ember_loader: EmberNgLoader,
                   ember_scenario: str,
                   target_year: int,
                   RES_max_capacity_factor: float = 1.0,
                   offshore_max_capacity_factor: float = 1.0,
                   dispatchable_max_capacity_factor: float = 1.0,
                   allow_extra_smrs: bool = False,
                   RES_prices: RESPrices = RESPrices.DEFAULT,
                   aggregation_level: Optional[str] = None):
    if aggregation_level == "cz-sk":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                }),
            "interconnectors": ember_loader.get_interconnectors(ember_scenario, target_year),
        }
    elif aggregation_level == "neighbors":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    GERMANY: ember_loader.get_country(ember_scenario, target_year, GERMANY),
                    AUSTRIA: ember_loader.get_country(ember_scenario, target_year, AUSTRIA),
                    POLAND: ember_loader.get_country(ember_scenario, target_year, POLAND,
                                                     profile_overrides={BasicSourceType.NUCLEAR: CZECHIA}),
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                }),
            "interconnectors": ember_loader.get_interconnectors(ember_scenario, target_year),
        }
    elif aggregation_level == "coarse":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    GERMANY: ember_loader.get_country(ember_scenario, target_year, GERMANY),
                    AUSTRIA: ember_loader.get_country(ember_scenario, target_year, AUSTRIA),
                    POLAND: ember_loader.get_country(ember_scenario, target_year, POLAND,
                                                     profile_overrides={BasicSourceType.NUCLEAR: CZECHIA}),
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                } | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, NORDICS,
                    overrides={DENMARK: {BasicSourceType.HYDRO: SWEDEN}})
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, BRITISH_ISLES,
                                                            )
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, WEST,
                                                            overrides={NETHERLANDS: {BasicSourceType.HYDRO: BELGIUM}})
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, IBERIA)
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, SOUTH,
                                                            overrides={CROATIA: {BasicSourceType.OFFSHORE: GREECE}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, BALKANS,
                    overrides={
                        ROMANIA: {BasicSourceType.OFFSHORE: GREECE},
                        BULGARIA: {BasicSourceType.OFFSHORE: GREECE},
                        MONTENEGRO: {BasicSourceType.OFFSHORE: GREECE}})),
            "interconnectors": ember_loader.get_interconnectors(
                ember_scenario, target_year,
                aggregate_countries={BALKANS, BRITISH_ISLES, IBERIA, NORDICS, SOUTH, WEST}),
        }

    elif aggregation_level == "midfine":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    GERMANY: ember_loader.get_country(ember_scenario, target_year, GERMANY),
                    AUSTRIA: ember_loader.get_country(ember_scenario, target_year, AUSTRIA),
                    POLAND: ember_loader.get_country(ember_scenario, target_year, POLAND,
                                                     profile_overrides={BasicSourceType.NUCLEAR: CZECHIA}),
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                    ITALY: ember_loader.get_country(ember_scenario, target_year, ITALY),
                } | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, SCANDINAVIA,
                    overrides={DENMARK: {BasicSourceType.HYDRO: SWEDEN}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, BALTICS)
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, BRITISH_ISLES)
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, BENELUX,
                    overrides={NETHERLANDS: {BasicSourceType.HYDRO: BELGIUM}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, FRANCE_SWITZERLAND)
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, IBERIA)
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, ROMANIA_BULGARIA,
                    overrides={ROMANIA: {BasicSourceType.OFFSHORE: GREECE},
                               BULGARIA: {BasicSourceType.OFFSHORE: GREECE}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, SLOVENIA_CROATIA_HUNGARY,
                    overrides={CROATIA: {BasicSourceType.OFFSHORE: GREECE}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, SOUTH_CENTRAL_BALKAN,
                    overrides={MONTENEGRO: {BasicSourceType.OFFSHORE: GREECE}})),
            "interconnectors": ember_loader.get_interconnectors(
                ember_scenario, target_year,
                aggregate_countries={SCANDINAVIA, BALTICS, BRITISH_ISLES, BENELUX, FRANCE_SWITZERLAND, IBERIA,
                                     ROMANIA_BULGARIA, SLOVENIA_CROATIA_HUNGARY, SOUTH_CENTRAL_BALKAN}),
        }

    elif aggregation_level == "finer":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    GERMANY: ember_loader.get_country(ember_scenario, target_year, GERMANY),
                    AUSTRIA: ember_loader.get_country(ember_scenario, target_year, AUSTRIA),
                    POLAND: ember_loader.get_country(ember_scenario, target_year, POLAND,
                                                     profile_overrides={BasicSourceType.NUCLEAR: CZECHIA}),
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                    DENMARK: ember_loader.get_country(ember_scenario, target_year, DENMARK,
                                                      profile_overrides={BasicSourceType.HYDRO: SWEDEN}),
                    NORWAY: ember_loader.get_country(ember_scenario, target_year, NORWAY),
                    SWEDEN: ember_loader.get_country(ember_scenario, target_year, SWEDEN),
                    FINLAND: ember_loader.get_country(ember_scenario, target_year, FINLAND),
                    LITHUANIA: ember_loader.get_country(ember_scenario, target_year, LITHUANIA),
                    FRANCE: ember_loader.get_country(ember_scenario, target_year, FRANCE),
                    NETHERLANDS: ember_loader.get_country(ember_scenario, target_year, NETHERLANDS,
                                                          profile_overrides={BasicSourceType.HYDRO: BELGIUM}),
                    SWITZERLAND: ember_loader.get_country(ember_scenario, target_year, SWITZERLAND),
                    ITALY: ember_loader.get_country(ember_scenario, target_year, ITALY),
                    HUNGARY: ember_loader.get_country(ember_scenario, target_year, HUNGARY),
                    ROMANIA: ember_loader.get_country(ember_scenario, target_year, ROMANIA,
                                                      profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                    BULGARIA: ember_loader.get_country(ember_scenario, target_year, BULGARIA,
                                                       profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                    GREECE: ember_loader.get_country(ember_scenario, target_year, GREECE),
                } | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, ESTONIA_LATVIA)
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, BRITISH_ISLES)
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, BELUX)
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, IBERIA)
                | ember_loader.get_countries_from_aggregate(ember_scenario, target_year, WEST_BALKAN,
                                                            overrides={CROATIA: {BasicSourceType.OFFSHORE: GREECE}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, SOUTH_BALKAN,
                    overrides={MONTENEGRO: {BasicSourceType.OFFSHORE: GREECE}})
                | ember_loader.get_countries_from_aggregate(
                    ember_scenario, target_year, CENTRAL_BALKAN)),
            "interconnectors": ember_loader.get_interconnectors(
                ember_scenario, target_year,
                aggregate_countries={BELUX, BRITISH_ISLES, CENTRAL_BALKAN, ESTONIA_LATVIA, IBERIA,
                                     SOUTH_BALKAN, WEST_BALKAN}),
        }

    elif aggregation_level == "finest":
        return {
            "countries": _increase_max_capacities(
                RES_max_capacity_factor,
                offshore_max_capacity_factor,
                dispatchable_max_capacity_factor,
                allow_extra_smrs, RES_prices, {
                    GERMANY: ember_loader.get_country(ember_scenario, target_year, GERMANY),
                    AUSTRIA: ember_loader.get_country(ember_scenario, target_year, AUSTRIA),
                    POLAND: ember_loader.get_country(ember_scenario, target_year, POLAND,
                                                     profile_overrides={BasicSourceType.NUCLEAR: CZECHIA}),
                    SLOVAKIA: ember_loader.get_country(ember_scenario, target_year, SLOVAKIA),
                    DENMARK: ember_loader.get_country(ember_scenario, target_year, DENMARK,
                                                      profile_overrides={BasicSourceType.HYDRO: SWEDEN}),
                    NORWAY: ember_loader.get_country(ember_scenario, target_year, NORWAY),
                    SWEDEN: ember_loader.get_country(ember_scenario, target_year, SWEDEN),
                    FINLAND: ember_loader.get_country(ember_scenario, target_year, FINLAND),
                    ESTONIA: ember_loader.get_country(ember_scenario, target_year, ESTONIA),
                    LATVIA: ember_loader.get_country(ember_scenario, target_year, LATVIA),
                    LITHUANIA: ember_loader.get_country(ember_scenario, target_year, LITHUANIA),
                    GREAT_BRITAIN: ember_loader.get_country(ember_scenario, target_year, GREAT_BRITAIN),
                    IRELAND: ember_loader.get_country(ember_scenario, target_year, IRELAND),
                    FRANCE: ember_loader.get_country(ember_scenario, target_year, FRANCE),
                    NETHERLANDS: ember_loader.get_country(ember_scenario, target_year, NETHERLANDS,
                                                          profile_overrides={BasicSourceType.HYDRO: BELGIUM}),
                    LUXEMBOURG: ember_loader.get_country(ember_scenario, target_year, LUXEMBOURG),
                    BELGIUM: ember_loader.get_country(ember_scenario, target_year, BELGIUM),
                    SWITZERLAND: ember_loader.get_country(ember_scenario, target_year, SWITZERLAND),
                    SPAIN: ember_loader.get_country(ember_scenario, target_year, SPAIN),
                    PORTUGAL: ember_loader.get_country(ember_scenario, target_year, PORTUGAL),
                    ITALY: ember_loader.get_country(ember_scenario, target_year, ITALY),
                    SLOVENIA: ember_loader.get_country(ember_scenario, target_year, SLOVENIA),
                    CROATIA: ember_loader.get_country(ember_scenario, target_year, CROATIA,
                                                      profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                    HUNGARY: ember_loader.get_country(ember_scenario, target_year, HUNGARY),
                    GREECE: ember_loader.get_country(ember_scenario, target_year, GREECE),
                    ROMANIA: ember_loader.get_country(ember_scenario, target_year, ROMANIA,
                                                      profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                    BULGARIA: ember_loader.get_country(ember_scenario, target_year, BULGARIA,
                                                       profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                    NORTH_MACEDONIA: ember_loader.get_country(ember_scenario, target_year, NORTH_MACEDONIA),
                    BOSNIA_HERZEGOVINA: ember_loader.get_country(ember_scenario, target_year, BOSNIA_HERZEGOVINA),
                    SERBIA: ember_loader.get_country(ember_scenario, target_year, BOSNIA_HERZEGOVINA),
                    MONTENEGRO: ember_loader.get_country(ember_scenario, target_year, MONTENEGRO,
                                                         profile_overrides={BasicSourceType.OFFSHORE: GREECE}),
                }),
            "interconnectors": ember_loader.get_interconnectors(ember_scenario, target_year),
        }

    raise KeyError(f"Uknown country aggregation level '{aggregation_level}'")
