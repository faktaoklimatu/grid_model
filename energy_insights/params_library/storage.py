"""
Provides a library of capacities and other parameters for grid storage
facilities.

Import this module to make the bundled parameter set available for loading.
"""

from typing import Optional

import pandas

from ..color_map import ColorMap
from ..grid_plot_utils import Keys
from ..region import Region
# TODO: Avoid private access.
from ..sources.storage import StorageType, StorageUse, _storage
from ..sources.economics import usd_to_eur_2022


def _scale_storage(storage: dict, factor: float, min_factor: float = 1):
    return storage | {
        "max_energy_mwh": storage["max_energy_mwh"] * factor,
        "capacity_mw": storage["capacity_mw"] * factor,
        "min_capacity_mw": storage["min_capacity_mw"] * min_factor,
        "capacity_mw_charging": storage["capacity_mw_charging"] * factor,
        "min_capacity_mw_charging": storage["min_capacity_mw_charging"] * min_factor,
    }


def _specify_dsr(capacity_mw: float,
                 max_energy_mwh: Optional[float] = None,
                 max_hours: Optional[float] = None) -> dict[str, float]:
    if max_energy_mwh is None:
        if max_hours is None:
            raise ValueError(
                "At lest one of max_energy_mwh and max_hours must be specified for DSR")
        else:
            max_energy_mwh = capacity_mw * max_hours

    initial_energy_mwh = max_energy_mwh  # Allow to move _down_ by `max_energy_mwh`.

    return {
        "capacity_mw": capacity_mw,
        "capacity_mw_charging": capacity_mw,
        "min_capacity_mw": capacity_mw,  # Take out from capex optimization.
        "min_capacity_mw_charging": capacity_mw,
        "max_energy_mwh": initial_energy_mwh + max_energy_mwh,
        "initial_energy_mwh": initial_energy_mwh,
        "midnight_energy_mwh": initial_energy_mwh
    }


__pumped_charging_efficiency = 0.8
__pumped_discharging_efficiency = 0.9
__pecd_hydro_fill_ratio = 0.75

__cost_for_kg_hydrogen_eur = 2
# LHV of H2 of 33.33 kWh/kg gives 30 kg needed for 1 MWh.
__cost_for_mwh_hydrogen_eur = __cost_for_kg_hydrogen_eur * 30

# TODO: Move defaults to the `sources.storage` module and refactor
# so that storage is specified using dictionaries, akin to basic and
# flexible soures.
__pumped_hydro = {
    "type": StorageType.PUMPED,
    "renewable": True,
    "charging_efficiency": __pumped_charging_efficiency,
    "discharging_efficiency": __pumped_discharging_efficiency,
    "color": ColorMap.PUMPED_HYDRO,
    "construction_time_years": 5,
    "lifetime_years": 80,
    "fixed_o_m_costs_per_kw_eur": 15,
    "overnight_costs_per_kw_eur": 2000,
    # Variable O&M: 4, price of input electricity is counted elsewhere.
    "variable_costs_per_mwh_eur": 4,
}

__pumped_hydro_closed_pecd = __pumped_hydro | {
    # Put all losses into charging efficiency so that storage capacity and inflow data is consistent
    # with the PECD database (that has all energy scaled to output power).
    "charging_efficiency": __pumped_charging_efficiency * __pumped_discharging_efficiency,
    "discharging_efficiency": 1,
}

__pumped_hydro_open_pecd = __pumped_hydro_closed_pecd | {
    "type": StorageType.PUMPED_OPEN,
    "inflow_hourly_data_key": Keys.HYDRO_INFLOW_PUMPED_OPEN,
}

__hydro_pecd = __pumped_hydro_closed_pecd | {
    "use": StorageUse.ELECTRICITY_AS_BASIC,
    "color": ColorMap.HYDRO,
    # No charging.
    "capacity_mw_charging": 0,
    "min_capacity_mw_charging": 0,
}

__reservoir_pecd = __hydro_pecd | {
    "type": StorageType.RESERVOIR,
    "inflow_hourly_data_key": Keys.HYDRO_INFLOW_RESERVOIR,
    # Set the variable costs only one EUR lower than pumped in order
    # to distinguish it in the optimization.
    "variable_costs_per_mwh_eur": 3,
}

__ror_pecd = __hydro_pecd | {
    "type": StorageType.ROR,
    "inflow_hourly_data_key": Keys.HYDRO_INFLOW_ROR,
    "max_energy_mwh": 0,
    # Tiny variable costs for run-of-river.
    "variable_costs_per_mwh_eur": 2,
}


