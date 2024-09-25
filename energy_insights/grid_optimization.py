"""
Provides optimal grid dispatch for a grid with given parameters.
"""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
from pulp import LpAffineExpression, LpMinimize, LpProblem, LpVariable, value
from pulp.constants import LpStatus

from .grid_capex_utils import *
from .country_grid import CountryGrid
from .country_grid_spot_price_estimator import CountryGridSpotPriceEstimator
from .export_flow import ExportFlow
from .grid_plot_utils import (
    Keys,
    get_basic_key,
    get_charging_key,
    get_discharging_key,
    get_export_key,
    get_flexible_basic_decrease_key,
    get_flexible_basic_predefined_key,
    get_flexible_electricity_equivalent_key,
    get_flexible_heat_key,
    get_flexible_key,
    get_import_key,
    get_ramp_up_key,
    get_state_of_charge_key,
)
from .params_library.interconnectors import Interconnectors, OUTFLOW_CAPACITY_COST_EUR_PER_MWH
from .region import Region
from .solver_util import Solver, solve_problem
from .sources.basic_source import BasicSourceType, FlexibleBasicSource
from .sources.flexible_source import (
    BackPressureTurbine,
    CapacityFactorConstraint,
    ExtractionTurbine,
    FlexibleSource,
    HeatRecoveryUnit,
    ProductionConstraint,
)
from .sources.storage import StorageUse


def _extract_values(variables: list[LpVariable]) -> np.ndarray:
    return np.fromiter(map(lambda x: x.varValue, variables), dtype=float)


