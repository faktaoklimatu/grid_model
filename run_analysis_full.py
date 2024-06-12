#!/usr/bin/env python
import shlex
import subprocess
import time

import pandas

from energy_insights.loaders import EmberNgLoader

# Common context scenarios across all runs.
CONTEXTS = [
    # Optimistic scenarios.
    (EmberNgLoader.TECHNOLOGY_DRIVEN, 2050, 2050, 1.05, 1.09),
    (EmberNgLoader.NUCLEAR_PLUS, 2050, 2050, 1.05, 1.09),

    # Pessimistic scenarios, 2040 to strengthen the effect.
    (EmberNgLoader.RESISTANCE_TO_RES, 2040, 2050, 1.03, 1.03),
    (EmberNgLoader.DELAYED_INTERCONNECTIONS, 2040, 2050, 1.05, 1.09),
]

# All nuclear analysis scenarios.
NUCLEAR_RUNS: dict[str, list[str]] = {
    # Suite of exploration runs:
    # A. Calibration:
    #  Ember default capacities for all sources
    # "calibration": (1, ["--calibration", "--no-optimize-capex"], [])

    # B. Unbounded optimization (to see the limits of nuclear and wind).
    # "unbounded-core": (1, ["--optimize-nuclear-capacity", "--higher-limits"], [])

    # Suite of runs for the study:
    # C. Core runs:
    #   Nuclear 9400 €/kW, WACC 4%
    #   × 5 capacities: +0 - +4
    #   × 4 contexts: TD-B, N+, DI, ResToRES
    # "core": (5, [], []),
    # "core-3yrs": (5, ["--common-years=2008,2009,1995",
    #                   "--pecd-years=2008,2009,1995",
    #                   "--entsoe-years=2020,2019,2015",
    #                   "--pecd-normalization-years=2008,2009,1995",
    #                   "--solver-shift-ipm-termination-by-orders=3",
    #                   "--solver-timeout-minutes=300"], []),
    # "core-3yrs-midfine": (5, ["--common-years=2008,2009,1995",
    #                           "--pecd-years=2008,2009,1995",
    #                           "--entsoe-years=2020,2019,2015",
    #                           "--pecd-normalization-years=2008,2009,1995",
    #                           "--aggregation-level=midfine",
    #                           "--solver-shift-ipm-termination-by-orders=3",
    #                           "--solver-timeout-minutes=600"], []),

    # D. Further scenario sets:
    # "sceptic-demand": (5, ["--demand-factor=1.2"], []),
    # "sceptic-wind": (5, ["--onshore-capacity=3000"], []),
    # "sceptic-smr": (5, ["--smr-capacity=0"], []),
    # "sceptic-ccs": (5, ["--gas-ccs-max-twh-limit=0"], []),
    # "sceptic-prices": (5, ["--higher-res-prices"], []),
    # "sceptic-demand+wind": (5, ["--demand-factor=1.2",
    #                             "--onshore-capacity=3000"], []),
    # "sceptic-demand+wind+smr": (5, ["--demand-factor=1.2",
    #                                 "--onshore-capacity=3000",
    #                                 "--smr-capacity=0"], []),
    # "sceptic-demand+wind+ccs": (5, ["--demand-factor=1.2",
    #                                 "--onshore-capacity=3000",
    #                                 "--gas-ccs-max-twh-limit=0"], []),
    # "sceptic-demand+wind+ccs+smr": (5, ["--demand-factor=1.2",
    #                                     "--onshore-capacity=3000",
    #                                     "--smr-capacity=0",
    #                                     "--gas-ccs-max-twh-limit=0"], []),
    # "sceptic-demand+ccs+smr": (5, ["--demand-factor=1.2",
    #                                "--onshore-capacity=3000",
    #                                "--smr-capacity=0",
    #                                "--gas-ccs-max-twh-limit=0"], []),

    # "optimistic-prices": (5, ["--lower-res-prices"], []),
    # "optimistic-smr": (5, ["--smr-capacity=2800",
    #                        "--allow-extra-smrs",
    #                        "--smr-capex=7000"], []),
    # "optimistic-demand": (5, ["--demand-factor=0.8"], []),
    # "optimistic-wind": (5, ["--onshore-capacity=12000"], []),
    # "optimistic-ccs": (5, ["--gas-ccs-max-twh-limit=30"], []),
    # "optimistic-demand+wind": (5, ["--demand-factor=0.8", "--onshore-capacity=12000"], []),
    # "optimistic-prices+smr": (5, ["--lower-res-prices",
    #                               "--smr-capacity=2800",
    #                               "--allow-extra-smrs",
    #                               "--smr-capex=7000"], []),
    # "optimistic-prices+smr+wind": (5, ["--lower-res-prices",
    #                                    "--smr-capacity=2800",
    #                                    "--allow-extra-smrs",
    #                                    "--smr-capex=7000",
    #                                    "--onshore-capacity=12000"], []),
    # "optimistic-prices+smr+wind+demand": (5, ["--lower-res-prices",
    #                                           "--smr-capacity=2800",
    #                                           "--allow-extra-smrs",
    #                                           "--smr-capex=7000"
    #                                           "--onshore-capacity=12000",
    #                                           "--demand-factor=0.8"], []),

    # E. Sensitivities:

    # E.1 Effect of wind
    # "effect-of-wind-0":
    #    (15, ["--fix-nuclear-capacity-mw=2060",
    #      "--onshore-capacities=1000,2000,3000,4000,5000,6000,7000,8000,9000,10000,11000,12000,13000,14000,15000",
    #      ], []),
    # "effect-of-wind-2":
    #     (15, ["--fix-nuclear-capacity-mw=4260",
    #      "--onshore-capacities=1000,2000,3000,4000,5000,6000,7000,8000,9000,10000,11000,12000,13000,14000,15000"
    #      ], []),
    # "effect-of-wind-4":
    #     (15, ["--fix-nuclear-capacity-mw=6460",
    #      "--onshore-capacities=1000,2000,3000,4000,5000,6000,7000,8000,9000,10000,11000,12000,13000,14000,15000"
    #      ], []),

    # E.2 Effect of solar
    # "effect-of-solar-2":
    #     (14, ["--fix-nuclear-capacity-mw=4260",
    #      "--solar-capacities=2500,5000,7500,10000,12500,15000,17500,20000,22500,25000,27500,30000,32500, 35000"], []),
    # "effect-of-cheaper-solar-2":
    #     (14, ["--fix-nuclear-capacity-mw=4260",
    #           "--lower-res-prices",
    #           "--solar-capacities=2500,5000,7500,10000,12500,15000,17500,20000,22500,25000,27500,30000,32500, 35000"], []),

    # E.3 Effect of demand
    # "effect-of-demand-0":
    #     (9, ["--fix-nuclear-capacity-mw=2060",
    #      "--demand-factors=0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20"], []),
    # "effect-of-demand-2":
    #     (9, ["--fix-nuclear-capacity-mw=4260",
    #      "--demand-factors=0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20"], []),
    # "effect-of-demand-4":
    #     (9, ["--fix-nuclear-capacity-mw=6460",
    #      "--demand-factors=0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.15, 1.20"], []),

    # E.4 Effect of interconnection (derived from the core set).
    # E.4 Effect of hydrogen import prices
    # "effect-of-hydrogen-price-2":
    #     (6, ["--fix-nuclear-capacity-mw=4260",
    #          "--RES-max-capacity-factor=1.5",
    #          "--offshore-max-capacity-factor=1.5",
    #          "--dispatchable-max-capacity-factor=1.5",
    #          "--h2-import-prices=1.5,2,2.5,3,3.5,4"], []),

    # E.5 Effect of nuclear (derived from the core set)
    # E.5 Effect of SMR
    # "effect-of-smr-0":
    #     (7, ["--fix-nuclear-capacity-mw=2060",
    #          "--smr-capacity=2800",
    #          "--allow-extra-smrs",
    #          "--smr-capexes=5000,6000,7000,8000,9000,10000,11000"], []),
    # "effect-of-smr-2":
    #     (7, ["--fix-nuclear-capacity-mw=4260",
    #          "--smr-capacity=2800",
    #          "--allow-extra-smrs",
    #          "--smr-capexes=5000,6000,7000,8000,9000,10000,11000"], []),
    # "effect-of-smr-4":
    #     (7, ["--fix-nuclear-capacity-mw=6460",
    #          "--smr-capacity=2800",
    #          "--allow-extra-smrs",
    #          "--smr-capexes=5000,6000,7000,8000,9000,10000,11000"], []),
}


