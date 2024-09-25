"""
Provides structures for grid storage facilities.
"""

from copy import deepcopy
from dataclasses import dataclass, fields
from enum import Enum, unique
from typing import Optional
from warnings import warn

from numpy import average

from ..sources.basic_source import Source, fix_source_params
from ..sources.economics import SourceEconomics, extract_economics_params


class StorageUse(Enum):
    # Standard storage for electricity.
    ELECTRICITY = 1
    # Storage for electricity that pretends to be a basic source (in plots).
    ELECTRICITY_AS_BASIC = 2
    # Whether this models demand flexibility and should only alter the plotted demand curve.
    DEMAND_FLEXIBILITY = 3
    # Storage for heat (in district heating systems).
    HEAT = 4

    def is_electricity(self) -> bool:
        return self == StorageUse.ELECTRICITY or self == StorageUse.ELECTRICITY_AS_BASIC


@unique
class StorageType(Enum):
    DSR = "dsr"
    """Demand-side response."""
    HYDRO_FLEX = "hydro-flex"
    """Hydro flexibility."""
    HEAT = "heat"
    """Heat storage."""
    HEAT_FLEX = "heat-flex"
    """Flexibility of heat pump electricity demand (both heating and cooling)."""
    HYDROGEN = "h2"
    """Hydrogen-based storage."""
    HYDROGEN_PEAK = "h2p"
    """Hydrogen-based storage with OCGT turbines."""
    LI = "li"
    """Generic lithium-ion batteries."""
    LI_2H = "li-2"
    """2-hour lithium-ion batteries."""
    LI_4H = "li-4"
    """4-hour lithium-ion batteries."""
    VEHICLE_TO_GRID_50KWH_11KW = "v2g11"
    """Vehicle to grid with 50 kWh batteries and 11 kW slow charging."""
    VEHICLE_TO_GRID_50KWH_3KW = "v2g"
    """Vehicle to grid with 50 kWh batteries and 3 kW slow charging."""
    SMART_CHARGING_50KWH_3KW = "ecars"
    """Vehicle to grid with 50 kWh batteries and 3 kW slow charging."""
    PONDAGE = "h_pond"
    """Pondage hydro power, typically a turbine on a river with a small
    reservoir upstream for short-term storage. Lies conceptually between
    a reservoir and run-of-river hydro."""
    PUMPED = "pump"
    """Pumped hydro power, closed-loop (can only be charged by drawing power)."""
    PUMPED_OPEN = "pump_open"
    """Pumped hydro power, open-loop (allows for river inflows)."""
    RESERVOIR = "h_dams"
    """Reservoir hydro power, typically a large reservoir with a turbine
    for flexible power output."""
    ROR = "h_ror"
    """Run-of-river hydro power, typically a smaller turbine with no
    storage capacity and little flexibility."""

    @property
    def available_for_reserves(self) -> bool:
        return (
            self == StorageType.PONDAGE
            or self == StorageType.PUMPED
            or self == StorageType.PUMPED_OPEN
            or self == StorageType.RESERVOIR
            or self == StorageType.ROR
        )


