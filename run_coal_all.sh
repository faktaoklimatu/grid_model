#!/bin/bash

# Exit on first error.
set -e

scenarios=(2025-cheap-ets 2025-expensive-ets 2028-no 2028-slow 2028-advanced)
num_scenarios=${#scenarios[@]}
aggregation_level=none

# Selected years:
#   1985: benchmark for a "bad" year, extreme winter, high demand
#   1989: droughts[1], low inflows in parts of Europe
#   2008: benchmark for a "good" year, fine weather, sunny, windy
#   2009: "average" year
#   2014: lowest demand in the PECD dataset
#
# [1]: https://www.nature.com/articles/s41598-018-27464-4
weather_years=(2009 2014 1985 1989 2008)
capex_optimization_weather_year=1985
# Go for the "average" year when analysing subsidies.
subsidy_scenario=2028-slow
subsidy_weather_year=2009
subsidy_levels=(0 2.5 5 7.5 10 12.5 15 17.5 20)

capex_optimization_name="coaldown+weather-$capex_optimization_weather_year+capex"
capex_summary_path="output/$capex_optimization_name/$capex_optimization_name-complete.csv"

# Uncomment for plotting final SVG hourly plots.
# final_plots="--final-plots --load-solution --group-colors"

common_arguments="--aggregation-level $aggregation_level --tyndp-lignite-prices $final_plots"

echo "Running all coal scenarios..."

# 1. Optimise coal capacity in all of Europe 1985 with reserves enabled.
idx=1
for scenario in "${scenarios[@]}"; do
    echo "Capex optimization $idx/$num_scenarios: $scenario, weather $capex_optimization_weather_year"
    idx=$((idx + 1))
    if [[ -d "output/$capex_optimization_name/$scenario" ]]; then
        echo "Outputs have already been generated, skipping"
        continue
    fi
    time ./run_coal_single.py \
        "$scenario" \
        --name "$capex_optimization_name" \
        --pecd-year $capex_optimization_weather_year \
        $common_arguments \
        --optimize-coal all \
        --with-reserves
done

# 2. Optimise dispatch only in given weather years. Set coal capacity
#    in CZ per results of the previous step. Keep all other capacities
#    according to PEMMDB.
idx=1
num_runs=$((num_scenarios * ${#weather_years[@]}))
for weather_year in "${weather_years[@]}"; do
    for scenario in "${scenarios[@]}"; do
        name="coaldown+weather-$weather_year"
        echo "Dispatch optimization $idx/$num_runs: $scenario, weather $weather_year"
        idx=$((idx + 1))
        if [[ -d "output/$name/$scenario" && -z "$final_plots" ]]; then
            echo "Outputs have already been generated, skipping"
            continue
        fi
        time ./run_coal_single.py \
            "$scenario" \
            --name "$name" \
            --pecd-year $weather_year \
            $common_arguments \
            --load-coal-capacities-from "$capex_summary_path"
    done
done

# 3 Analysis of the critical scenarios -- limited interconnection,
#   increased demand, limited new resources.
scenarios=(2028-supercrit 2028-crit 2025-crit)
for scenario in "${scenarios[@]}"; do
    num_runs=${#weather_years[@]}
    idx=1
    for weather_year in "${weather_years[@]}"; do
        name="coaldown+weather-$weather_year"
        echo "Critical dispatch for $scenario $idx/$num_runs: weather $weather_year"
        idx=$((idx + 1))
        if [[ -d "output/$name/$scenario" ]]; then
            echo "Outputs have already been generated, skipping"
            continue
        fi
        time ./run_coal_single.py \
            "$scenario" \
            --name "$name" \
            --pecd-year $weather_year \
            $common_arguments \
            --load-coal-capacities-from "$capex_summary_path:2028-slow"
    done
done

# 4. Analysis of coal subsidy for neutral import balance.
idx=1
num_runs=${#subsidy_levels[@]}
for coal_subsidy in "${subsidy_levels[@]}"; do
    name="coaldown+weather-$subsidy_weather_year+subsidy"
    scenario_override="subsidy-$coal_subsidy"
    echo "Subsidy analysis $idx/$num_runs: $scenario_override, weather $subsidy_weather_year"
    idx=$((idx + 1))
    if [[ -d "output/$name/$scenario_override" ]]; then
        echo "Outputs have already been generated, skipping"
        continue
    fi
    time ./run_coal_single.py \
        "$subsidy_scenario" \
        --name "$name" \
        --scenario-override "$scenario_override" \
        --pecd-year $subsidy_weather_year \
        $common_arguments \
        --cz-coal-subsidy $coal_subsidy
done

echo "All done"