__old_pumped_hydro = __pumped_hydro | {
    # Consider only 1/10th of investment costs.
    "overnight_costs_per_kw_eur": 200,
}

# Taken from PEMMDB (TY2024).
__cz_existing_pumped_hydro = __old_pumped_hydro | {
    "max_energy_mwh": 6_000 / __pumped_discharging_efficiency,
    "capacity_mw": 1156,
    "min_capacity_mw": 1156,
    "capacity_mw_charging": 1102,
    "min_capacity_mw_charging": 1102,
}

# Taken from PEMMDB (TY2024).
__pl_existing_pumped_hydro = __old_pumped_hydro | {
    "max_energy_mwh": 7_590 / __pumped_discharging_efficiency,
    "capacity_mw": 1550,
    "min_capacity_mw": 1550,
    "capacity_mw_charging": 1658,
    "min_capacity_mw_charging": 1658,
}

# Capacity taken from PEMMDB (TY2024).
# Installed power taken from https://www.hydropower.org/country-profiles/austria.
__at_existing_pumped_hydro = __old_pumped_hydro | {
    "max_energy_mwh": 1_732_300 / __pumped_discharging_efficiency,
    "capacity_mw": 5_596,
    "min_capacity_mw": 5_596,
    "capacity_mw_charging": 5_596,
    "min_capacity_mw_charging": 5_596,
}

# Capacity taken from PEMMDB (TY2024).
__de_existing_pumped_hydro = __old_pumped_hydro | {
    "max_energy_mwh": 1_046_600 / __pumped_discharging_efficiency,
    "capacity_mw": 8_094,
    "min_capacity_mw": 8_094,
    "capacity_mw_charging": 7_963,
    "min_capacity_mw_charging": 7_963,
}

# Capacity taken from PEMMDB (TY2024).
__sk_existing_pumped_hydro = __old_pumped_hydro | {
    "max_energy_mwh": 49_647 / __pumped_discharging_efficiency,
    "capacity_mw": 926,
    "min_capacity_mw": 926,
    "capacity_mw_charging": 828,
    "min_capacity_mw_charging": 828,
}

__grid_lion_battery = {
    "type": StorageType.LI,
    "charging_efficiency": .95,
    "discharging_efficiency": .95,
    "color": ColorMap.BATTERY,
    "construction_time_years": 1,
    "lifetime_years": 15,
    "overnight_costs_per_kw_eur": 500,
    "fixed_o_m_costs_per_kw_eur": 8,
    # Variable O&M: 2, price of input electricity is counted elsewhere.
    "variable_costs_per_mwh_eur": 2,
    # Be careful to use this as it drastically changes optimization
    # -- moving most costs from capex to opex and thus becomes hesitant
    # to use those batteries.
    # "lifetime_cycles": 3000,
    "initial_energy_ratio": 1.0,
}

__grid_lion_battery_4hrs = __grid_lion_battery | {
    "type": StorageType.LI_4H,
    "max_energy_hours": 4,
    "overnight_costs_per_kw_eur": 700,
}

__cheap_grid_lion_battery_4hrs = __grid_lion_battery_4hrs | {
    "overnight_costs_per_kw_eur": 100,
}

__grid_lion_battery_2hrs = __grid_lion_battery | {
    "type": StorageType.LI_2H,
    "max_energy_hours": 2,
    "overnight_costs_per_kw_eur": 500,
}

__grid_lion_battery_2hrs_2030 = __grid_lion_battery_2hrs | {
    "overnight_costs_per_kw_eur": 400,
}

__vehicle_to_grid_charging_11kW: float = 11
__vehicle_to_grid_charging_3kW: float = 3
__vehicle_to_grid_capacity_kWh: float = 50
# Assuming only third of the capacity is available for V2G and smart charging.
__vehicle_to_grid_available_capacity_kWh: float = __vehicle_to_grid_capacity_kWh / 3
__smart_charging_available_capacity_kWh: float = __vehicle_to_grid_capacity_kWh / 3
# Assuming only fourth of the fleet is available for V2G and smart charging at a given moment.
# TODO: Replace by smarter ratios varying throughout each day.
__vehicle_to_grid_availability_ratio: float = 0.25
__smart_availability_ratio: float = 0.25

__vehicle_to_grid = __grid_lion_battery | {
    # Considering an externality to the grid, all costs should be included in variable costs.
    "overnight_costs_per_kw_eur": 0,
    # Guesstimate, car owners will ask for remuneration (on top of spot difference).
    "variable_costs_per_mwh_eur": 10,
}

