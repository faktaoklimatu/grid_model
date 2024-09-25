#!/usr/bin/env python
import argparse
import sys
from pathlib import Path
from typing import Optional, Union

import pandas

from energy_insights.execution_utils import (
    get_entsoe_loader,
    get_pecd_loader,
    optimize_runs,
)
from energy_insights.loaders.pemmdb import Pemmdb2023Loader
from energy_insights.region import *
from energy_insights.scenarios.czech_coal import (
    FLEXIBLE_COAL_TYPES,
    cz_2025_basic,
    cz_2028_basic,
    cz_2028_advanced,
    get_pemmdb_loader,
    global_adjustments,
    make_pessimistic_scenario,
    make_scenario,
)


def load_capacities_from_summary(csv_path: Union[str, Path], scenario: str, region: Region) -> pandas.Series:
    summary_df = pandas.read_csv(csv_path)
    capacities_df = summary_df.loc[
        (summary_df["name"] == scenario)
        & (summary_df["region"] == region)
        & (summary_df["season"] == "Y")
        & (summary_df["stat"] == "capacity_GW"),
        ["source", "val"]
    ].set_index("source")

    if capacities_df.empty:
        raise KeyError(
            "Could not load capacities from summary file. Is the scenario name "
            f"(‘{scenario}’) correct?"
        )

    # Round to megawatts.
    return capacities_df["val"].round(3)