@dataclass
class Storage(Source):
    type: StorageType
    # Use for this device.
    use: StorageUse
    capacity_mw_charging: float
    min_capacity_mw_charging: float
    # Charging capacity that is considered already paid off. Must be lower or equal to the minimal
    # charging capacity of this storage. This means that paid off capacity does not influence
    # optimization, it only decreases total system costs (for this storage and overall).
    paid_off_capacity_mw_charging: float
    # Capacity for charging is enforced to at least this ratio of the sum of capacity of solar,
    # onshore, offshore. Must be non-negative, often will be close to zero (such as 0.1).
    min_charging_capacity_ratio_to_VRE: float
    # Not enforced in the LP, only used for statistics.
    separate_charging: Optional[SourceEconomics]

    # Various bounds for capacity and state of the storage.
    # If the storage has separate charging, these bounds are multiplied by number of weather years
    # so that yearly required outflow or allowed inflow of storage (such as on hydrogen market),
    # captured by `final_energy_mwh` or `min_final_energy_mwh` get scaled to number of years.
    # If the storage has not separate charging, all these bounds depend on capacity (get decreased
    # with capex optimization).
    # TODO: This distinction is a bit arbitrary, maybe make it somehow clearer?
    max_energy_mwh: float
    initial_energy_mwh: float
    # The ideal final energy. Ending up with more results in financial gains, ending up with less
    # (if allowed by `min_final_energy_mwh`) results in further costs.
    final_energy_mwh: float
    # The strict lower limit for final energy of the storage.
    min_final_energy_mwh: float
    # If provided, the energy in this storage type must be equal to the value _every midnight_.
    midnight_energy_mwh: Optional[float]

    charging_efficiency: float
    discharging_efficiency: float
    # Only applies to ELECTRICITY StorageUse. If non-zero, it generates heat (potentially next to
    # electricity).
    discharging_efficiency_thermal: float
    # Loss of state of charge per day (as a ratio of current charge).
    loss_rate_per_day: float
    # Constant use of charge (useful for e-mobility). Depends on capacity (gets decreased with capex
    # optimization).
    use_mwh_per_day: float
    # Bonus in optimization for every MWh of extra energy above `final_energy_mwh` or malus for
    # every MWh of energy missing to `final_energy_mwh` (if allowed by `min_final_energy_mwh`).
    cost_sell_buy_mwh_eur: float
    # Power (expressed as ratio of `capacity_mw + capacity_mw_charging`) by which the current
    # (charging - discharging) can change up or down in one hour.
    ramp_rate: float
    # Optional inflow into the storage (independent of charging) specified as a string key of hourly
    # inflow data (in MW).
    inflow_hourly_data_key: Optional[str]
    # Minimal ratio of inflow that must be directly discharged in the given hour. Only has effect if
    # `inflow_hourly_data_key` is specified.
    inflow_min_discharge_ratio: Optional[float]
    # Optional additional charging/discharging capacity limit, specific for each modeled hour
    # (specified as a string key of the hourly data, in MW). Does not depend on capacity (i.e. does
    # not get decreased with capex optimization).
    max_capacity_mw_hourly_data_key: Optional[str]
    # A factor that is hourly data from `max_capacity_mw_hourly_data_key` multiplied by.
    max_capacity_mw_factor: Optional[float]

    def __add__(self, other):
        basic_source = Source.__add__(self, other)
        source_dict = {
            field.name: getattr(basic_source, field.name) for field in fields(Source)
        }

        assert self.use == other.use
        assert (self.midnight_energy_mwh is None) == (other.midnight_energy_mwh is None)
        assert self.separate_charging == other.separate_charging, "charging cost profiles must be same"
        assert self.loss_rate_per_day == other.loss_rate_per_day
        assert self.ramp_rate == other.ramp_rate, f"ramp rates differ for {self.type}"
        assert self.inflow_hourly_data_key == other.inflow_hourly_data_key, "inflows must be same"
        assert self.inflow_min_discharge_ratio == other.inflow_min_discharge_ratio
        assert self.max_capacity_mw_hourly_data_key == other.max_capacity_mw_hourly_data_key
        assert self.max_capacity_mw_factor == other.max_capacity_mw_factor

        # TODO: Consider reverting this to an assert after nuclear study is completed.
        if self.cost_sell_buy_mwh_eur != other.cost_sell_buy_mwh_eur:
            warn(f"different `cost_sell_buy_mwh_eur` values for {self.type}, picking"
                 "(randomly) one of the values, summary graphs will be wrong")

        if self.min_charging_capacity_ratio_to_VRE != other.min_charging_capacity_ratio_to_VRE:
            warn(f"different `min_charging_capacity_ratio_to_VRE` values for {self.type}, picking"
                 "(randomly) one of the values as this cannot get aggregated correctly")

        # Prevent division by zero in case both sides have zero capacity.
        if self.capacity_mw_charging > 0 or other.capacity_mw_charging > 0:
            charging_efficiency = average([self.charging_efficiency, other.charging_efficiency],
                                          weights=[self.capacity_mw_charging, other.capacity_mw_charging])
        else:
            assert self.capacity_mw_charging == 0 and other.capacity_mw_charging == 0, \
                f"storage charging capacities must both be zero for {self.type}"
            charging_efficiency = self.charging_efficiency

        if self.capacity_mw > 0 or other.capacity_mw > 0:
            discharging_efficiency = (
                average([self.discharging_efficiency, other.discharging_efficiency],
                        weights=[self.capacity_mw, other.capacity_mw])
            )
            discharging_efficiency_thermal = (
                average([self.discharging_efficiency_thermal,
                         other.discharging_efficiency_thermal],
                        weights=[self.capacity_mw, other.capacity_mw])
            )
        else:
            assert self.capacity_mw == 0 and other.capacity_mw == 0, \
                f"storage discharging capacities must both be zero for {self.type}"
            discharging_efficiency = self.discharging_efficiency
            discharging_efficiency_thermal = self.discharging_efficiency_thermal

        return Storage(
            **source_dict,
            use=self.use,
            capacity_mw_charging=self.capacity_mw_charging + other.capacity_mw_charging,
            min_capacity_mw_charging=self.min_capacity_mw_charging + other.min_capacity_mw_charging,
            paid_off_capacity_mw_charging=self.paid_off_capacity_mw_charging + other.paid_off_capacity_mw_charging,
            min_charging_capacity_ratio_to_VRE=self.min_charging_capacity_ratio_to_VRE,
            separate_charging=self.separate_charging,
            max_energy_mwh=self.max_energy_mwh + other.max_energy_mwh,
            initial_energy_mwh=self.initial_energy_mwh + other.initial_energy_mwh,
            final_energy_mwh=self.final_energy_mwh + other.final_energy_mwh,
            min_final_energy_mwh=self.min_final_energy_mwh + other.min_final_energy_mwh,
            midnight_energy_mwh=(
                self.midnight_energy_mwh + other.midnight_energy_mwh
                if self.midnight_energy_mwh is not None else None),
            charging_efficiency=charging_efficiency,
            discharging_efficiency=discharging_efficiency,
            discharging_efficiency_thermal=discharging_efficiency_thermal,
            loss_rate_per_day=self.loss_rate_per_day,
            use_mwh_per_day=self.use_mwh_per_day + other.use_mwh_per_day,
            cost_sell_buy_mwh_eur=self.cost_sell_buy_mwh_eur,
            ramp_rate=self.ramp_rate,
            inflow_hourly_data_key=self.inflow_hourly_data_key,
            inflow_min_discharge_ratio=self.inflow_min_discharge_ratio,
            max_capacity_mw_hourly_data_key=self.max_capacity_mw_hourly_data_key,
            max_capacity_mw_factor=self.max_capacity_mw_factor)