__vehicle_to_grid_50kWh_11kW = __vehicle_to_grid | {
    "type": StorageType.VEHICLE_TO_GRID_50KWH_11KW,
    "max_energy_hours": __vehicle_to_grid_available_capacity_kWh / __vehicle_to_grid_charging_11kW,
}

__vehicle_to_grid_50kWh_3kW = __vehicle_to_grid | {
    "type": StorageType.VEHICLE_TO_GRID_50KWH_3KW,
    "max_energy_hours": __vehicle_to_grid_available_capacity_kWh / __vehicle_to_grid_charging_3kW,
}

__smart_charging_50kWh_3kW = __grid_lion_battery | {
    "use": StorageUse.DEMAND_FLEXIBILITY,
    "type": StorageType.SMART_CHARGING_50KWH_3KW,
    # Considering an externality to the grid, motivated only by spot price differences.
    "overnight_costs_per_kw_eur": 0,
}

__electrolysis = {
    "construction_time_years": 1,
    "lifetime_years": 25,
    "fixed_o_m_costs_per_kw_eur": 15,

    # Mid of the 2022 cost range, based on IEA's World Energy Outlook 2023 for Europe.
    # https://iea.blob.core.windows.net/assets/2b0ded44-6a47-495b-96d9-2fac0ac735a8/WorldEnergyOutlook2023.pdf
    "overnight_costs_per_kw_eur": usd_to_eur_2022(1355),
    # Variable O&M: 4, price of input electricity is counted elsewhere.
    "variable_costs_per_mwh_eur": 4,
    # Be careful to use this as it drastically changes optimization -- moving most costs from capex to opex and thus becomes hesitant to use those batteries.
    # "lifetime_hours": 50_000,
}

__electrolysis_iea_2030 = __electrolysis | {
    # Mid of the 2030 cost estimate, based on IEA's World Energy Outlook 2023 for the Announced Pledges scenario.
    # https://iea.blob.core.windows.net/assets/2b0ded44-6a47-495b-96d9-2fac0ac735a8/WorldEnergyOutlook2023.pdf
    "overnight_costs_per_kw_eur": usd_to_eur_2022(625),
    "fixed_o_m_costs_per_kw_eur": 13,
}

__grid_hydrogen_storage = {  # With H2 burnt in CCGT plants.
    "type": StorageType.HYDROGEN,
    "charging_efficiency": .65,
    "discharging_efficiency": .55,
    "color": ColorMap.HYDROGEN,
    # Cost profile of discharging (=H2 burning in a H2-ready CCGT plant).
    "construction_time_years": 3,
    "lifetime_years": 30,
    "fixed_o_m_costs_per_kw_eur": 15,
    "overnight_costs_per_kw_eur": 1500,
    # Variable O&M: 4.
    "variable_costs_per_mwh_eur": 4,
    # Cost profile for charging (=independent devices for electrolysis)
    "separate_charging": __electrolysis,
    # Make de-facto unbounded storage capacity (to have enough H2 for the start of the year).
    "max_energy_mwh": 200_000_000,
    "initial_energy_mwh": 100_000_000,
    "final_energy_mwh": 100_000_000,
    "cost_sell_buy_mwh_eur": __cost_for_mwh_hydrogen_eur,
}

__grid_hydrogen_storage_iea_2030 = __grid_hydrogen_storage | {
    "charging_efficiency": .74,
    "discharging_efficiency": .56,
    # No price indexes exist whatsoever for 100% H2 turbines. Taking slightly more than for CH4
    # combined cycle turbines.
    "overnight_costs_per_kw_eur": 1100,
    "separate_charging": __electrolysis_iea_2030,
    # Consumption elsewhere is exogenous and out-of-optimization, so don't reward for selling more.
    "cost_sell_buy_mwh_eur": 0,
}

__grid_hydrogen_storage_ocgt_iea_2020 = __grid_hydrogen_storage | {  # With H2 burnt in OCGT plants.
    "type": StorageType.HYDROGEN_PEAK,
    # No price indexes exist whatsoever for 100% H2 turbines. Adding the same premium (of 100 EUR)
    # to OCGT turbines as for H2 CCGT.
    "overnight_costs_per_kw_eur": 570,
    "discharging_efficiency": .4,
}

__cheap_electrolysis = __electrolysis | {
    "overnight_costs_per_kw_eur": 500,
}

__cheap_grid_hydrogen_storage = __grid_hydrogen_storage | {
    "separate_charging": __cheap_electrolysis
}

