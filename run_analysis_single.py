#!/usr/bin/env python
import argparse
import sys
from copy import deepcopy
from pathlib import Path

from energy_insights.sources.basic_source import BasicSourceType
from energy_insights.execution_utils import optimize_runs
from energy_insights.hourly_data_extrapolator import HourlyDataExtrapolator
from energy_insights.loaders.ember_ng import EmberNgLoader
from energy_insights.region import CZECHIA, GERMANY
from energy_insights.scenarios.czech_nuclear import (
    construct_grid, make_nuclear_scenarios, make_original_scenarios, RESPrices
)
from energy_insights.sources.flexible_source import FlexibleSourceType
from energy_insights.sources.storage import StorageType


load_hydro_from_pecd = True
load_demand_from_pecd = True

__hydrogen_kg_per_mwh = 30


def make_nuclear_run(ember_loader: EmberNgLoader, args: argparse.Namespace) -> dict:
    ember_scenario = args.CONTEXT_SCENARIO
    short_name: str = ember_scenario.lower().replace(" ", "-")
    analysis_name = (
        f"{args.name_prefix}-{short_name}" if args.name_prefix else f"ember-{short_name}"
    )

    RES_prices: RESPrices = RESPrices.DEFAULT
    if args.lower_res_prices:
        RES_prices = RESPrices.LOWER
    elif args.higher_res_prices:
        RES_prices = RESPrices.HIGHER

    if args.calibration:
        # Assume for the whole grid the same year (2050).
        context_grid = construct_grid(ember_loader, ember_scenario, args.CZ_YEAR,
                                      RES_max_capacity_factor=args.RES_max_capacity_factor,
                                      offshore_max_capacity_factor=args.offshore_max_capacity_factor,
                                      dispatchable_max_capacity_factor=args.dispatchable_max_capacity_factor,
                                      allow_extra_smrs=args.allow_extra_smrs,
                                      RES_prices=RES_prices,
                                      aggregation_level=args.aggregation_level)
        scenarios = make_original_scenarios(ember_loader, ember_scenario, args.CZ_YEAR)
    else:
        context_grid = construct_grid(ember_loader, ember_scenario, args.CONTEXT_YEAR,
                                      RES_max_capacity_factor=args.RES_max_capacity_factor,
                                      offshore_max_capacity_factor=args.offshore_max_capacity_factor,
                                      dispatchable_max_capacity_factor=args.dispatchable_max_capacity_factor,
                                      allow_extra_smrs=args.allow_extra_smrs,
                                      RES_prices=RES_prices,
                                      aggregation_level=args.aggregation_level)
        scenarios = (
            make_nuclear_scenarios(ember_loader, ember_scenario, args.CZ_YEAR, RES_prices,
                                   optimize_nuclear=args.optimize_nuclear_capacity,
                                   fix_nuclear_capacity_mw=args.fix_nuclear_capacity_mw,
                                   higher_limits=args.higher_limits)
        )

    # Parameters that modify all existing scenarios.
    for scenario in scenarios:
        czechia = scenario["countries"][CZECHIA]
        if args.nuclear_wacc:
            # Modified discount rate for conventional nuclear (for simplicity, keep SMRs untouched).
            discount_rate = float(args.nuclear_wacc)
            czechia["basic_sources"][BasicSourceType.NUCLEAR]["discount_rate"] = discount_rate
        if args.onshore_capacity:
            onshore_mw = float(args.onshore_capacity)
            czechia["basic_sources"][BasicSourceType.ONSHORE]["capacity_mw"] = onshore_mw
        if args.demand_factor:
            demand_factor = float(args.demand_factor)
            czechia["load_factors"]["load_base"] *= demand_factor
        if args.smr_capacity:
            smr_capacity = float(args.smr_capacity)
            czechia["flexible_sources"][FlexibleSourceType.SMR]["capacity_mw"] = smr_capacity
        if args.smr_capex:
            smr_capex = float(args.smr_capex)
            for country in scenario["countries"].values():
                if FlexibleSourceType.SMR in country["flexible_sources"]:
                    country["flexible_sources"][FlexibleSourceType.SMR]["overnight_costs_per_kw_eur"] = smr_capex
            for country in context_grid["countries"].values():
                if FlexibleSourceType.SMR in country["flexible_sources"]:
                    country["flexible_sources"][FlexibleSourceType.SMR]["overnight_costs_per_kw_eur"] = smr_capex
        if args.gas_ccs_max_twh_limit:
            max_production = float(args.gas_ccs_max_twh_limit)
            czechia["flexible_sources"][FlexibleSourceType.GAS_CCGT_CCS]["max_total_twh"] = max_production
        if args.h2_import_price:
            h2_import_price_EUR_per_kg = float(args.h2_import_price)
            for storage in czechia["storage"]:
                if storage["type"] == StorageType.HYDROGEN or storage["type"] == StorageType.HYDROGEN_PEAK:
                    storage["cost_sell_buy_mwh_eur"] = h2_import_price_EUR_per_kg * \
                        __hydrogen_kg_per_mwh

    # The remaining cases assume that there is only one scenario
    # in the `scenarios` list so far.
    if args.nuclear_capex:
        # Modified overnight costs for conventional nuclear.
        capexes = map(float, args.nuclear_capex.split(","))
        base_scenario = scenarios.pop()
        for capex in capexes:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"capex-{capex:.0f}"
            czechia = scenario["countries"][CZECHIA]
            czechia["basic_sources"][BasicSourceType.NUCLEAR]["overnight_costs_per_kw_eur"] = capex
            scenarios.append(scenario)
    elif args.onshore_capacities:
        # Modified maximum onshore capacities in Czechia.
        capacities = map(float, args.onshore_capacities.split(","))
        base_scenario = scenarios.pop()
        for onshore_mw in capacities:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"onshore-{onshore_mw / 1e3:.0f}gw"
            czechia = scenario["countries"][CZECHIA]
            czechia["basic_sources"][BasicSourceType.ONSHORE]["capacity_mw"] = onshore_mw
            scenarios.append(scenario)
    elif args.solar_capacities:
        capacities = map(float, args.solar_capacities.split(","))
        base_scenario = scenarios.pop()
        for solar_mw in capacities:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"solar-{solar_mw / 1e3:.1f}gw"
            czechia = scenario["countries"][CZECHIA]
            czechia["basic_sources"][BasicSourceType.SOLAR]["capacity_mw"] = solar_mw
            czechia["basic_sources"][BasicSourceType.SOLAR]["min_capacity_mw"] = solar_mw
            scenarios.append(scenario)
    elif args.demand_factors:
        # Modified power demand factors in Czechia.
        factors = map(float, args.demand_factors.split(","))
        base_scenario = scenarios.pop()
        for demand_factor in factors:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"demand-{100 * demand_factor:.0f}pct"
            czechia = scenario["countries"][CZECHIA]
            czechia["load_factors"]["load_base"] *= demand_factor
            scenarios.append(scenario)
    elif args.smr_capexes:
        capexes = map(float, args.smr_capexes.split(","))
        base_scenario = scenarios.pop()
        for smr_capex in capexes:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"smr-{smr_capex:.0f}EUR"
            czechia = scenario["countries"][CZECHIA]
            czechia["flexible_sources"][FlexibleSourceType.SMR]["overnight_costs_per_kw_eur"] = smr_capex
            # Override overnight_costs_per_kw_eur for SMRs for all countries in the scenario dict
            # (and not the context_grid, where it is static for all scenarios).
            for country, country_dict in context_grid["countries"].items():
                if FlexibleSourceType.SMR in country_dict["flexible_sources"]:
                    scenario["countries"][country] = {"flexible_sources": {
                        FlexibleSourceType.SMR: {"overnight_costs_per_kw_eur": smr_capex}}}
            scenarios.append(scenario)
    elif args.smr_capacities:
        # Modified maximum SMR capacities in Czechia.
        capacities = map(float, args.smr_capacities.split(","))
        base_scenario = scenarios.pop()
        for smr_capacity in capacities:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"smr-{smr_capacity / 1e3:.0f}gw"
            czechia = scenario["countries"][CZECHIA]
            czechia["flexible_sources"][FlexibleSourceType.SMR]["capacity_mw"] = smr_capacity
            scenarios.append(scenario)
    elif args.h2_import_prices:
        h2_import_prices_EUR_per_kg = map(float, args.h2_import_prices.split(","))
        base_scenario = scenarios.pop()
        for h2_import_price_EUR_per_kg in h2_import_prices_EUR_per_kg:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"h2-{h2_import_price_EUR_per_kg:.1f}EUR"
            czechia = scenario["countries"][CZECHIA]
            for storage in czechia["storage"]:
                if storage["type"] == StorageType.HYDROGEN or storage["type"] == StorageType.HYDROGEN_PEAK:
                    storage["cost_sell_buy_mwh_eur"] = h2_import_price_EUR_per_kg * \
                        __hydrogen_kg_per_mwh
            # Override import price for hydrogen for all countries in the scenario dict
            # (and not the context_grid, where it is static for all scenarios).
            for country, country_dict in context_grid["countries"].items():
                storage_list = []
                for storage in country_dict["storage"]:
                    if storage["type"] == StorageType.HYDROGEN:
                        storage_list.append({"type": StorageType.HYDROGEN,
                                             "cost_sell_buy_mwh_eur": h2_import_price_EUR_per_kg * __hydrogen_kg_per_mwh})
                    elif storage["type"] == StorageType.HYDROGEN_PEAK:
                        storage_list.append({"type": StorageType.HYDROGEN_PEAK,
                                             "cost_sell_buy_mwh_eur": h2_import_price_EUR_per_kg * __hydrogen_kg_per_mwh})
                if storage_list:
                    scenario["countries"][country] = {"storage": storage_list}
            scenarios.append(scenario)
    elif args.gas_ccs_max_twh_limits:
        # Modified maximum allowed production from natural gas+CCS
        # power plants in Czechia.
        values = map(float, args.gas_ccs_max_twh_limits.split(","))
        base_scenario = scenarios.pop()
        for max_production in values:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"gas+ccs-{max_production:.0f}twh"
            czechia = scenario["countries"][CZECHIA]
            czechia["flexible_sources"][FlexibleSourceType.GAS_CCGT_CCS]["max_total_twh"] = max_production
            scenarios.append(scenario)
    elif args.h2_import_gwh:
        # Modified import capacities for hydrogen in Czechia.
        values = map(float, args.h2_import_gwh.split(","))
        base_scenario = scenarios.pop()
        for import_for_power_gwh in values:
            scenario = deepcopy(base_scenario)
            scenario["name"] = f"h2-import-{import_for_power_gwh:.0f}gwh"
            czechia = scenario["countries"][CZECHIA]

            for storage in czechia["storage"]:
                if storage["type"] == StorageType.HYDROGEN:
                    storage["min_final_energy_mwh"] = storage["initial_energy_mwh"] - \
                        1000 * import_for_power_gwh

            scenarios.append(scenario)

    if len(scenarios) <= args.scenario_id:
        print(f"scenario {args.scenario_id} does not exist, skipping")
        exit()
    s_id = args.scenario_id

    if args.final_svg_plots:
        filter = {
            "days": [
                "2008-02-18", "2008-02-19", "2008-02-20",
                "2008-04-16", "2008-04-17", "2008-04-18",
                "2008-07-28", "2008-07-29", "2008-07-30",
                "2008-11-12", "2008-11-13", "2008-11-14",
            ],
            "countries": [CZECHIA]
        }
    else:
        filter = {
            "week_sampling": 4,  # Plot every fourth week in the output.
            "countries": [CZECHIA, GERMANY]
        }

    return {
        "config": {
            "common_years": args.common_years,
            "entsoe_years": args.entsoe_years,
            "pecd_years": args.pecd_years,
            "load_hydro_from_pecd": load_hydro_from_pecd,
            "load_demand_from_pecd": load_demand_from_pecd,
            "analysis_name": analysis_name,
            "filter": filter,
            "output": {
                # "max_gw": 17,
                # "min_gw": -5.5,
                # "size_x_week": 0.8,
                "format": "svg" if args.final_svg_plots else "png",
                "dpi": 150,
                "size_y_week": 0.7,
                "parts": ["weeks"] if args.final_svg_plots else ["titles", "weeks", "week_summary", "year_stats"],
                "regions": "separate",
            },
            "optimize_capex": args.optimize_capex,
            "input_costs": "2050-SEK",
            "optimize_ramp_up_costs": args.optimize_ramp_up_costs,
            "load_previous_solution": args.load_solution,
            "import_ppa_price": 50,
            "include_transmission_loss_in_price": True,
            "compute_interconnector_capex": True,
            "solver": args.solver,
            "solver_timeout_minutes": args.solver_timeout_minutes if args.solver_timeout_minutes > 0 else None,
            "solver_shift_ipm_termination_by_orders": args.solver_shift_ipm_termination_by_orders,
            "store_model": args.store_model,
        } | context_grid,
        "scenarios": scenarios[s_id:s_id+1],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Data parameters.
    parser.add_argument("--common-years", default="2008")
    parser.add_argument("--entsoe-years", default="2020")
    parser.add_argument("--pecd-years", default="2008")
    parser.add_argument("--pecd-normalization-years", default="2008")
    parser.add_argument("--aggregation-level", default="coarse")
    # Optimization parameters.
    parser.add_argument("--optimize-ramp-up-costs",
                        action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--optimize-capex", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fix-nuclear-capacity-mw", type=int, nargs='?')
    parser.add_argument("--optimize-nuclear-capacity", action=argparse.BooleanOptionalAction)
    # Arguments with only one value allowed.
    parser.add_argument("--nuclear-wacc", help="Discount rate as a factor")
    parser.add_argument("--onshore-capacity", help="Onshore wind capacity in CZ in MW")
    parser.add_argument("--demand-factor", help="Power demand factor in CZ")
    parser.add_argument("--smr-capacity", help="SMR capacity in CZ in MW")
    parser.add_argument("--smr-capex", help="SMR overnight costs in EUR per kWe")
    parser.add_argument("--gas-ccs-max-twh-limit",
                        help="Yearly production limit for gas+CCS in CZ in TWh")
    parser.add_argument("--h2-import-price", help="Assumed import price in EUR/kg H2")
    # Arguments that can have a list of variants (must be paired with --optimize-nuclear-capacity)
    parser.add_argument("--nuclear-capex", help="Overnight cost in EUR/kWe")
    parser.add_argument("--onshore-capacities", help="Onshore wind capacities in CZ in MW")
    parser.add_argument("--solar-capacities", help="Solar capacities in CZ in MW")
    parser.add_argument("--demand-factors", help="Power demand factors in CZ")
    parser.add_argument("--smr-capexes", help="SMR overnight costs in EUR per kWe")
    parser.add_argument("--smr-capacities")
    parser.add_argument("--h2-import-prices")
    parser.add_argument("--gas-ccs-max-twh-limits")
    parser.add_argument("--h2-import-gwh",
                        help="Assumed imports of hydrogen for power generation in GWh")
    parser.add_argument("--solver")
    # Complex model, shift the IMP termination criteria by one order of magnitude to avoid premature
    # termination. Set default timeout to 40 minutes to avoid spending to much time on unsuccessful
    # IPM runs.
    parser.add_argument("--solver-shift-ipm-termination-by-orders", type=int, default=1)
    parser.add_argument("--solver-timeout-minutes", type=int, default=40)
    parser.add_argument("--calibration", action=argparse.BooleanOptionalAction)
    parser.add_argument("--higher-res-prices", action=argparse.BooleanOptionalAction)
    parser.add_argument("--lower-res-prices", action=argparse.BooleanOptionalAction)
    parser.add_argument("--higher-limits", action=argparse.BooleanOptionalAction)
    parser.add_argument("--RES-max-capacity-factor", type=float, default=1.03)
    parser.add_argument("--offshore-max-capacity-factor", type=float, default=1.03)
    parser.add_argument("--allow-extra-smrs", action=argparse.BooleanOptionalAction)
    parser.add_argument("--dispatchable-max-capacity-factor", type=float, default=1.05)

    # Output parameters.
    parser.add_argument("--final-svg-plots", action=argparse.BooleanOptionalAction)
    parser.add_argument("--scenario-id", type=int)
    parser.add_argument("--load-solution", action=argparse.BooleanOptionalAction)
    parser.add_argument("--store-model", action=argparse.BooleanOptionalAction)
    parser.add_argument("--name-prefix")
    # Context specification.
    parser.add_argument("CONTEXT_SCENARIO")
    parser.add_argument("CONTEXT_YEAR", type=int)
    parser.add_argument("CZ_YEAR", type=int)

    args = parser.parse_args()
    print("Parsed command line arguments:", args)

    if not args.optimize_nuclear_capacity and args.fix_nuclear_capacity_mw is None and (
            args.nuclear_capex or args.onshore_capacities or args.solar_capacities or
            args.demand_factors or args.smr_capexes or args.smr_capacities or
            args.gas_ccs_max_twh_limits or args.h2_import_gwh or args.h2_import_prices):
        # TODO: Also check that no more than one of those is specified at once.
        print("Error: Incompatible arguments specified")
        sys.exit(1)

    pecd_normalization_years = list(map(int, args.pecd_normalization_years.split(",")))
    args.common_years = list(map(int, args.common_years.split(",")))
    args.entsoe_years = list(map(int, args.entsoe_years.split(",")))
    args.pecd_years = list(map(int, args.pecd_years.split(",")))

    data_path = Path("data")
    extrapolator = HourlyDataExtrapolator(data_path=data_path)

    ember_loader = EmberNgLoader(
        entsoe_years=args.entsoe_years,
        pecd_years=args.pecd_years,
        pecd_normalization_years=pecd_normalization_years,
        common_years=args.common_years,
        data_file=data_path / "ember" / "Raw data - New Generation.xlsx",
        tyndp_input_file=data_path / "tyndp" / "Draft_Demand_Scenarios_TYNDP_2024.xlsb",
        load_demand_from_pecd=load_demand_from_pecd,
        load_hydro_from_pecd=load_hydro_from_pecd,
        extrapolator=extrapolator)

    runs = [
        make_nuclear_run(ember_loader, args)
    ]

    optimize_runs(runs, extrapolator=extrapolator, root_dir=".")

    # TODO: It would be nice if we could return an exit code here
    # or pass information about failures to the parent process somehow.