@dataclass
class CountryProblem:
    grid: CountryGrid
    optimize_capex: bool
    optimize_ramp_up_costs: bool
    optimize_heat: bool

    def __post_init__(self):
        """
        `data` is expected to have the following columns:
        - Load for electricity demand from the grid;
        - Heat_Demand_MW for heat demand from the district heating systems;
        - Wind, Solar, Nuclear, Hydro for hourly generation from individual sources (in MWh);
        - (optional) Hydro pumped open inflow, Hydro reservoir inflow, Hydro RoR inflow for
            river inflow time series.
        """
        columns = [Keys.LOAD, Keys.HEAT_DEMAND, Keys.SOLAR, Keys.WIND, Keys.WIND_ONSHORE,
                   Keys.WIND_OFFSHORE, Keys.NUCLEAR, Keys.HYDRO]
        # Optional columns from PECD dataset.
        if Keys.HYDRO_INFLOW_PUMPED_OPEN in self.grid.data:
            columns += [
                Keys.HYDRO_INFLOW_PONDAGE, Keys.HYDRO_INFLOW_PUMPED_OPEN, Keys.HYDRO_INFLOW_RESERVOIR,
                Keys.HYDRO_INFLOW_ROR
            ]
        # Optional columns from cooling/heating demand decomposition.
        if Keys.LOAD_BASE in self.grid.data:
            columns += [Keys.LOAD_BASE, Keys.LOAD_HEAT_PUMPS]
        # TODO: Remove this data validation or move it into CountryGrid.
        self.grid.data = self.grid.data.loc[:, columns]

    def __add__(self, other):
        assert self.optimize_capex == other.optimize_capex, \
            f"optimize_capex != for {self.grid.type} and {other.grid.type}"
        assert self.optimize_ramp_up_costs == other.optimize_ramp_up_costs, \
            f"optimize_ramp_up_costs != for {self.type} and {other.type}"
        assert self.optimize_heat == other.optimize_heat, \
            f"optimize_heat != for {self.type} and {other.type}"
        return CountryProblem(
            grid=CountryGrid.__add__(self.grid, other.grid),
            optimize_capex=self.optimize_capex,
            optimize_ramp_up_costs=self.optimize_ramp_up_costs,
            optimize_heat=self.optimize_heat)

    # Implement to allow summing a list of country grids (the sum starts with int 0).
    def __radd__(self, other):
        if other == 0:
            return self
        return self.__add__(other)

    def _make_list_variables(self,
                             prefix: str,
                             count: int,
                             list_index: Optional[int] = None,
                             **kwargs) -> list[LpVariable]:
        return [LpVariable(self._make_name(prefix, t, list_index), **kwargs) for t in range(count)]

    def _make_name(self, name: str, index: Union[int, str], list_index: Optional[int] = None) -> str:
        if list_index is None:
            return "{}_{}_{}".format(name, self.grid.country, index)
        return "{}_{}_{}_{}".format(name, self.grid.country, list_index, index)

    def _get_low_bound(self, installed: float, min_installed: float) -> float:
        global_low_bound = 0 if self.optimize_capex else 1
        if installed == 0:
            return global_low_bound
        min_ratio = min_installed / installed
        if global_low_bound > min_ratio:
            return global_low_bound
        return min(min_ratio, 1.0)

    def _get_inflow(self, inflow_MW, country_from: Region):
        return inflow_MW * (1 - self.inflow_loss[country_from])

    def _is_midnight(self, t: int) -> bool:
        return t % 24 == 0

    def _get_max_ramp(self,
                      source: Union[FlexibleBasicSource, FlexibleSource]) -> float:
        return source.ramp_rate * source.capacity_mw

    def _add_ramp_contraints(self,
                             prob: LpProblem,
                             cost: LpAffineExpression,
                             source: Union[FlexibleBasicSource, FlexibleSource],
                             production_MW: list[LpVariable],
                             ramp_up_MW: list[LpVariable],
                             installed_factor: LpVariable,
                             suffix: str,
                             index: int,
                             t: int,
                             ramp_up_predefined_MW: float = 0):
        assert t > 0, "cannot add ramp constraints for the first time slice"
        assert source.ramp_rate < 1, "no reason for ramp constraints for sources with ramp rate 1"
        max_ramp_MW: float = source.ramp_rate * source.capacity_mw

        # Positive or negative relaxation of ramp constraints (if the predefined curve ramps faster
        # than `ramp_up_MW`).
        ramp_up_extra_MW: LpAffineExpression = LpAffineExpression()
        ramp_down_extra_MW: LpAffineExpression = LpAffineExpression()
        if ramp_up_predefined_MW > max_ramp_MW:
            ramp_up_extra_MW = (ramp_up_predefined_MW - max_ramp_MW) * installed_factor
        elif ramp_up_predefined_MW < -max_ramp_MW:
            ramp_down_extra_MW = (ramp_up_predefined_MW + max_ramp_MW) * installed_factor * -1

        # Make tighter limits to the ramp up variable (applying the installed factor).
        max_ramp_MW_scaled: LpAffineExpression = max_ramp_MW * installed_factor
        prob += (ramp_up_MW[t] <= max_ramp_MW_scaled,
                 self._make_name(f"FlexibleRampUpperVar{suffix}", t, index))

        prob += (production_MW[t - 1] + ramp_up_MW[t] - max_ramp_MW_scaled - ramp_down_extra_MW <= production_MW[t],
                 self._make_name(f"FlexibleRampLower{suffix}", t, index))
        prob += (production_MW[t - 1] + ramp_up_MW[t] + ramp_up_extra_MW >= production_MW[t],
                 self._make_name(f"FlexibleRampUpperResult{suffix}", t, index))
        if self.optimize_ramp_up_costs:
            cost += ramp_up_MW[t] * source.ramp_up_cost_mw_eur

    def _decrease_extraction_turbine_el_production(self,
                                                   turbine: ExtractionTurbine,
                                                   el_production_MW: Union[float, LpAffineExpression],
                                                   heat_production_MW: Union[float,
                                                                             LpAffineExpression]
                                                   ) -> Union[float, LpAffineExpression]:
        variable_heat_production_MW = heat_production_MW - \
            (el_production_MW * turbine.base_ratio_heat_mw_per_el_mw)
        decrease_MW = variable_heat_production_MW / turbine.heat_mw_per_decreased_el_mw
        return decrease_MW

    def construct_country_problem(self,
                                  prob: LpProblem,
                                  objective: LpAffineExpression,
                                  outflow: dict[Region, list[LpVariable]],
                                  inflow: dict[Region, list[LpVariable]],
                                  inflow_loss: dict[Region, float]):
        self.outflow = outflow
        self.inflow = inflow
        self.inflow_loss = inflow_loss

        num_steps = self.grid.data.index.size

        # Variables.

        # Per flexible basic type, list of present production in MW, one for each step.
        self.flexible_basic_production_MW_dict: dict[BasicSourceType, list[LpVariable]] = {}
        # Per flexible basic type, list of increases in production in MW, one for each step.
        self.flexible_basic_ramp_up_MW_dict: dict[BasicSourceType, list[LpVariable]] = {}
        # Per flexible basic type, last production from the predefined curve.
        self.last_flexible_basic_production_dict: dict[BasicSourceType, float] = {}
        # Per basic type, affine expression with its opex costs.
        self.basic_source_opex_per_mwh_eur: dict[BasicSourceType, MaybeAffineExpression] = {}
        for type, source in self.grid.basic_sources.items():
            if isinstance(source, FlexibleBasicSource) and source.is_truly_flexible():
                self.flexible_basic_production_MW_dict[type] = self._make_list_variables(
                    "flexible_basic_production_MW", num_steps, list_index=type.value,
                    lowBound=0, upBound=None)
                ramp_up_vars = self._make_list_variables(
                    "flexible_basic_ramp_up_MW", num_steps, list_index=type.value, lowBound=0,
                    upBound=self._get_max_ramp(source)
                )
                ramp_up_vars[0].setInitialValue(0)
                self.flexible_basic_ramp_up_MW_dict[type] = ramp_up_vars

        # Per flexible type, installed factor between 0 and 1 (for capex optimization).
        self.flexible_installed_list: list[LpVariable] = []
        # Per flexible type, list of present production in MW, one for each step.
        self.flexible_production_MW_list: list[list[LpVariable]] = []
        self.flexible_heat_production_MW_list: list[list[LpVariable]] = []
        # Per flexible type, list of present increases in production in MW, one for each step.
        self.flexible_ramp_up_MW_list = []
        # Per flexible type, affine expression with its opex costs.
        self.flexible_opex_per_mwh_eur: list[MaybeAffineExpression] = []

        for index, flexible_source in enumerate(self.grid.flexible_sources):
            low_bound = self._get_low_bound(flexible_source.capacity_mw,
                                            flexible_source.min_capacity_mw)
            installed = LpVariable(
                self._make_name("flexible_installed", index), lowBound=low_bound, upBound=1)
            # If there's no production for this type, solver will factor it out as trivial and will
            # not provide any value. Circumvent this by providing initial value.
            installed.setInitialValue(1)
            self.flexible_installed_list.append(installed)

            self.flexible_production_MW_list.append(self._make_list_variables(
                "flexible_production_MW", num_steps, list_index=index, lowBound=0,
                upBound=flexible_source.capacity_mw
            ))
            if flexible_source.ramp_rate < 1:
                ramp_up_vars = self._make_list_variables(
                    "flexible_ramp_up_MW", num_steps, list_index=index, lowBound=0,
                    upBound=self._get_max_ramp(flexible_source)
                )
                ramp_up_vars[0].setInitialValue(0)
                self.flexible_ramp_up_MW_list.append(ramp_up_vars)
            else:
                self.flexible_ramp_up_MW_list.append([])

            self.flexible_opex_per_mwh_eur.append(get_source_opex_per_mwh_eur_with_capacity(
                flexible_source, installed * flexible_source.capacity_mw))
            if self.optimize_heat:
                self.flexible_heat_production_MW_list.append(self._make_list_variables(
                    "flexible_heat_production_MW_list", num_steps, list_index=index, lowBound=0,
                    upBound=None
                ))

        # Per storage type, installed factor between 0 and 1 for charging (for capex optimization).
        self.storage_charging_installed_list: list[LpVariable] = []
        # Per storage type, installed factor between 0 and 1 for discharging (for capex opt.).
        self.storage_discharging_installed_list: list[LpVariable] = []
        # Per storage type, list of present state of charge in MWh, one for each step.
        self.storage_state_MWh_list: list[list[LpVariable]] = []
        # Per storage type, list of present charging in MW, one for each step.
        self.storage_charging_MW_list: list[list[LpVariable]] = []
        # Per storage type, list of present discharging in MW, one for each step.
        self.storage_discharging_MW_list: list[list[LpVariable]] = []
        # Per storage type, affine expression with its opex costs.
        self.storage_discharging_opex_per_mwh_eur: list[MaybeAffineExpression] = []
        self.storage_charging_opex_per_mwh_eur: list[MaybeAffineExpression] = []
        for index, storage_type in enumerate(self.grid.storage):
            charging_low_bound = self._get_low_bound(storage_type.capacity_mw_charging,
                                                     storage_type.min_capacity_mw_charging)
            discharging_low_bound = self._get_low_bound(storage_type.capacity_mw,
                                                        storage_type.min_capacity_mw)
            charging_installed_var = LpVariable(self._make_name("storage_charging_installed", index),
                                                lowBound=charging_low_bound, upBound=1)
            discharging_installed_var = LpVariable(self._make_name("storage_discharging_installed", index),
                                                   lowBound=discharging_low_bound, upBound=1)
            self.storage_charging_installed_list.append(charging_installed_var)
            self.storage_discharging_installed_list.append(discharging_installed_var)
            discharging_opex = get_discharging_opex_per_mwh_eur_with_capacity(
                storage_type, discharging_installed_var * storage_type.capacity_mw)
            self.storage_discharging_opex_per_mwh_eur.append(discharging_opex)
            charging_opex = get_charging_opex_per_mwh_eur_with_capacity(
                storage_type, charging_installed_var * storage_type.capacity_mw_charging)
            self.storage_charging_opex_per_mwh_eur.append(charging_opex)

            # If there's no production for this type, solver will factor it out as trivial and will
            # not provide any value. Circumvent this by providing initial value.
            charging_installed_var.setInitialValue(1)
            discharging_installed_var.setInitialValue(1)

            max_energy_MWh = storage_type.max_energy_mwh
            if storage_type.separate_charging:
                max_energy_MWh *= self.grid.num_years

            self.storage_state_MWh_list.append(self._make_list_variables(
                "storage_state_MWh",
                num_steps,
                list_index=index,
                lowBound=0,
                upBound=max_energy_MWh,
            ))
            self.storage_charging_MW_list.append(self._make_list_variables(
                "storage_charging_MW",
                num_steps,
                list_index=index,
                lowBound=0,
                upBound=storage_type.capacity_mw_charging,
            ))
            self.storage_discharging_MW_list.append(self._make_list_variables(
                "storage_discharging_MW",
                num_steps,
                list_index=index,
                lowBound=0,
                upBound=storage_type.capacity_mw,
            ))

        # Per basic type, installed factor between 0 and 1 (for capex optimization).
        self.basic_installed_dict: dict[BasicSourceType, LpVariable] = {}
        for type, source in self.grid.basic_sources.items():
            low_bound = self._get_low_bound(source.capacity_mw, source.min_capacity_mw)
            self.basic_installed_dict[type] = LpVariable(
                self._make_name("basic_installed", type.value),
                lowBound=low_bound, upBound=1
            )
            # If there's no production for this type, solver will factor it out as trivial and will
            # not provide any value. Circumvent this by providing initial value.
            self.basic_installed_dict[type].setInitialValue(1)
            self.basic_source_opex_per_mwh_eur[type] = get_source_opex_per_mwh_eur_with_capacity(
                source, source.capacity_mw * self.basic_installed_dict[type])

        # Global constraints.
        for index, storage_type in enumerate(self.grid.storage):
            charging_installed_factor = self.storage_charging_installed_list[index]
            # Constraints for storage installed (factor for charging must equal factor for discharging,
            # unless it has separate_charging).
            if not storage_type.separate_charging:
                discharging_installed_factor = self.storage_discharging_installed_list[index]
                prob += (charging_installed_factor == discharging_installed_factor,
                         self._make_name("NonSeparateStorageSameInstalled", index))
            if storage_type.min_charging_capacity_ratio_to_VRE > 0:
                installed_VRE_mw = 0
                for type, source in self.grid.basic_sources.items():
                    if type.is_variable_renewable():
                        installed_VRE_mw += self.basic_installed_dict[type] * source.capacity_mw
                installed_charging_mw = charging_installed_factor * storage_type.capacity_mw_charging
                prob += (installed_charging_mw >= installed_VRE_mw * storage_type.min_charging_capacity_ratio_to_VRE,
                         self._make_name("MinChargingCapacityRatioToVRE", index))

        # Constraints for each step.
        flexible_production_el_eq_sum_MWh_list = [0 for _ in self.grid.flexible_sources]
        for t, (_index, row) in enumerate(self.grid.data.iterrows()):
            total_hourly_cost_EUR: LpAffineExpression = LpAffineExpression()

            # Get the sum of production and consumption to specify adequacy.
            total_heat_supply_MW = 0

            # Production and variable costs from basic sources.
            total_basic_production_MW = 0
            for type, source in self.grid.basic_sources.items():
                if isinstance(source, FlexibleBasicSource) and source.is_truly_flexible():
                    production_MW = self.flexible_basic_production_MW_dict[type][t]
                else:
                    key = get_basic_key(type)
                    installed_factor = self.basic_installed_dict[type]
                    production_MW = row[key] * installed_factor
                total_basic_production_MW += production_MW
                total_hourly_cost_EUR += self.basic_source_opex_per_mwh_eur[type] * production_MW

            # Production and variable costs from flexible sources.
            total_flexible_production_MW = 0
            for index, flexible_source in enumerate(self.grid.flexible_sources):
                flexible_production_MW = self.flexible_production_MW_list[index][t]
                flexible_el_production_MW = flexible_production_MW
                total_hourly_cost_EUR += \
                    self.flexible_opex_per_mwh_eur[index] * flexible_production_MW

                if self.optimize_heat and flexible_source.heat is not None:
                    turbine = flexible_source.heat
                    # Decrease electricity production based on steam extraction. Costs however
                    # correspond to the whole production (above).
                    # For back-pressure turbines, costs are based on electricity production only
                    # (appropriate el. efficiency needs to be provided, lower than e.g. for
                    # condensing turbines).
                    if isinstance(turbine, ExtractionTurbine):
                        flexible_heat_production_MW = self.flexible_heat_production_MW_list[index][t]
                        flexible_el_production_MW -= self._decrease_extraction_turbine_el_production(
                            turbine, flexible_production_MW, flexible_heat_production_MW)
                    total_heat_supply_MW += self.flexible_heat_production_MW_list[index][t]

                total_flexible_production_MW += flexible_el_production_MW
                flexible_production_el_eq_sum_MWh_list[index] += flexible_production_MW

            total_supply_MW = total_basic_production_MW + total_flexible_production_MW

            # Inflow / outflow and variable costs from grid storage.
            for index, storage_type in enumerate(self.grid.storage):
                discharging_MW: LpVariable = self.storage_discharging_MW_list[index][t]
                charging_MW: LpVariable = self.storage_charging_MW_list[index][t]
                if (storage_type.use.is_electricity()
                        or storage_type.use == StorageUse.DEMAND_FLEXIBILITY):
                    total_supply_MW += discharging_MW - charging_MW
                elif storage_type.use == StorageUse.HEAT:
                    total_heat_supply_MW += discharging_MW - charging_MW
                else:
                    assert False, f"unsupported storage use {storage_type.use.name}"

                total_hourly_cost_EUR += \
                    self.storage_discharging_opex_per_mwh_eur[index] * discharging_MW
                if storage_type.separate_charging:
                    total_hourly_cost_EUR += \
                        self.storage_charging_opex_per_mwh_eur[index] * charging_MW

            # Inflow / outflow and variable costs from interconnectors.
            total_interconnector_inflow_MW = sum(
                self._get_inflow(inflow_MW[t], country_from)
                for country_from, inflow_MW in self.inflow.items()
            )
            total_interconnector_outflow_MW = sum(
                outflow_MW[t]
                for outflow_MW in self.outflow.values()
            )
            # Exporters pay for interconnection capacity in the European market.
            total_hourly_cost_EUR += \
                total_interconnector_outflow_MW * OUTFLOW_CAPACITY_COST_EUR_PER_MWH

            total_supply_MW += total_interconnector_inflow_MW - total_interconnector_outflow_MW

            total_demand_MW = row[Keys.LOAD]

            # Adequacy Constraint.
            # Here we assume that VRE generation can be arbitrarily curtailed so we allow
            # over-production. Ramp-down restrictions may cause slight over-production of flexible
            # generation with zero VRE generation.
            prob += total_supply_MW >= total_demand_MW, self._make_name("Adequacy", t)

            if self.optimize_heat:
                prob += total_heat_supply_MW == row[Keys.HEAT_DEMAND], self._make_name(
                    "AdequacyHeat", t)

            # Flexibility constraints for flexible basic sources.
            for type, source in self.grid.basic_sources.items():
                if isinstance(source, FlexibleBasicSource) and source.is_truly_flexible():
                    max_production_MW: float = row[get_basic_key(type)]
                    min_production_MW: float = source.min_production_mw
                    if source.max_decrease_mw < source.capacity_mw:
                        # Cap output ration at 1.0 (in case of inconsistent data when
                        # the actual, historical production is above net capacity).
                        current_output_ratio: float = min(
                            1.0, max_production_MW / source.capacity_mw)
                        min_production_MW_relative: float = max_production_MW - \
                            current_output_ratio * source.max_decrease_mw
                        min_production_MW = max(min_production_MW, min_production_MW_relative)

                    # Sources must respect capacity optimization. (When installed capacity of this
                    # type is decreased to 30 %, it's hourly production must respect that.)
                    installed_factor = self.basic_installed_dict[type]

                    flexible_basic_production_MW: LpVariable = \
                        self.flexible_basic_production_MW_dict[type][t]
                    if min_production_MW == max_production_MW:
                        prob += (
                            flexible_basic_production_MW == max_production_MW * installed_factor,
                            self._make_name("FlexibleBasicEqualsPredefined", t, type.value)
                        )
                    else:
                        assert min_production_MW < max_production_MW
                        prob += (
                            flexible_basic_production_MW <= max_production_MW * installed_factor,
                            self._make_name("FlexibleBasicBelowMax", t, type.value)
                        )
                        prob += (
                            flexible_basic_production_MW >= min_production_MW * installed_factor,
                            self._make_name("FlexibleBasicAboveMin", t, type.value)
                        )

            # Production for flexible sources and storage must respect capacity optimization.
            # (When installed capacity of a type is decreased to 30 %, it's hourly production must
            # respect that.)
            for index, flexible_source in enumerate(self.grid.flexible_sources):
                if flexible_source.min_capacity_mw < flexible_source.capacity_mw:
                    installed_factor = self.flexible_installed_list[index]
                    flexible_production_MW = self.flexible_production_MW_list[index]
                    prob += (
                        flexible_production_MW[t] <= flexible_source.capacity_mw * installed_factor,
                        self._make_name("FlexibleInstalled", t, index)
                    )

            # Keep track of available hydro capacities for implicit
            # reserves modelling.
            available_reserve_capacity_MW: Union[float, LpVariable] = 0.0

            for index, storage_type in enumerate(self.grid.storage):
                charging_MW = self.storage_charging_MW_list[index][t]
                discharging_MW = self.storage_discharging_MW_list[index][t]
                if storage_type.min_capacity_mw_charging < storage_type.capacity_mw_charging:
                    charging_installed_factor = self.storage_charging_installed_list[index]
                    prob += (
                        charging_MW <= storage_type.capacity_mw_charging * charging_installed_factor,
                        self._make_name("StorageChargingInstalled", t, index)
                    )
                if storage_type.max_capacity_mw_hourly_data_key is not None:
                    max_MW = row[storage_type.max_capacity_mw_hourly_data_key]
                    if storage_type.max_capacity_mw_factor is not None:
                        max_MW *= storage_type.max_capacity_mw_factor
                    prob += (
                        charging_MW <= max_MW,
                        self._make_name("StorageMaxCharging", t, index)
                    )
                    prob += (
                        discharging_MW <= max_MW,
                        self._make_name("StorageMaxDischarging", t, index)
                    )

                if storage_type.min_capacity_mw < storage_type.capacity_mw:
                    state_MWh = self.storage_state_MWh_list[index][t]
                    discharging_installed_factor = self.storage_discharging_installed_list[index]
                    discharging_installed_MW = storage_type.capacity_mw * discharging_installed_factor
                    prob += (
                        discharging_MW <= discharging_installed_MW,
                        self._make_name("StorageDischargingInstalled", t, index)
                    )
                    if storage_type.type.available_for_reserves:
                        available_reserve_capacity_MW += discharging_installed_MW - discharging_MW
                    # The limit on max_energy_mwh is derived from `discharging_installed`, quite
                    # arbitrarily.
                    max_energy_MWh = storage_type.max_energy_mwh
                    if storage_type.separate_charging:
                        max_energy_MWh *= self.grid.num_years
                    else:
                        max_energy_MWh *= discharging_installed_factor

                    prob += (
                        state_MWh <= max_energy_MWh,
                        self._make_name("StorageStateInstalled", t, index)
                    )
                elif storage_type.type.available_for_reserves:
                    available_reserve_capacity_MW += storage_type.capacity_mw - discharging_MW

            # Account for implicit modelling of balancing reserves
            # via hydropower capacity reduction.
            if self.grid.reserves and self.grid.reserves.hydro_capacity_reduction_mw > 0:
                required_reserve_capacity_MW = self.grid.reserves.hydro_capacity_reduction_mw

                if not available_reserve_capacity_MW and required_reserve_capacity_MW > 0:
                    raise ValueError(
                        f"No hydro reserve capacities available in {self.grid.country}, "
                        f"{required_reserve_capacity_MW} MW is required."
                    )

                prob += (
                    available_reserve_capacity_MW >= required_reserve_capacity_MW,
                    self._make_name("HydroReserveCapacity", t)
                )

            # Heat production constraints.
            if self.optimize_heat:
                for index, flexible_source in enumerate(self.grid.flexible_sources):
                    turbine = flexible_source.heat
                    heat_MW = self.flexible_heat_production_MW_list[index][t]
                    el_MW = self.flexible_production_MW_list[index][t]
                    if isinstance(turbine, BackPressureTurbine):
                        prob += (heat_MW == el_MW * turbine.ratio_heat_mw_per_el_mw,
                                 self._make_name("HeatBackPressure", t, index))
                    elif isinstance(turbine, ExtractionTurbine):
                        base_heat_ratio = turbine.base_ratio_heat_mw_per_el_mw
                        max_heat_variable_ratio = turbine.heat_mw_per_decreased_el_mw * \
                            (1 - turbine.min_ratio_el)
                        max_heat_ratio = base_heat_ratio + max_heat_variable_ratio
                        prob += (heat_MW >= el_MW * base_heat_ratio,
                                 self._make_name("HeatExtractionLower", t, index))
                        prob += (heat_MW <= el_MW * max_heat_ratio,
                                 self._make_name("HeatExtractionUpper", t, index))
                    elif isinstance(turbine, HeatRecoveryUnit):
                        # Heat production from a heat recovery unit can
                        # be zero - the waste exhaust heat can always
                        # just be let go.
                        prob += (heat_MW <= el_MW * turbine.max_heat_mw_per_el_mw,
                                 self._make_name("HeatRecoveryUpper", t, index))

            # Ramp-up and ramp-down rates for flexible sources.
            for index, flexible_source in enumerate(self.grid.flexible_sources):
                if flexible_source.ramp_rate < 1 and t > 0:
                    production_MW = self.flexible_production_MW_list[index]
                    ramp_up_MW = self.flexible_ramp_up_MW_list[index]
                    installed_factor = self.flexible_installed_list[index]
                    self._add_ramp_contraints(
                        prob, total_hourly_cost_EUR, flexible_source, production_MW, ramp_up_MW,
                        installed_factor, "Flexible", index, t)

            # Ramp-up and ramp-down rates for flexible basic sources.
            for type, source in self.grid.basic_sources.items():
                if isinstance(source, FlexibleBasicSource) and source.is_truly_flexible():
                    if source.ramp_rate < 1 and t > 0:
                        # Add context how the predefined curve evolves so that ramp up constraints
                        # can be relaxed to allow predefined change.
                        last_flexible_MW = self.last_flexible_basic_production_dict[type]
                        ramp_up_predefined_MW: float = row[get_basic_key(type)] - last_flexible_MW

                        production_MW = self.flexible_basic_production_MW_dict[type]
                        ramp_up_MW = self.flexible_basic_ramp_up_MW_dict[type]
                        installed_factor: LpVariable = self.basic_installed_dict[type]
                        self._add_ramp_contraints(
                            prob, total_hourly_cost_EUR, source, production_MW, ramp_up_MW,
                            installed_factor, "FlexibleBasic", type.value, t, ramp_up_predefined_MW)
                    self.last_flexible_basic_production_dict[type] = row[get_basic_key(type)]

            # Ramp-up and ramp-down rates for storage types.
            for index, storage_type in enumerate(self.grid.storage):
                if storage_type.ramp_rate < 1 and t > 0:
                    # Assuming no capex optimization here for simplicity (could be expanded, if needed).
                    assert storage_type.capacity_mw_charging == storage_type.min_capacity_mw_charging
                    assert storage_type.capacity_mw == storage_type.min_capacity_mw
                    max_ramp_MW: float = storage_type.ramp_rate * \
                        (storage_type.capacity_mw + storage_type.capacity_mw_charging)

                    discharging = self.storage_discharging_MW_list[index]
                    charging = self.storage_charging_MW_list[index]
                    before_out = charging[t - 1] - discharging[t - 1]
                    now_out = charging[t] - discharging[t]
                    prob += (before_out - max_ramp_MW <= now_out,
                             self._make_name("StorageRampLower", t, index))
                    prob += (before_out + max_ramp_MW >= now_out,
                             self._make_name("StorageRampUpper", t, index))

            # State constraints for storage.
            for index, storage_type in enumerate(self.grid.storage):
                if storage_type.use == StorageUse.HEAT and not self.optimize_heat:
                    continue
                state_MWh_list: list[LpVariable] = self.storage_state_MWh_list[index]
                charging_MW: LpVariable = self.storage_charging_MW_list[index][t]
                discharging_MW: LpVariable = self.storage_discharging_MW_list[index][t]
                installed_factor = self.storage_discharging_installed_list[index]

                # Figure out the previous state of the storage.
                if t > 0:
                    # Loss rate per hour.
                    keep_rate_day = 1 - storage_type.loss_rate_per_day
                    keep_rate_hour = keep_rate_day ** (1/24)
                    previous_state_MWh = state_MWh_list[t - 1] * keep_rate_hour
                else:
                    previous_state_MWh = storage_type.initial_energy_mwh
                    if storage_type.separate_charging:
                        previous_state_MWh *= self.grid.num_years
                    else:
                        previous_state_MWh *= installed_factor

                inflow_MW = 0
                if storage_type.inflow_hourly_data_key:
                    inflow_MW = row[storage_type.inflow_hourly_data_key]

                state_discharging_MW = (1 / storage_type.discharging_efficiency) * discharging_MW
                net_charging_MW = storage_type.charging_efficiency * charging_MW - state_discharging_MW
                # Constant use per hour.
                use_MW = (storage_type.use_mwh_per_day / 24) * installed_factor
                net_inflow_MW = inflow_MW - use_MW

                # In all cases, allow spilling of power (new state <= old state + ...).
                if storage_type.max_energy_mwh == 0:
                    # Special case - no storage capacity.
                    prob += (discharging_MW <= net_inflow_MW,
                             self._make_name("StorageStateTransitionNoStorage", t, index))
                elif storage_type.capacity_mw_charging == 0:
                    # Special case - no charging capacity.
                    prob += (
                        state_MWh_list[t] <= previous_state_MWh +
                        net_inflow_MW - state_discharging_MW,
                        self._make_name("StorageStateTransition", t, index),
                    )
                else:
                    # General case.
                    prob += (
                        state_MWh_list[t] <= previous_state_MWh + net_inflow_MW + net_charging_MW,
                        self._make_name("StorageStateTransition", t, index),
                    )

                if inflow_MW > 0 and storage_type.inflow_min_discharge_ratio:
                    # Specifying inflow_min_discharge_ratio does not work with capex optimization.
                    assert storage_type.capacity_mw == storage_type.min_capacity_mw
                    min_production = inflow_MW * storage_type.inflow_min_discharge_ratio
                    prob += (
                        state_discharging_MW >= min(min_production, storage_type.capacity_mw),
                        self._make_name("StorageInflowMinDischargeRatio", t, index),
                    )

                if storage_type.midnight_energy_mwh is not None and self._is_midnight(t):
                    midnight_energy_mwh = storage_type.midnight_energy_mwh
                    if storage_type.separate_charging:
                        midnight_energy_mwh *= self.grid.num_years
                    else:
                        midnight_energy_mwh *= installed_factor
                    prob += (state_MWh_list[t] == midnight_energy_mwh,
                             self._make_name("MidnightState", t, index))

                if t + 1 == len(self.grid.data.index):
                    min_final_energy_mwh = storage_type.min_final_energy_mwh
                    final_energy_mwh = storage_type.final_energy_mwh
                    if storage_type.separate_charging:
                        min_final_energy_mwh *= self.grid.num_years
                        final_energy_mwh *= self.grid.num_years
                    else:
                        min_final_energy_mwh *= installed_factor
                        final_energy_mwh *= installed_factor

                    # Use `min_final_energy_mwh` as a strict bound whereas `final_energy_mwh` is
                    # used as a reference for profit / costs from selling excess energy / buying
                    # missing energy (e.g. hydrogen).
                    prob += (state_MWh_list[t] >= min_final_energy_mwh,
                             self._make_name("FinalCharge", index))

                    # Substract gains from extra state of charge (e.g. selling hydrogen) / add costs
                    # from missing state of charge (e.g. buying imported hydrogen).
                    extra_state_mwh = state_MWh_list[t] - final_energy_mwh
                    total_hourly_cost_EUR -= extra_state_mwh * storage_type.cost_sell_buy_mwh_eur

            # Objective.
            objective += total_hourly_cost_EUR

        # Constrain total production of flexible sources. In the case
        # of CHP, this constrains the total electricity-equivalent
        # production.
        for index, flexible_source in enumerate(self.grid.flexible_sources):
            flexible_production_el_eq_MWh_sum = flexible_production_el_eq_sum_MWh_list[index]
            if isinstance(flexible_source.constraint, CapacityFactorConstraint):
                # Cap production to satisfy the constraint on maximum average
                # capacity factor.
                max_capacity_factor = flexible_source.constraint.max_capacity_factor
                max_total_twh = (
                    self.grid.num_years * flexible_source.capacity_mw * max_capacity_factor
                    * 8760 / 1e6
                )
                # Scale max production accordingly with capex optimization.
                max_total_twh_scaled = (
                    max_total_twh * self.flexible_installed_list[index]
                )
                prob += (
                    flexible_production_el_eq_MWh_sum / 1e6 <= max_total_twh_scaled,
                    self._make_name("MaxCapFactor", index)
                )
            elif isinstance(flexible_source.constraint, ProductionConstraint):
                # Cap total production in absolute terms.
                max_total_twh = self.grid.num_years * flexible_source.constraint.max_total_twh
                prob += (
                    flexible_production_el_eq_MWh_sum / 1e6 <= max_total_twh,
                    self._make_name("MaxTotalProduction", index)
                )

        # Add fixed costs for different sources to total cost.
        for type, source in self.grid.basic_sources.items():
            installed_mw_expression = source.capacity_mw * self.basic_installed_dict[type]
            objective += get_source_capex_per_year_eur_with_capacity(
                source, installed_mw_expression) * self.grid.num_years
        for index, flexible_source in enumerate(self.grid.flexible_sources):
            if not flexible_source.virtual:
                installed_mw_expression = flexible_source.capacity_mw * \
                    self.flexible_installed_list[index]
                objective += get_source_capex_per_year_eur_with_capacity(
                    flexible_source, installed_mw_expression) * self.grid.num_years
            # TODO: Pay for heat not served (EENS is a virtual source with
            # an unlimited extraction turbine, but costs are only incurred
            # for the electricity it "generates").
        for index, storage_type in enumerate(self.grid.storage):
            discharging_mw_expression = storage_type.capacity_mw * \
                self.storage_discharging_installed_list[index]
            charging_mw_expression = storage_type.capacity_mw_charging * \
                self.storage_charging_installed_list[index]
            objective += get_storage_capex_per_year_eur_with_capacities(
                storage_type,
                discharging_mw_expression, charging_mw_expression) * self.grid.num_years

    def remove_nonnumeric_data(self) -> None:
        # All other columns are numeric, drop this column after storing it so that the invariant is
        # satisfied.
        if Keys.PRICE_TYPE in self.grid.data:
            self.grid.data = self.grid.data.drop(Keys.PRICE_TYPE, axis=1)

    def load_solution(self, csv_filename: Path) -> None:
        self.grid.data = pd.read_csv(csv_filename, index_col='Date', parse_dates=True)

    def store_solution(self, csv_filename: Path) -> None:
        self.grid.data.to_csv(csv_filename)

    def extract_solution(self) -> None:
        data = {}
        rows = len(self.grid.data.index)

        total_flexible = np.zeros(rows)
        total_flexible_el_eq = np.zeros(rows)
        total_flexible_heat = np.zeros(rows)
        for index, flexible_source in enumerate(self.grid.flexible_sources):
            flexible = _extract_values(self.flexible_production_MW_list[index])

            if flexible_source.heat is not None:
                flexible_el_eq = _extract_values(self.flexible_production_MW_list[index])
                total_flexible_el_eq += flexible_el_eq
                data[get_flexible_electricity_equivalent_key(flexible_source)] = flexible_el_eq

                if self.optimize_heat:
                    flexible_heat = _extract_values(self.flexible_heat_production_MW_list[index])
                    total_flexible_heat += flexible_heat
                    data[get_flexible_heat_key(flexible_source)] = flexible_heat

                    if isinstance(flexible_source.heat, ExtractionTurbine):
                        turbine = flexible_source.heat
                        flexible -= self._decrease_extraction_turbine_el_production(
                            turbine, flexible, flexible_heat)

            # Store ramp-up generation so that we can recalculate exact
            # ramp-up costs later.
            if self.optimize_ramp_up_costs and self.flexible_ramp_up_MW_list[index]:
                data[get_ramp_up_key(flexible_source.type)] = \
                    _extract_values(self.flexible_ramp_up_MW_list[index])

            total_flexible += flexible
            data[get_flexible_key(flexible_source)] = flexible

        data["Flexible"] = total_flexible
        if self.optimize_heat:
            data["Electricity_Equivalent_Flexible"] = total_flexible_el_eq
            data[Keys.HEAT_FLEXIBLE_PRODUCTION] = total_flexible_heat

        total_charging = np.zeros(rows)
        total_discharging = np.zeros(rows)
        load_shift = np.zeros(rows)
        has_load_shift = False

        for index, storage_type in enumerate(self.grid.storage):
            charging = _extract_values(self.storage_charging_MW_list[index])
            discharging = _extract_values(self.storage_discharging_MW_list[index])
            state = _extract_values(self.storage_state_MWh_list[index])

            data[get_charging_key(storage_type)] = charging
            data[get_discharging_key(storage_type)] = discharging
            data[get_state_of_charge_key(storage_type)] = state

            if storage_type.use == StorageUse.DEMAND_FLEXIBILITY:
                load_shift += charging - discharging
                has_load_shift = True
            # Only include electricity storage in the totals.
            elif storage_type.use.is_electricity():
                total_charging += charging
                total_discharging += discharging

        data["Charging"] = total_charging
        data["Discharging"] = total_discharging
        data["Load_Shift"] = load_shift

        total_import = np.zeros(rows)
        total_export = np.zeros(rows)
        net_import = np.zeros(rows)
        for country_from, inflow_MW in self.inflow.items():
            import_country = self._get_inflow(_extract_values(inflow_MW), country_from)
            total_import += import_country
            net_import += import_country
            data[get_import_key(country_from)] = import_country
        for country_to, outflow_MW in self.outflow.items():
            export_country = _extract_values(outflow_MW)
            total_export += export_country
            net_import -= export_country
            data[get_export_key(country_to)] = export_country
        data[Keys.IMPORT] = total_import
        data[Keys.EXPORT] = total_export
        data[Keys.NET_IMPORT] = data[Keys.IMPORT] - data[Keys.EXPORT]

        df = pd.DataFrame(
            data=data,
            index=self.grid.data.index,
        )
        df = df.join(self.grid.data, how="inner")

        # Scale basic production according to computed installed
        # capacities and extract flexible basic production values.
        for type, source in self.grid.basic_sources.items():
            key = get_basic_key(type)
            if isinstance(source, FlexibleBasicSource) and source.is_truly_flexible():
                df[get_flexible_basic_predefined_key(type)] = df[key]
                df[key] = _extract_values(
                    self.flexible_basic_production_MW_dict[type])
                df[get_flexible_basic_decrease_key(type)] = (
                    df[get_flexible_basic_predefined_key(type)] - df[key])

                # Store ramp-up generation so that we can recalculate
                # exact ramp-up costs later.
                if self.optimize_ramp_up_costs and type in self.flexible_basic_ramp_up_MW_dict:
                    df[get_ramp_up_key(type)] = \
                        _extract_values(self.flexible_basic_ramp_up_MW_dict[type])
            else:
                installed_factor = self.basic_installed_dict[type].varValue
                df[key] *= installed_factor

        if has_load_shift:
            df[Keys.LOAD_BEFORE_FLEXIBILITY] = df[Keys.LOAD]
            df[Keys.LOAD] += load_shift

        df[Keys.WIND] = df[Keys.WIND_ONSHORE] + df[Keys.WIND_OFFSHORE]
        df["VRE"] = df[Keys.SOLAR] + df[Keys.WIND]
        df["Residual"] = df[Keys.LOAD] - df["VRE"]
        df[Keys.PRODUCTION] = df["VRE"] + df[Keys.HYDRO] + df[Keys.NUCLEAR] + df["Flexible"]
        df["Total_Without_Storage"] = df[Keys.PRODUCTION] + df[Keys.NET_IMPORT]
        df["Total"] = df["Total_Without_Storage"] - df["Charging"] + df["Discharging"]
        df['Storable'] = df["Total_Without_Storage"] - df[Keys.LOAD]
        df["Curtailment"] = df["Total"] - df[Keys.LOAD]
        df['Shortage'] = df[Keys.LOAD] - df['Total']

        self.grid.data = df

    def extract_factors(self) -> None:
        for type, source in self.grid.basic_sources.items():
            installed_factor = self.basic_installed_dict[type].varValue
            source.capacity_mw *= installed_factor

        for index, flexible_source in enumerate(self.grid.flexible_sources):
            installed_factor = self.flexible_installed_list[index].varValue
            flexible_source.capacity_mw *= installed_factor

        for index, storage_type in enumerate(self.grid.storage):
            discharging_installed_factor = self.storage_discharging_installed_list[index].varValue
            storage_type.capacity_mw *= discharging_installed_factor
            if storage_type.separate_charging:
                charging_installed_factor = self.storage_charging_installed_list[index].varValue
                storage_type.capacity_mw_charging *= charging_installed_factor
            else:
                storage_type.capacity_mw_charging *= discharging_installed_factor
            # Scale down all the storage capacities that depend on installed factor.
            if not storage_type.separate_charging:
                storage_type.initial_energy_mwh *= discharging_installed_factor
                storage_type.max_energy_mwh *= discharging_installed_factor
                storage_type.final_energy_mwh *= discharging_installed_factor
                storage_type.min_final_energy_mwh *= discharging_installed_factor
                if storage_type.midnight_energy_mwh:
                    storage_type.midnight_energy_mwh *= discharging_installed_factor