__demand_flexibility = {
    "type": StorageType.DSR,
    "color": "black",  # Irrelevant.
    "use": StorageUse.DEMAND_FLEXIBILITY,
    "charging_efficiency": 1,
    "discharging_efficiency": 1,
    # Artificially apply a ramp rate to avoid demand curve jumping too much.
    "ramp_rate": 0.2,
    # No costs, assuming natural electricity cost optimization on consumer side.
    "variable_costs_per_mwh_eur": 0,
    "overnight_costs_per_kw_eur": 0,
    "fixed_o_m_costs_per_kw_eur": 0,
}

__heat_distribution = {
    "type": StorageType.HEAT,
    "color": ColorMap.HEAT_DISTRIBUTION,
    "use": StorageUse.HEAT,
    "charging_efficiency": 0.99,  # Less than 1 to avoid simultaneous charging-discharging.
    "discharging_efficiency": 1,
    "loss_rate_per_day": 0.77,  # Loses 6% per hour, i.e. ~77% per day.
    # TODO: Add cost estimates.
    "variable_costs_per_mwh_eur": 0,
    "overnight_costs_per_kw_eur": 0,
    "fixed_o_m_costs_per_kw_eur": 0,
}

# Rather an over-estimate to make sure we don't over-estimate smart-charging / vehicle-to-grid
# potential. This assumes BEV are slightly more expensive and thus there's a slightly higher
# utilization compared to ICE.
__ember_average_vehicle_km_per_year = 25_000
__ember_average_vehicle_kwh_per_km = 0.18

__demand_flexibility_cz_2030_basic = __demand_flexibility | _specify_dsr(
    capacity_mw=200, max_energy_mwh=200*8)
__demand_flexibility_cz_2030_advanced = __demand_flexibility | _specify_dsr(
    capacity_mw=400, max_energy_mwh=400*8)

# Order of storage types in each scenario does not have to be alphabetical. It determines their
# order in the supplementary graphs and thus can follow the logic of the model (e.g. H2-centric
# modelling can deliberately put H2 first whereas lion-centric modelling will put lion first).
_storage.update({
    # Czechia.
    "cz-current": [
        __cz_existing_pumped_hydro,
        __heat_distribution | {"nominal_mw": 5_000,
                               "max_energy_mwh": 25_000},
    ],
    "cz-2030-advanced": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_2hrs | {"nominal_mw": 2000},
        __grid_hydrogen_storage | {"capacity_mw": 125,
                                   "capacity_mw_charging": 500},
        __demand_flexibility_cz_2030_advanced,
    ],
    "cz-2030-advanced-forced": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_2hrs | {"nominal_mw": 2000, "min_nominal_mw": 2000},
        __grid_hydrogen_storage | {"capacity_mw": 125, "min_capacity_mw": 125,
                                   "capacity_mw_charging": 500, "min_capacity_mw_charging": 500},
        __demand_flexibility_cz_2030_advanced,
    ],
    "cz-2030-basic": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_2hrs | {"nominal_mw": 1000},
        __grid_hydrogen_storage | {"capacity_mw": 25,
                                   "capacity_mw_charging": 100},
        __demand_flexibility_cz_2030_basic,
    ],
    "cz-2030-potential": [
        # Assuming simple conversions of some existing hydro plants.
        _scale_storage(__cz_existing_pumped_hydro, 1.2),
        __grid_lion_battery_4hrs | {"nominal_mw": 2000},
        __grid_hydrogen_storage | {"capacity_mw": 2000,
                                   "capacity_mw_charging": 4000},
    ],
    "cz-2050-nuclear": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 4_000},
    ],
    "cz-2050-hydrogen": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 10_000},
        __grid_hydrogen_storage | {"capacity_mw": 6_000,
                                   "capacity_mw_charging": 12_000},
    ],
    "cz-2050-import": [
        __cz_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 6_000},
    ],
    "cz-2050-import-de": [
        # German storage facilities for an artificial synthetic Czech scenario.
        __grid_hydrogen_storage | {"capacity_mw": 1_000,
                                   "capacity_mw_charging": 2_000},
    ],
    # Germany.
    "de-current": [__de_existing_pumped_hydro],
    "de-2030": [
        # Assuming https://tyndp2022-project-platform.azurewebsites.net/projectsheets/storage/1048
        # and https://tyndp2022-project-platform.azurewebsites.net/projectsheets/storage/1026.
        _scale_storage(__de_existing_pumped_hydro, 1.2),
        __grid_lion_battery_4hrs | {"nominal_mw": 10_000},
        __grid_hydrogen_storage | {"capacity_mw": 2_500,
                                   # https://www.iea.org/policies/11561-package-for-the-future-hydrogen-strategy
                                   "capacity_mw_charging": 5_000},
        __demand_flexibility | _specify_dsr(capacity_mw=3000, max_energy_mwh=3000*8)
    ],
    "de-2030-potential": [
        # Assuming https://tyndp2022-project-platform.azurewebsites.net/projectsheets/storage/1048
        # and https://tyndp2022-project-platform.azurewebsites.net/projectsheets/storage/1026.
        _scale_storage(__de_existing_pumped_hydro, 1.3),
        __grid_lion_battery_4hrs | {"nominal_mw": 10_000},
        __grid_hydrogen_storage | {"capacity_mw": 5_000,
                                   # https://www.iea.org/policies/11561-package-for-the-future-hydrogen-strategy
                                   "capacity_mw_charging": 10_000},
        __demand_flexibility | _specify_dsr(capacity_mw=3000, max_energy_mwh=3000*8)
    ],
    # Poland.
    "pl-current": [__pl_existing_pumped_hydro],
    "pl-2030": [
        __pl_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 1_000},
    ],
    "pl-2030-potential": [
        __pl_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 2_000},
        __grid_hydrogen_storage | {"capacity_mw": 2000,
                                   "capacity_mw_charging": 4000},
    ],
    # Austria.
    "at-current": [__at_existing_pumped_hydro],
    "at-2030": [
        # Only minor upgrades, no large pump hydro projects in TYNDYP until 2030.
        _scale_storage(__at_existing_pumped_hydro, 1.05),
        __grid_lion_battery_4hrs | {"nominal_mw": 1_000},
    ],
    "at-2030-potential": [
        _scale_storage(__at_existing_pumped_hydro, 1.1),
        __grid_lion_battery_4hrs | {"nominal_mw": 2_000},
        __grid_hydrogen_storage | {"capacity_mw": 3000,
                                   "capacity_mw_charging": 6000},
    ],
    # Slovakia.
    "sk-current": [__sk_existing_pumped_hydro],
    "sk-2030-potential": [
        __sk_existing_pumped_hydro,
        __grid_lion_battery_4hrs | {"nominal_mw": 1_000},
    ],
})