def make_coal_run(args: argparse.Namespace, pemmdb_loader: Pemmdb2023Loader) -> dict:
    pecd_year = args.pecd_year
    name = args.name
    aggregation_level = None if args.aggregation_level == "none" else args.aggregation_level

    scenarios = [
        {
            "name": "2025-cheap-ets",
            "year": 2025,
            "adjustments": cz_2025_basic,
            "global_adjustments": global_adjustments,
            "input_costs": "2025-cheap-ets",
        },
        {
            "name": "2025-expensive-ets",
            "year": 2025,
            "adjustments": cz_2025_basic,
            "global_adjustments": global_adjustments,
        },
        {
            "name": "2028-no",
            "year": 2028,
            "adjustments": cz_2025_basic,
            "global_adjustments": global_adjustments,
        },
        {
            "name": "2028-slow",
            "year": 2028,
            "adjustments": cz_2028_basic,
            "global_adjustments": global_adjustments,
        },
        {
            "name": "2028-advanced",
            "year": 2028,
            "adjustments": cz_2028_advanced,
            "global_adjustments": global_adjustments,
        },
    ]

    format = None
    if args.plots:
        format = "svg" if args.final_plots else "png"
    if args.final_plots:
        filter = {
            "countries": [CZECHIA],
            "days": [
                "2018-06-11", "2018-06-12", "2018-06-13",
                "2018-10-30", "2018-11-01", "2018-11-02",
                "2018-11-26", "2018-11-27", "2018-11-28",
            ],
        }
    else:
        filter = {"week_sampling": 4,
                  "countries": [CZECHIA]}

    return {
        "config": {
            "analysis_name": name,
            "common_years": [args.common_year],
            "entsoe_years": [args.entsoe_year],
            "pecd_years": [pecd_year],
            "filter": filter,
            "output": {
                "format": format,
                "dpi": 150,
                "heat": args.optimize_heat and not args.final_plots,
                "size_y_week": 0.7,
                "parts": ["weeks"] if args.final_plots else ["titles", "weeks", "week_summary", "year_stats"],
                "regions": "separate",
                "group_colors": args.group_colors,
            },
            "optimize_capex": args.optimize_coal != "none",
            "optimize_heat": args.optimize_heat,
            "optimize_ramp_up_costs": True,
            "load_previous_solution": args.load_solution,
            # "include_transmission_loss_in_price": True,
            "store_model": args.store_model,
        },
        "scenarios": [
            make_scenario(
                scenario_spec,
                pemmdb_loader=pemmdb_loader,
                aggregation_level=aggregation_level,
                optimize_coal=args.optimize_coal,
                optimize_heat=args.optimize_heat,
                include_reserves=args.with_reserves,
                tyndp_lignite_prices=args.tyndp_lignite_prices,
            )
            for scenario_spec in scenarios
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    # Data parameters.
    parser.add_argument("--common-year", type=int, default=2018)
    # ENTSO-E has only data back to 2010, we need a fallback year for the load.
    # TODO: Try ENTSO-E 2020 & PECD 2009 -- leapness mismatch.
    # NOTE: ENTSO-E is now used for nuclear production only.
    parser.add_argument("--entsoe-year", type=int, default=2018)
    # NOTE: There's a mismatch between the weather years (common_year and
    # entsoe_year) and PECD year because we only have weather starting 2019,
    # but the PECD dataset ends in 2016.
    parser.add_argument("--pecd-year", type=int, default=2009)
    parser.add_argument("--aggregation-level", default="coarse")

    # Optimization parameters.
    parser.add_argument("--cz-coal-subsidy", type=float, default=None)
    parser.add_argument("--optimize-coal", choices=["all", "cz", "none"], default="none")
    parser.add_argument("--optimize-heat", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--with-reserves", action="store_true")
    parser.add_argument("--tyndp-lignite-prices", action="store_true")

    # Output parameters.
    parser.add_argument("--load-solution", action="store_true")
    parser.add_argument("--store-model", action="store_true")
    # Expects a path to the (long) summary CSV. Optionally accepts
    # a scenario name separated with a colon, for example, to load
    # capacities from the 2028-slow scenario:
    #   --load-coal-capacities-from coaldown-1985+capex.csv:2028-slow
    parser.add_argument("--load-coal-capacities-from", default=None)
    # Run name.
    parser.add_argument("--name", default="coaldown-core")
    # Scenario identifier override.
    parser.add_argument("--scenario-override", default=None)
    parser.add_argument("--plots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--final-plots", action="store_true")
    parser.add_argument("--group-colors", action="store_true")

    # Scenarios specification.
    parser.add_argument("SCENARIOS", nargs="*")

    args = parser.parse_args()
    print("Parsed command line arguments:", args)

    entsoe_loader = get_entsoe_loader("data")
    pecd_loader = get_pecd_loader("data")
    pemmdb_loader = get_pemmdb_loader(root_dir=".")

    coal_run = make_coal_run(args, pemmdb_loader)

    scenario_2025 = next(filter(lambda sc: sc["name"] ==
                         "2025-expensive-ets", coal_run["scenarios"]))
    scenario_2028 = next(filter(lambda sc: sc["name"] == "2028-slow", coal_run["scenarios"]))
    coal_run["scenarios"].append(make_pessimistic_scenario("2025-crit", scenario_2025, scenario_2025,
                                                           buildout_factor=0,  # irrelevant
                                                           demand_increase=1.05))
    coal_run["scenarios"].append(make_pessimistic_scenario("2028-crit", scenario_2025, scenario_2028,
                                                           buildout_factor=0.5, demand_increase=1.05))
    coal_run["scenarios"].append(make_pessimistic_scenario("2028-supercrit", scenario_2025, scenario_2028,
                                                           buildout_factor=0, demand_increase=1.1))

    if args.SCENARIOS:
        coal_run["scenarios"] = list(
            filter(lambda sc: sc["name"] in args.SCENARIOS, coal_run["scenarios"]))

    if not coal_run["scenarios"]:
        print("Warning: No scenarios match specified filter, quitting")
        sys.exit(0)

    # Operating subsidies for coal plants.
    if args.cz_coal_subsidy:
        for scenario in coal_run["scenarios"]:
            flexible_sources = scenario["countries"][CZECHIA]["flexible_sources"]
            for coal_type in FLEXIBLE_COAL_TYPES:
                if source_spec := flexible_sources.get(coal_type):
                    source_spec["subsidy_eur_per_mwh"] = args.cz_coal_subsidy

    # Load optimized capacities from summary file if requested.
    if args.load_coal_capacities_from:
        summary_csv_path = args.load_coal_capacities_from
        source_scenario: Optional[str] = None

        if ":" in summary_csv_path:
            summary_csv_path, source_scenario = summary_csv_path.split(":")

        for scenario in coal_run["scenarios"]:
            for country, country_data in scenario["countries"].items():
                capacities = load_capacities_from_summary(
                    summary_csv_path,
                    source_scenario or scenario["name"],
                    country
                )

                flexibl_sources = country_data["flexible_sources"]
                for coal_type in FLEXIBLE_COAL_TYPES:
                    if source_spec := flexibl_sources.get(coal_type):
                        source_spec["capacity_mw"] = 1000 * capacities.get(coal_type.value, 0)

    if args.scenario_override:
        if len(coal_run["scenarios"]) > 1:
            print("Error: Cannot specify scenario name override for more than one scenario")
            sys.exit(1)
        coal_run["scenarios"][0]["name"] = args.scenario_override

    optimize_runs(
        [coal_run],
        entsoe_loader=entsoe_loader,
        pecd_loader=pecd_loader,
        root_dir="."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted by user")
        sys.exit(130)