def _print_run_times(run_times: list[tuple[str, str, float]]) -> None:
    times_df = pandas.DataFrame(run_times, columns=["set", "context", "time"])

    print("\nRun times (in minutes):")
    print(times_df)

    print("\nRun times of sets:")
    print(times_df.groupby("set").time.sum())


if __name__ == "__main__":
    run_times: list[tuple[str, str, float]] = []

    num_run_sets = len(NUCLEAR_RUNS)
    num_contexts = len(CONTEXTS)
    try:
        for run_id, (name_prefix, (scenario_count, run_arguments, scenarios)) in enumerate(NUCLEAR_RUNS.items()):
            print("-" * 70)
            for ctx_id, (context, target_year_europe, target_year_cz,
                         RES_max_capacity_factor, offshore_max_capacity_factor) in enumerate(CONTEXTS):
                run_start_time = time.monotonic()
                if not scenarios:
                    scenarios = range(scenario_count)
                for scenario_id in scenarios:
                    print(f"\nRunset {run_id + 1}/{num_run_sets} ‘{name_prefix}’, "
                          f"context {ctx_id + 1}/{num_contexts} ‘{context}’, "
                          f"scenario {scenario_id + 1}/{scenario_count}\n")

                    args = [
                        "python",
                        "run_analysis_single.py",
                        context,
                        str(target_year_europe),
                        str(target_year_cz),
                        f"--name-prefix={name_prefix}",
                        f"--RES-max-capacity-factor={RES_max_capacity_factor:.2f}",
                        f"--offshore-max-capacity-factor={offshore_max_capacity_factor:.2f}",
                        f"--scenario-id={scenario_id}",
                    ] + run_arguments

                    # If not the first run or only a subsection is selected to get added.
                    if scenario_id > 0 or len(scenarios) < scenario_count:
                        args.append("--amend-stats")

                    print("Command line:", shlex.join(args))

                    subprocess.run(args)

                run_finish_time = time.monotonic()
                run_elapsed_min = (run_finish_time - run_start_time) / 60

                run_times.append((name_prefix, context, run_elapsed_min))

            # Print the partial set of run times after each finished run.
            _print_run_times(run_times)

    except KeyboardInterrupt:
        # Ignore the keyboard interrupt in order to be able to print
        # elapsed times before bailing.
        print("Interrupted by keyboard, stopping model runs")
        _print_run_times(run_times)