def _ember_ng_load_hydrogen(df: pandas.DataFrame, allow_capex_optimization: bool) -> list[dict]:
    matched_generation = df[(df["KPI"] == "Installed capacities - power generation") &
                            (df["Technology"] == "Hydrogen fleet")].head(1)
    matched_charging = df[(df["KPI"] == "Installed capacities - electrolysers") &
                          (df["Technology"] == "Electrolysis")].head(1)

    capacity_mw_generation: float = 0
    capacity_mw_charging: float = 0

    if not matched_generation.empty:
        capacity_mw_generation = 1000 * matched_generation["Result"].item()
    if not matched_charging.empty:
        capacity_mw_charging = 1000 * matched_charging["Result"].item()

    # Ignore tiny capacities below 100 kW.
    if capacity_mw_generation < .1 and capacity_mw_charging < .1:
        return

    params = {
        "capacity_mw": capacity_mw_generation,
        "capacity_mw_charging": capacity_mw_charging,
        "min_capacity_mw": 0 if allow_capex_optimization else capacity_mw_generation,
        "min_capacity_mw_charging": 0 if allow_capex_optimization else capacity_mw_charging,
    }

    # Add H2 OCGT as an alternative (with the same max capacity, capex optimization will sort it out),
    # without eletrolysers (market buy/sell price effectively allows transfers of H2 between the two).
    list = [__grid_hydrogen_storage_iea_2030 | params,
            __grid_hydrogen_storage_ocgt_iea_2020 | params | {"capacity_mw_charging": 0,
                                                              "min_capacity_mw_charging": 0}]
    return list


def _ember_ng_load_lion(df: pandas.DataFrame, allow_capex_optimization: bool) -> Optional[dict]:
    matched = df[(df["KPI"] == "Installed capacities - power generation") &
                 (df["Technology"] == "Lithium ion battery fleet")].head(1)
    if matched.empty:
        return

    capacity_mw: float = 1000 * matched["Result"].item()
    # Ignore tiny capacities below 100 kW.
    if capacity_mw < .1:
        return

    # Assume two hours of battery storage.
    return __grid_lion_battery_2hrs_2030 | {
        # `nominal_mw` gets expanded to `capacity_mw` and
        # `capacity_mw_charging` with the same value.
        "min_nominal_mw": 0 if allow_capex_optimization else capacity_mw,
        "nominal_mw": capacity_mw,
    }