_storage: dict[str, list[dict]] = {}


def fix_storage_params(storage: dict):
    # Base charging / discharging capacities on nominal_mw.
    nominal_mw = storage.pop("nominal_mw", 0)
    storage.setdefault("capacity_mw", nominal_mw)
    storage.setdefault("capacity_mw_charging", nominal_mw)

    min_nominal_mw = storage.pop("min_nominal_mw", 0)
    storage.setdefault("min_capacity_mw", min_nominal_mw)
    storage.setdefault("min_capacity_mw_charging", min_nominal_mw)

    storage.setdefault("paid_off_capacity_mw_charging", 0)
    # Paid off capacity can't be above min capacity (it is not accounted for in optimization).
    assert storage["paid_off_capacity_mw_charging"] <= storage["min_capacity_mw_charging"], \
        f"{storage['type']} paid off charging capacity must be below min capacity for optimization"

    # Capacities need to be derived before `fix_source_params` because it sets the
    # (0) default for `min_capacity_mw`.
    storage = fix_source_params(storage)

    # Set trivial default values.
    storage.setdefault("min_charging_capacity_ratio_to_VRE", 0)
    assert storage["min_charging_capacity_ratio_to_VRE"] >= 0, "cannot force negative ratio"
    storage.setdefault("use", StorageUse.ELECTRICITY)
    storage.setdefault("separate_charging", None)
    storage.setdefault("loss_rate_per_day", 0)
    assert storage["loss_rate_per_day"] < 1, "cannot lose more than 100%"
    storage.setdefault("use_mwh_per_day", 0)

    storage.setdefault("initial_energy_mwh", 0)
    storage.setdefault("final_energy_mwh", 0)
    storage.setdefault("midnight_energy_mwh", None)

    storage.setdefault("cost_sell_buy_mwh_eur", 0)
    storage.setdefault("ramp_rate", 1)
    storage.setdefault("inflow_hourly_data_key", None)
    storage.setdefault("inflow_min_discharge_ratio", None)
    storage.setdefault("max_capacity_mw_hourly_data_key", None)
    storage.setdefault("max_capacity_mw_factor", None)

    storage.setdefault("discharging_efficiency_thermal", 0)

    # Unless explicitly specified, `min_final_energy_mwh` mirrors `final_energy_mwh`.
    storage.setdefault("min_final_energy_mwh", storage["final_energy_mwh"])

    # Base max_energy_mwh on max_energy_hours.
    max_energy_hours = storage.pop("max_energy_hours", None)
    if max_energy_hours is not None:
        nominal_mw_discharging = storage["capacity_mw"]
        storage["max_energy_mwh"] = nominal_mw_discharging * max_energy_hours

    initial_energy_ratio = storage.pop("initial_energy_ratio", None)
    if initial_energy_ratio is not None:
        storage["initial_energy_mwh"] = storage["max_energy_mwh"] * initial_energy_ratio

    # Construct separate charging cost profile.
    if storage["separate_charging"] is not None:
        economics_dict = extract_economics_params(storage["separate_charging"])
        economics = SourceEconomics(**economics_dict)
        storage["separate_charging"] = economics

    # Base overnight_costs_per_kw_eur on cost per kwh of max_energy and on (discharging) capacity.
    overnight_costs_per_kwh_eur = storage.pop("overnight_costs_per_kwh_eur", None)
    if overnight_costs_per_kwh_eur is not None:
        kwh_per_kw = storage["max_energy_mwh"] / storage["capacity_mw"]
        storage["overnight_costs_per_kw_eur"] = overnight_costs_per_kwh_eur * kwh_per_kw

    lifetime_cycles = storage.pop("lifetime_cycles", None)
    if lifetime_cycles is not None:
        max_energy_mwh = storage["max_energy_mwh"]
        discharging_mw = storage["capacity_mw"]
        discharging_efficiency = storage["discharging_efficiency"]
        draining_mw = discharging_mw / discharging_efficiency
        hours_for_full_cycle = max_energy_mwh / draining_mw
        storage["lifetime_hours"] = lifetime_cycles * hours_for_full_cycle

    return storage


def get_storage(storage):
    if isinstance(storage, str):
        source_list = deepcopy(_storage[storage])
    else:
        source_list = deepcopy(storage)

    def create_storage(params: dict) -> Storage:
        economics = SourceEconomics(**extract_economics_params(params))
        return Storage(economics=economics, **fix_storage_params(params))

    return [create_storage(item) for item in source_list]