def grids_from_problems(problems: dict[Region, CountryProblem]) -> dict[Region, CountryGrid]:
    return {country: problem.grid for country, problem in problems.items()}


class GridOptimization:
    def __init__(
        self,
        problems: dict[Region, CountryProblem],
        interconnectors: Interconnectors,
        out_dir: Path,
        include_transmission_loss_in_price: bool = False,
        # TODO: Replace by a more robust solution. This assumes exactly same grids params as when
        # optimizing the previous solution. This also does not load optimized capacities if
        # `optimize_capex` parameter was used in the previous solution (as those are not persisted).
        load_previous_solution: bool = False,
        store_model: bool = True,
        preferred_solver: Optional[Solver] = None,
        solver_timeout_minutes: Optional[int] = None,
        solver_shift_ipm_termination_by_orders: int = 0
    ) -> None:
        self.problems = problems
        self.interconnectors = interconnectors
        self.out_dir = out_dir
        self.include_transmission_loss_in_price = include_transmission_loss_in_price
        self.load_previous_solution = load_previous_solution
        self.store_model = store_model
        self.preferred_solver = preferred_solver
        self.solver_timeout_minutes = solver_timeout_minutes
        self.solver_shift_ipm_termination_by_orders = solver_shift_ipm_termination_by_orders

    def _get_union_index(self) -> pd.Index:
        index = None
        for country, problem in self.problems.items():
            if index is None:
                index = problem.grid.data.index
            else:
                index = index.join(problem.grid.data.index, how="outer")
            assert index.size > 0, "grid for {country} must be non-empty"
        return pd.Index() if index is None else index

    def _make_list_variables(self,
                             prefix: str,
                             count: int,
                             **kwargs) -> list[LpVariable]:
        return [LpVariable(self._make_name(prefix, t), **kwargs) for t in range(count)]

    def _make_name(self, name: str, index: int) -> str:
        return f"{name}_{index}"

    def _estimate_spot_prices(self) -> None:
        union_index = self._get_union_index()
        estimators: dict[Region, CountryGridSpotPriceEstimator] = {}
        for country, problem in self.problems.items():
            estimators[country] = CountryGridSpotPriceEstimator(problem.grid)

        export_flow = ExportFlow(self.interconnectors, grids_from_problems(self.problems),
                                 self.include_transmission_loss_in_price)
        for i in union_index:
            # One pass to estimate spot prices from generation and
            # import prices.
            for country in export_flow.get_order(i):
                problem = self.problems[country]
                estimator = estimators[country]
                import_price = export_flow.get_import_price(country, i)
                price, marginal_type = estimator.estimate_spot_price(problem.grid.data.loc[i],
                                                                     import_price)
                problem.grid.data.loc[i, Keys.PRICE] = price
                problem.grid.data.loc[i, Keys.PRICE_IMPORT] = import_price
                problem.grid.data.loc[i, Keys.PRICE_TYPE] = marginal_type

            # One more pass to estimate export prices.
            for country, problem in self.problems.items():
                export_price = export_flow.get_export_price(country, i)
                problem.grid.data.loc[i, Keys.PRICE_EXPORT] = export_price

        # Possibly increase prices by charging.
        for country, problem in self.problems.items():
            estimator = estimators[country]
            storage_average_margin_per_mwh = estimator.compute_storage_average_margin_per_mwh()

            for i in union_index:
                row = problem.grid.data.loc[i]
                price = row[Keys.PRICE]
                marginal_type = row[Keys.PRICE_TYPE]
                max_price, max_marginal_type = estimator.estimate_spot_price_with_charging(
                    row, (price, marginal_type), storage_average_margin_per_mwh)
                problem.grid.data.loc[i, Keys.PRICE] = max_price
                problem.grid.data.loc[i, Keys.PRICE_TYPE] = max_marginal_type

    def optimize(self) -> bool:
        if self.load_previous_solution:
            for problem in self.problems.values():
                problem.load_solution(self.out_dir / f"{problem.grid.country}.csv")
                problem.remove_nonnumeric_data()

            print(f"Loaded previous solution from {self.out_dir} files")
            return True

        print("Constructing the problem...", end=" ")
        construction_start_time = time.monotonic()

        prob = LpProblem("MinimizeGridOperationCosts", LpMinimize)

        # Flows from and to a given country.
        interconnector_outflow_dict: dict[Region, dict[Region, list[LpVariable]]] = {}
        interconnector_inflow_dict: dict[Region, dict[Region, list[LpVariable]]] = {}
        interconnector_inflow_loss_dict: dict[Region, dict[Region, float]] = {}

        # Take a joint index for all data frames.
        union_index = self._get_union_index()
        # Enlarge all country data frames to the union index (and fill NA with zeros).
        empty_df = pd.DataFrame(index=union_index)
        for country, problem in self.problems.items():
            # Backfill gaps of up to 4 hours, fill larger gaps with zeroes.
            problem.grid.data = empty_df.join(
                problem.grid.data, how="left").bfill(limit=4).fillna(0)

        for country in self.problems.keys():
            interconnector_outflow_dict[country] = {}
            interconnector_inflow_dict[country] = {}
            interconnector_inflow_loss_dict[country] = {}

        num_steps = union_index.size
        for country_from, to_dict in self.interconnectors.from_to.items():
            for country_to, interconnector in to_dict.items():
                if interconnector.capacity_mw > 0:
                    prefix = f"flow_{country_from}_{country_to}_MW"
                    flow: list[LpVariable] = self._make_list_variables(
                        prefix, num_steps, lowBound=0, upBound=interconnector.capacity_mw)
                    interconnector_outflow_dict[country_from][country_to] = flow
                    interconnector_inflow_dict[country_to][country_from] = flow
                    interconnector_inflow_loss_dict[country_to][country_from] = interconnector.loss

        objective: LpAffineExpression = LpAffineExpression(0)
        for problem in self.problems.values():
            country = problem.grid.country
            problem.construct_country_problem(prob,
                                              objective,
                                              interconnector_outflow_dict[country],
                                              interconnector_inflow_dict[country],
                                              interconnector_inflow_loss_dict[country])

        prob += objective, "Total cost of running the system in EUR"

        construction_end_time = time.monotonic()
        construction_mins = (construction_end_time - construction_start_time) / 60
        print(f"Construction took {construction_mins:.1f} mins")

        if self.store_model:
            lp_filename = self.out_dir / "model.lp"
            prob.writeLP(lp_filename)
            print(f"Constructed and stored to {lp_filename}")
        else:
            print(f"Constructed (without saving)")

        print("Solving the problem...", end=" ")
        solving_start_time = time.monotonic()

        solve_problem(prob, self.preferred_solver, self.solver_timeout_minutes,
                      self.solver_shift_ipm_termination_by_orders)
        if LpStatus[prob.status] != "Optimal":
            print(
                "Problem could not be solved! The problem is {}.".format(
                    LpStatus[prob.status]
                )
            )
            return False

        solving_end_time = time.monotonic()
        solving_mins = (solving_end_time - solving_start_time) / 60
        print(f"Solving took {solving_mins:.1f} mins")
        print("Solved with optimal cost={:,}".format(value(prob.objective)))
        print("Gathering resulting dataframe...", end=" ")

        for problem in self.problems.values():
            problem.extract_solution()
            problem.extract_factors()

        print("Estimating spot prices...", end=" ")
        self._estimate_spot_prices()
        print(f"Done")

        print("Printing output CSVs...", end=" ")
        for problem in self.problems.values():
            problem.store_solution(self.out_dir / f"{problem.grid.country}.csv")
            problem.remove_nonnumeric_data()

        print(f"Done and saved to {self.out_dir} files")
        return True