def ember_ng_get_vehicle_types_consumption_gwh(df: pandas.DataFrame) -> tuple[float, float, float]:
    """
    Returns consumption of different vehicle types based on the EmberNG dataset. For the purpose of
    this study, electric vehicle consumption is assumed to be divided in 3 disjoint categories:
     - vehicle-to-grid (cars allowing to get discharged remotely)
     - smart-charging (cars allowing flexibility when to charge, without allowing to discharge)
     - other electric vehicles (non-flexible consumption).

    Arguments:
        df: Pandas data frame of the Ember New Generation dataset, restricted to a specific
            scenario, country and trajectory year.

    Returns:
        Electricity consumption (in GWh) of electric vehicles, split into 3 categories:
        - vehicle-to-grid consumption,
        - smart charging consumption,
        - other BEV consumption.
    """
    matched_all_demand = df[(df["KPI"] == "Power demand") &
                            (df["Technology"] == "Electric Vehicles")].head(1)
    matched_consumption = df[(df["KPI"] == "Electricity consumption (by storage assets)") &
                             (df["Technology"] == "Electric Vehicles (V2G)")].head(1)
    matched_production = df[(df["KPI"] == "Electricity production (by storage assets)") &
                            (df["Technology"] == "Electric Vehicles (V2G)")].head(1)
    if matched_all_demand.empty or matched_production.empty or matched_consumption.empty:
        return 0, 0, 0

    all_vehicle_consumption_gwh = matched_all_demand["Result"].item()
    vehicle_to_grid_consumption_gwh = \
        (matched_consumption["Result"].item() - matched_production["Result"].item())
    non_v2g_consumption_gwh = all_vehicle_consumption_gwh - vehicle_to_grid_consumption_gwh
    smart_charging_consumption_gwh = non_v2g_consumption_gwh / 2
    other_bev_consumption_gwh = non_v2g_consumption_gwh - smart_charging_consumption_gwh
    return vehicle_to_grid_consumption_gwh, smart_charging_consumption_gwh, other_bev_consumption_gwh


def _ember_ng_load_cars(df: pandas.DataFrame) -> list[dict]:
    matched_all_demand = df[(df["KPI"] == "Power demand") &
                            (df["Technology"] == "Electric Vehicles")].head(1)
    matched_consumption = df[(df["KPI"] == "Electricity consumption (by storage assets)") &
                             (df["Technology"] == "Electric Vehicles (V2G)")].head(1)
    matched_production = df[(df["KPI"] == "Electricity production (by storage assets)") &
                            (df["Technology"] == "Electric Vehicles (V2G)")].head(1)
    if matched_all_demand.empty or matched_production.empty or matched_consumption.empty:
        return []

    vehicle_to_grid_consumption_gwh, smart_charging_consumption_gwh, other_bev_consumption_gwh = \
        ember_ng_get_vehicle_types_consumption_gwh(df)
    all_vehicle_consumption_gwh = (vehicle_to_grid_consumption_gwh +
                                   smart_charging_consumption_gwh + other_bev_consumption_gwh)

    ratio_of_vehicle_to_grid = vehicle_to_grid_consumption_gwh / all_vehicle_consumption_gwh
    ratio_of_smart_charging = smart_charging_consumption_gwh / all_vehicle_consumption_gwh

    estimate_total_km = (all_vehicle_consumption_gwh * 1e6) / __ember_average_vehicle_kwh_per_km
    estimate_total_vehicles = estimate_total_km / __ember_average_vehicle_km_per_year

    estimate_connected_v2g_vehicles = estimate_total_vehicles * (ratio_of_vehicle_to_grid *
                                                                 __vehicle_to_grid_availability_ratio)
    estimate_total_smart_vehicles = estimate_total_vehicles * ratio_of_smart_charging
    estimate_connected_smart_vehicles = estimate_total_smart_vehicles * __smart_availability_ratio

    capacity_v2g_mw = estimate_connected_v2g_vehicles * __vehicle_to_grid_charging_3kW / 1000
    capacity_smart_mw = estimate_connected_smart_vehicles * __vehicle_to_grid_charging_3kW / 1000

    smart_vehicle_mwh = (ratio_of_smart_charging * all_vehicle_consumption_gwh) * 1000
    smart_vehicle_daily_mwh = smart_vehicle_mwh / 365

    cars = [
        __vehicle_to_grid_50kWh_3kW | {
            "min_nominal_mw": capacity_v2g_mw,
            "nominal_mw": capacity_v2g_mw,
        }
    ]

    if smart_charging_consumption_gwh > 0:
        max_energy_mwh = estimate_total_smart_vehicles * 0.05
        cars.append(__smart_charging_50kWh_3kW | {
            "capacity_mw": 0,
            "capacity_mw_charging": capacity_smart_mw,
            "min_capacity_mw_charging": capacity_smart_mw,
            "max_energy_mwh": max_energy_mwh,
            "use_mwh_per_day": smart_vehicle_daily_mwh,
            # Start charged.
            "initial_energy_mwh": max_energy_mwh,
            # Artificially apply a ramp rate to avoid demand curve jumping too much. This roughly
            # corresponds to half of the average charging.
            "ramp_rate": 0.2
        })

    return cars


