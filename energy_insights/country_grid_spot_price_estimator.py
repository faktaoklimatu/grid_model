"""
Provides estimates of grid spot prices.
"""

import math

import pandas as pd

from .grid_capex_utils import *
from .country_grid import CountryGrid
from .grid_plot_utils import (
    Keys,
    has_curtailment,
    has_excess,
    get_basic_key,
    get_charging_key,
    get_discharging_key,
    get_flexible_key,
)
from .sources.storage import StorageType, StorageUse, Storage


class CountryGridSpotPriceEstimator:
    def __init__(
        self,
        grid: CountryGrid
    ) -> None:
        self.grid = grid

    def _maybe_update_price(self,
                            row: pd.Series,
                            current: tuple[float, str],
                            candidate: tuple[float, str],
                            max: bool = True) -> tuple[float, str]:
        def should_update(candidate_price: float, current_price: float) -> bool:
            if max:
                return candidate_price > current_price
            return candidate_price < current_price

        price, key = candidate
        if row[key] > 0 and should_update(price, current[0]):
            return candidate
        return current

    def estimate_spot_price(self, row: pd.Series, import_price: float) -> tuple[float, str]:
        maximum: tuple[float, str] = 0, "Curtailment"
        # No discussion about positive price if there is non-negligible curtailment.
        if has_curtailment(row):
            return maximum

        # Basic or flexible sources do not dictate the price if there is excess, this is dictated
        # by demand side.
        minimum_storage_price = 5
        if has_excess(row):
            return minimum_storage_price, "Charging_min"

        maximum = self._maybe_update_price(row, maximum, (import_price, Keys.NET_IMPORT))

        min_flexible_price = min(
            {source.economics.variable_costs_per_mwh_eur for source in self.grid.flexible_sources})

        for type, source in self.grid.basic_sources.items():
            maximum = self._maybe_update_price(
                row, maximum, (source.economics.variable_costs_per_mwh_eur, get_basic_key(type)))
        for source in self.grid.flexible_sources:
            # TODO: Add ramp up costs.
            maximum = self._maybe_update_price(
                row, maximum, (source.economics.variable_costs_per_mwh_eur, get_flexible_key(source)))

        for storage in self.grid.storage:
            if storage.use.is_electricity():
                price = min_flexible_price

                # If this storage cannot charge (but can buy energy, such as H2), adjust it's
                # selling bid price accordingly.
                if storage.capacity_mw_charging == 0 and storage.cost_sell_buy_mwh_eur > 0:
                    variable_cost_per_mwh_eur: float = storage.cost_sell_buy_mwh_eur / storage.discharging_efficiency
                    price = max(price, variable_cost_per_mwh_eur)

                maximum = self._maybe_update_price(
                    row, maximum, (price, get_discharging_key(storage)))

        # Assume storage discharging always bids at zero and gets remuneration from much higher
        # closing price.
        return maximum

    def estimate_spot_price_with_charging(
            self,
            row: pd.Series,
            maximum: tuple[float, str],
            storage_average_margin_per_mwh: dict[StorageType, float]) -> tuple[float, str]:
        # No discussion about positive price if there is non-negligible curtailment.
        if has_curtailment(row):
            return maximum

        def get_buy_price(storage: Storage) -> float:
            margin = storage_average_margin_per_mwh[storage.type]
            return max(0, margin)

        # Search for the _minimum_ price of used storage and update max if this minimum is above max.
        min: tuple[float, str] = math.inf, "Infty"
        for storage in self.grid.storage:
            if storage.use == StorageUse.ELECTRICITY:
                buy_price = get_buy_price(storage)
                min = self._maybe_update_price(
                    row, min, (buy_price, get_charging_key(storage)), max=False)
        if min[0] < math.inf:
            maximum = self._maybe_update_price(row, maximum, min)

        return maximum

    def compute_storage_average_margin_per_mwh(self) -> dict[StorageType, float]:
        def compute_margin(storage: Storage) -> float:
            total_discharging_mwh = self.grid.data[get_discharging_key(storage)].sum()
            if total_discharging_mwh == 0:
                return 0

            sell_eur = self.grid.data[Keys.PRICE] * self.grid.data[get_discharging_key(storage)]
            price_per_mwh_eur = sell_eur.sum() / total_discharging_mwh

            # Opex for discharging is composed of opex for charging (increased by losses) and opex
            # for discharging.
            total_charging_mwh = self.grid.data[get_charging_key(storage)].sum()
            charging_opex_per_mwh_eur = get_charging_opex_per_mwh_eur(storage, total_charging_mwh)
            round_trip_efficiency = storage.charging_efficiency * storage.discharging_efficiency
            opex_per_mwh_eur = charging_opex_per_mwh_eur / round_trip_efficiency
            discharging_opex_per_mwh_eur = get_discharging_opex_per_mwh_eur(
                storage, total_discharging_mwh)
            opex_per_mwh_eur += discharging_opex_per_mwh_eur

            margin = (price_per_mwh_eur - opex_per_mwh_eur) * round_trip_efficiency
            return margin

        return {storage.type: compute_margin(storage)
                for storage in self.grid.storage if storage.use.is_electricity()}