def _ember_ng_load_heat_pump_flexibility(df: pandas.DataFrame) -> Optional[dict]:
    matched_all_demand = df[(df["KPI"] == "Power demand") &
                            (df["Technology"] == "Heat Pump")].head(1)
    heat_pump_consumption_mwh = matched_all_demand["Result"].item() * 1000
    heat_pump_consumption_average_daily_mwh = heat_pump_consumption_mwh / 365

    # Assume the storage capacity to be the daily average
    # (note that yearly average day is much lower than the peak day).
    max_energy_mwh = heat_pump_consumption_average_daily_mwh
    # Charge / discharge capacity gets reasonably limited by
    # "max_capacity_mw_hourly_data_key", here we specify no bounds (by allowing to fully
    # charge or discharge in one hour).
    flexible_capacity_mw = max_energy_mwh

    if flexible_capacity_mw < .1:
        return

    return __demand_flexibility | {
        "type": StorageType.HEAT_FLEX,
        "charging_efficiency": 0.95,  # Less than 1 to avoid simultaneous charging-discharging.
        "discharging_efficiency": 1,
        "max_capacity_mw_hourly_data_key": Keys.LOAD_HEAT_PUMPS,
        "max_capacity_mw_factor": 0.5,
        "capacity_mw": flexible_capacity_mw,
        "capacity_mw_charging": flexible_capacity_mw,
        "min_capacity_mw": flexible_capacity_mw,  # Take out from capex optimization.
        "min_capacity_mw_charging": flexible_capacity_mw,
        "max_energy_mwh": max_energy_mwh + max_energy_mwh,
        "initial_energy_mwh": max_energy_mwh,
        "midnight_energy_mwh": max_energy_mwh,
        # Artificially apply a ramp rate to avoid demand curve jumping too much.
        "ramp_rate": 0.2,
    }


def _ember_ng_load_pumped(df: pandas.DataFrame) -> Optional[dict]:
    matched_capacity = df[(df["KPI"] == "Installed capacities - power generation") &
                          (df["Technology"] == "Pumped storage fleet")].head(1)
    matched_energy = df[(df["KPI"] == "Storage capacity (batteries, PHS and V2G)") &
                        (df["Technology"] == "Pumped storage fleet")].head(1)
    if matched_capacity.empty or matched_energy.empty:
        return

    capacity_mw: float = 1000 * matched_capacity["Result"].item()
    max_energy_mwh: float = 1000 * matched_energy["Result"].item()
    # Ignore tiny capacities below 100 kW.
    if capacity_mw < .1 or max_energy_mwh < .1:
        return

    return __pumped_hydro | {
        "max_energy_mwh": max_energy_mwh,
        # `nominal_mw` gets expanded to `capacity_mw` and `capacity_mw_charging` with
        # the same value.
        "min_nominal_mw": capacity_mw,
        "nominal_mw": capacity_mw,
    }


def load_storage_from_ember_ng(df: pandas.DataFrame,
                               scenario: str,
                               year: int,
                               country: Region,
                               allow_capex_optimization: bool,
                               load_hydro: bool) -> list[dict]:
    """
    Load grid storage capacities from Ember New Generation [1] raw data
    file.

    Arguments:
        df: Pandas data frame of the Ember New Generation dataset.
        scenario: Name of scenario to load.
        year: Target year for storage capacities. Available years
            range between 2020 and 2050 in 5-year increments.
        country: Code of country whose storage paramters should be
            loaded.

    Returns:
        Collection of dictionaries specifying the requested storage
        capacities from Ember NG.

    [1]: https://ember-climate.org/insights/research/new-generation/
    """
    # Validate arguments.
    if country not in df["Country"].unique():
        raise ValueError(f"Country ‘{country}’ not available in Ember New Generation dataset")
    if scenario not in df["Scenario"].unique():
        raise ValueError(f"Invalid Ember New Generation scenario ‘{scenario}’")
    if year not in df["Trajectory year"].unique():
        raise ValueError(f"Invalid Ember New Generation target year ‘{year}’")

    df = df[(df["Scenario"] == scenario) &
            (df["Trajectory year"] == year) &
            (df["Country"] == country)]

    storage_list: list[dict] = []

    # Load pumped hydro parameters.
    pumped = _ember_ng_load_pumped(df) if load_hydro else None
    if pumped:
        storage_list.append(pumped)

    # Load Li-ion batteries parameters.
    lion = _ember_ng_load_lion(df, allow_capex_optimization)
    if lion:
        storage_list.append(lion)

    # Load parameters for cars (vehicle to grid and smart charging).
    cars = _ember_ng_load_cars(df)
    if cars:
        storage_list.extend(cars)

    # Estimate heat pump flexibility.
    heat_pump_flex = _ember_ng_load_heat_pump_flexibility(df)
    if heat_pump_flex:
        storage_list.append(heat_pump_flex)

    # Load electrolysis and hydrogen storage parameters.
    hydrogen = _ember_ng_load_hydrogen(df, allow_capex_optimization)
    if hydrogen:
        storage_list.extend(hydrogen)

    return storage_list


def _pecd_load_pumped(df: pandas.DataFrame) -> list[dict]:
    def _make_storage_dict(df: pandas.DataFrame) -> Optional[dict]:
        max_energy_mwh = df[df["variable"] == "sto_GWh"]["value"].sum() * 1000
        capacity_mw = df[df["variable"] == "gen_cap_MW"]["value"].sum()
        capacity_mw_charging = df[df["variable"] == "pumping_cap_MW"]["value"].sum() * -1
        if max_energy_mwh == 0:
            return

        return {
            "max_energy_mwh": max_energy_mwh,
            "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
            "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
            "capacity_mw": capacity_mw,
            "min_capacity_mw": capacity_mw,
            "capacity_mw_charging": capacity_mw_charging,
            "min_capacity_mw_charging": capacity_mw_charging,
        }

    storages: list[dict] = []

    # Load open- and closed-loop pumped hydro separately as closed-loop
    # has no natural inflows.
    if pumped_open := _make_storage_dict(df[df["technology"] == "pumped_open"]):
        storages.append(__pumped_hydro_open_pecd | pumped_open)
    if pumped_closed := _make_storage_dict(df[df["technology"] == "pumped_closed"]):
        storages.append(__pumped_hydro_closed_pecd | pumped_closed)

    return storages


def _pecd_load_reservoir(df: pandas.DataFrame) -> Optional[dict]:
    df = df[df["technology"] == "reservoir"]

    max_energy_mwh = df[df["variable"] == "sto_GWh"]["value"].sum() * 1000
    capacity_mw = df[df["variable"] == "gen_cap_MW"]["value"].sum()
    if max_energy_mwh == 0:
        return

    return __reservoir_pecd | {
        "inflow_min_discharge_ratio": 0.4,
        "max_energy_mwh": max_energy_mwh,
        "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "capacity_mw": capacity_mw,
        "min_capacity_mw": capacity_mw,
    }


def _pecd_load_ror(df: pandas.DataFrame) -> Optional[dict]:
    max_energy_mwh = df["sto_GWh"].sum() * 1000
    capacity_mw = df["cap_MW"].sum()
    if capacity_mw == 0:
        return

    return __ror_pecd | {
        "inflow_min_discharge_ratio": 0.4,
        "max_energy_mwh": max_energy_mwh,
        "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "capacity_mw": capacity_mw,
        "min_capacity_mw": capacity_mw,
    }


def load_hydro_storage_from_pecd(df_reservoir: pandas.DataFrame,
                                 df_ror: pandas.DataFrame,
                                 country: Region) -> list[dict]:
    df_reservoir = df_reservoir[df_reservoir["country"] == country]
    df_ror = df_ror[df_ror["country"] == country]

    storage_list: list[dict] = []

    if ror := _pecd_load_ror(df_ror):
        storage_list.append(ror)

    if pumped := _pecd_load_pumped(df_reservoir):
        storage_list.extend(pumped)

    if reservoir := _pecd_load_reservoir(df_reservoir):
        storage_list.append(reservoir)

    return storage_list
