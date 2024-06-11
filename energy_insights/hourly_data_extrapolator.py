"""
Provides extrapolated entsoe hourly/15min data based on provided factors.
"""

import functools
import warnings
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
from scipy.stats import beta

from energy_insights.params_library.load_factors import LoadFactors

from .country_grid import CountryGrid
from .data_utils import scale_by_seasonal_factors
from .grid_plot_utils import Keys, get_basic_key
from .hourly_average import HourlyAverage
from .params_library.interconnectors import Interconnectors
from .region import Region, Zone, GREAT_BRITAIN
from .sources.basic_source import BasicSourceType, Source
from .sources.flexible_source import FlexibleSource


def _scale_up_pecd_series(pecd: pd.Series, normalization_factor: float) -> pd.Series:
    assert (pecd >= 0).all(), f"incorrect capacity factor series provided with min={pecd.min()}"
    assert (pecd <= 1).all(), f"incorrect capacity factor series provided with max={pecd.max()}"
    series = pecd
    sum = pecd.sum()
    desired_sum = sum * normalization_factor
    iteration = 0
    # Relative error of 1e-6 ends up with error of 1 MWh per 1 TWh of electricity produced.
    while (desired_sum - sum) / desired_sum > 1e-6:
        series = beta.cdf(series, a=1, b=normalization_factor)
        sum = series.sum()
        normalization_factor = desired_sum / sum
        iteration += 1
        if iteration >= 1000:
            break

    pecd = pd.Series(series, index=pecd.index)
    rel_error = (desired_sum - sum) / desired_sum
    assert np.abs(rel_error) < 1e-5, f"scaled up too off (with rel. error {rel_error * 100:.4f} %)"
    assert (pecd >= 0).all(), f"incorrect capacity factor series computed with min={pecd.min()}"
    assert (pecd <= 1).all(), f"incorrect capacity factor series computed with max={pecd.max()}"
    return pecd


def _normalize_pecd_series(pecd: pd.Series, normalization_factor: float, country: Zone) -> pd.Series:
    assert normalization_factor >= 0, "can't normalize to negative factor"
    if normalization_factor <= 1:
        return pecd * normalization_factor
    return _scale_up_pecd_series(pecd, normalization_factor)


def _scale_basic_production(df: pd.DataFrame,
                            pecd_data_map: dict[BasicSourceType, Optional[pd.Series]],
                            pecd_normalization_factors: dict[BasicSourceType, float],
                            country: Zone,
                            year_for_logging: int,
                            sources: dict[BasicSourceType, Source],
                            installed_map_gw: dict[BasicSourceType, float]) -> None:
    """
    Scale hourly production from basic sources as if their installed
    capacities changed according to the `sources` dict. This updates
    the data frame in place.
    """

    for source_type, source in sources.items():
        key = get_basic_key(source_type)
        if override := source.profile_override:
            installed_mw = override.installed_gw * 1000
        else:
            installed_mw = installed_map_gw[source_type] * 1000

        if pecd_data_map.get(source_type) is not None:
            normalization_factor = pecd_normalization_factors.get(source_type, 1.0)
            df[key] = (
                source.capacity_mw * _normalize_pecd_series(pecd_data_map[source_type],
                                                            normalization_factor, country)
            )
            df.fillna({key: 0}, inplace=True)
            continue

        # Issues a warning if the hourly production exceeds the assumed
        # installed capacity by more than 5% at any time. This check is disabled for wind where
        # wrongly stating current installed capacity to get higher capacity factor in the future can
        # be desired.
        if not source_type.is_wind() and (df[key] > 1.05 * installed_mw).any():
            # TODO: Use a logger once we have a proper logging solution in place.
            warnings.warn(
                f"Production of '{key}' in {country} in {year_for_logging} is more than 5% higher "
                f"than installed capacity ({df[key].max():.1f} vs. {installed_mw:.1f} MW)"
            )

        if installed_mw == 0:
            df[key] = 0
            if source.capacity_mw > 0:
                warnings.warn(
                    f"Cannot scale up {key} production from 0 to {source.capacity_mw:.1f} MW in "
                    f"{country} in {year_for_logging}. Did you forget to specify an override?"
                )
        else:
            if df[key].max() <= 0:
                warnings.warn(
                    f"Cannot scale up {key} production in {country} in {year_for_logging} because "
                    "it's zero in the whole time series. Did you forget to specify an override?"
                )
                # Nothing else can be done here.
            else:
                scale_factor = source.capacity_mw / installed_mw
                df[key] *= scale_factor


def _get_hours_with_pecd_week_numbers(year: int) -> pd.DataFrame:
    """
    Return table with a row per hour of the given year with two columns:
    - "date" - datetime of the hour
    - "Week" - PECD week to which the hour belongs.

    PECD has non-standard semantics of the "Week" column. It is _not_ an ISO week number. Instead,
    each year starts with week number 1 and counts up to week number 53. (Occasionally for leap
    years, it can count up to week number 54.)
    """
    index = pd.date_range(start=datetime(year, 1, 1), end=datetime(year + 1, 1, 1),
                          freq='H', inclusive="left")
    pecd_week = ((index.weekday == 0) & (index.hour == 0)).cumsum()
    if pecd_week[0] == 0:
        # The year doesn't start with a Monday: add one so that the first week is not zero.
        pecd_week += 1
    hourly = pd.DataFrame(data={"Week": pecd_week}, index=index)

    return hourly.reset_index().rename({"index": "date"}, axis=1)


class HourlyDataExtrapolator:
    # Columns extracted from the original ENTSO-E data file, mainly
    # generation from basic sources.
    _COLUMNS = [
        Keys.BIOMASS, Keys.HYDRO, Keys.LOAD, Keys.NUCLEAR, Keys.PRICE, Keys.SOLAR,
        Keys.WIND_OFFSHORE, Keys.WIND_ONSHORE
    ]

    def __init__(
        self,
        data_path: Union[str, Path],
    ) -> None:
        self.data_path = Path(data_path)

    def get_entsoe_path_if_exists(self, country: Zone, year: int) -> Optional[Path]:
        path: Path = Path(self._get_entsoe_path(country, year))
        if path.exists():
            return path
        path = Path(self._get_entsoe_local_path(country, year))
        if path.exists():
            return path
        return None

    def _get_entsoe_path(self, country: Zone, year: int) -> Path:
        return self.data_path / "entsoe" / f"{country}-{year}.csv"

    def _get_entsoe_local_path(self, country: Zone, year: int) -> Path:
        return self.data_path / "entsoe" / "local" / f"{country}-{year}.csv"

    @functools.cache
    def load_country_year_entsoe_data(
            self, country: Zone, entsoe_year: int, common_year: int) -> pd.DataFrame:
        path = self.get_entsoe_path_if_exists(country, entsoe_year)
        if not path:
            raise Exception(f"Data file for {country} and {entsoe_year} does not exist")

        df_all = pd.read_csv(path, index_col=Keys.DATE, parse_dates=True).fillna(0)

        # Sometimes, price data may be missing, default that with 0.
        if Keys.PRICE not in df_all:
            df_all[Keys.PRICE] = 0

        # Limit further processing to a subset of the columns.
        df_subset = df_all.loc[df_all.index.year == entsoe_year, HourlyDataExtrapolator._COLUMNS]
        # Some countries provide 30- or 15-min data. For our purposes,
        # average the numbers for each hour before any further processing.
        df_hourly = HourlyAverage(df_subset, reindex_to_year=common_year).mean_by_hour()

        return df_hourly

    def _get_pecd_country(self, country: Zone):
        if country == GREAT_BRITAIN:
            return "UK"
        return country

    @functools.cache
    def _load_pecd_data(self, path: Path, pecd_year: int, common_year: int) -> pd.DataFrame:
        df = pd.read_parquet(path)
        if pecd_year in df["year"].unique():
            df = df[df["year"] == pecd_year]
        elif str(pecd_year) in df["year"].unique():
            df = df[df["year"] == str(pecd_year)]
        else:
            raise Exception(
                f"unknown PECD year {pecd_year} used, choose one of {df['year'].unique()}")

        # Table with hourly capacity factors or hourly demand.
        if "cf" in df or "dem_MW" in df:
            date = df.apply(
                lambda x: f"{common_year}-{x['month']:.0f}-{x['day']:.0f} {x['hour'] - 1:.0f}:00:00",
                axis=1)
            df["date"] = pd.to_datetime(date)
            df.drop(["year", "month", "day", "hour"], axis=1, inplace=True)
        # Table with daily generation.
        elif "gen_GWh" in df:
            # Convert daily generation to average generation.
            df["gen_MW"] = df["gen_GWh"] / 24 * 1000
            # Turn one-row-per-day into one-row-per-hour.
            hourly = pd.DataFrame(data={"hour": range(24)})
            df = df.join(hourly, how="cross")
            # Create a proper datetime column.
            start_date = datetime(common_year, 1, 1)
            datetimes = df.apply(
                lambda x: start_date + timedelta(days=x['Day']-1, hours=x['hour']),
                axis=1)
            df["date"] = datetimes
            df.drop(["year", "Day", "hour", "gen_GWh"], axis=1, inplace=True)
        # Table with weekly inflow (into flexible "storage").
        elif "inflow_GWh" in df:
            # Remove pumped_closed which by definition has no inflows - the data is useless (all 0).
            df = df[df["technology"] != "pumped_closed"]
            # Convert weekly inflow to average inflow. The PECD data for the first week and the last
            # week is normalized, thus the operation is correct for those weeks as well.
            df["inflow_MW"] = df["inflow_GWh"] / (24 * 7) * 1000
            df = df.pivot_table(values="inflow_MW",
                                index=["country", "Week"],
                                columns=['technology'],
                                aggfunc="sum").reset_index()
            # Turn one-row-per-week into one-row-per-hour.
            hourly = _get_hours_with_pecd_week_numbers(common_year)
            df = hourly.merge(df, how="left", on="Week").drop(["Week"], axis=1)

        # Leap vs. non-leap year discrepancies can cause date to overflow to next year -- trim it.
        df = df[df["date"].dt.year == common_year]
        df.set_index("date", inplace=True)
        df.fillna(0, inplace=True)
        return df

    def _load_country_year_pecd_data(self,
                                     path: Path,
                                     country: Zone,
                                     pecd_year: int,
                                     common_year: int,
                                     key: Optional[str] = None) -> Optional[pd.Series]:
        df = self._load_pecd_data(path, pecd_year, common_year)
        pecd_country = self._get_pecd_country(country)
        if pecd_country not in df["country"].unique():
            return None
        df = df[df["country"] == pecd_country]
        if "dem_MW" in df:
            return df["dem_MW"]
        if "gen_MW" in df:
            return df["gen_MW"]
        if "reservoir" in df and key == Keys.HYDRO_INFLOW_RESERVOIR:
            return df["reservoir"]
        if "pumped_open" in df and key == Keys.HYDRO_INFLOW_PUMPED_OPEN:
            return df["pumped_open"]
        return df["cf"]

    @functools.cache
    def load_country_year_pecd_hydro_data_map(
            self,
            country: Zone,
            pecd_year: int,
            common_year: int) -> dict[str, Optional[pd.Series]]:
        def _load_hydro(key: str) -> Optional[pd.Series]:
            return self._load_country_year_pecd_data(
                self.data_path / "pecd" / "PECD_EERA2021_reservoir_pumping_2030_country_inflow.parquet",
                country, pecd_year, common_year, key)
        return {
            Keys.HYDRO_INFLOW_ROR: self._load_country_year_pecd_data(
                self.data_path / "pecd" / "PECD_EERA2021_ROR_2030_country_gen.parquet",
                country, pecd_year, common_year),
            Keys.HYDRO_INFLOW_RESERVOIR: _load_hydro(Keys.HYDRO_INFLOW_RESERVOIR),
            Keys.HYDRO_INFLOW_PUMPED_OPEN: _load_hydro(Keys.HYDRO_INFLOW_PUMPED_OPEN),
        }

    @functools.cache
    def load_country_year_pecd_data_map(
            self,
            country: Zone,
            pecd_year: int,
            common_year: int) -> dict[BasicSourceType, Optional[pd.Series]]:
        return {
            BasicSourceType.OFFSHORE: self._load_country_year_pecd_data(
                self.data_path / "pecd" / "PECD-2021.3-country-Offshore-2030.parquet",
                country, pecd_year, common_year),
            BasicSourceType.ONSHORE: self._load_country_year_pecd_data(
                self.data_path / "pecd" / "PECD-2021.3-country-Onshore-2030.parquet",
                country, pecd_year, common_year),
            BasicSourceType.SOLAR: self._load_country_year_pecd_data(
                self.data_path / "pecd" / "PECD-2021.3-country-LFSolarPV-2030.parquet",
                country, pecd_year, common_year),
        }

    @functools.cache
    def load_country_year_pecd_demand(
            self,
            country: Zone,
            pecd_year: int,
            common_year: int) -> Optional[pd.Series]:
        demand = self._load_country_year_pecd_data(
            self.data_path / "pecd" / "PECD-country-demand_national_estimates-2025.parquet",
            country, pecd_year, common_year)
        if demand is None or demand.sum() == 0:
            problem = 'unspecified' if demand is None else 'zero'
            print(f"Warning: PECD demand is {problem} for country {country}")
            return None
        return demand

    @functools.cache
    def get_pecd_parameters(self, filename: str) -> pd.DataFrame:
        df = pd.read_csv(self.data_path / "pecd" / filename)
        return df

    def extrapolate_hourly_country_data(self,
                                        country: Zone,
                                        entsoe_year: int,
                                        pecd_year: Optional[int],
                                        common_year: int,
                                        factors: LoadFactors,
                                        sources: dict[BasicSourceType, Source],
                                        installed_map_gw: dict[BasicSourceType, float],
                                        pecd_normalization_factors: dict[BasicSourceType, float],
                                        load_hydro_from_pecd: bool,
                                        load_demand_from_pecd: bool) -> pd.DataFrame:
        df = self.load_country_year_entsoe_data(country, entsoe_year, common_year).copy()

        if pecd_year:
            # Take a deep copy as the map may get modified with overrides below.
            pecd_data_map = deepcopy(self.load_country_year_pecd_data_map(
                country, pecd_year, common_year))
            pecd_hydro_map = self.load_country_year_pecd_hydro_data_map(
                country, pecd_year, common_year) if load_hydro_from_pecd else {}
            demand = self.load_country_year_pecd_demand(country, pecd_year, common_year)
            if demand is not None:
                df[Keys.LOAD] = demand
        else:
            assert not load_hydro_from_pecd, "can't load PECD hydro with no pecd_year"
            assert not load_demand_from_pecd, "can't load PECD demand with no pecd_year"
            pecd_data_map = {}
            pecd_hydro_map = {}

        # Make sure columns for all basic sources are present in the
        # data frame. Reset production of those not present in the
        # `sources` dict to zero.
        for basic_source in BasicSourceType:
            key = get_basic_key(basic_source)
            if key not in df or basic_source not in sources:
                df[key] = 0

        # Simply copy over all PECD hydro items (no scaling is needed).
        for key, series in pecd_hydro_map.items():
            df[key] = series

        # Replace production curves of sources with specified override.
        # TODO: Handle leap vs. non-leap years (8760 vs. 8784 hours/rows).
        for type, source in sources.items():
            if override := source.profile_override:
                # Check whether the override is unnecessary.
                pecd = pecd_data_map.get(type)
                assert pecd is None or (pecd == 0).all(), f"Nonzero PECD overriden {country}/{type}"
                source_key = get_basic_key(type)
                assert (df[source_key] == 0).all(), f"Nonzero ENTSOE overriden {country}/{type}"

                # Fall back to the original type if none was specified.
                override_type = override.source_type or type

                if pecd_year:
                    override_data_map = self.load_country_year_pecd_data_map(
                        override.country, pecd_year, common_year)
                    pecd_data_map[type] = override_data_map.get(override_type)

                # Provide an ENTSOE fallback if PECD data did not provide one.
                if pecd_data_map.get(type) is None:
                    override_df = self.load_country_year_entsoe_data(
                        override.country, entsoe_year, common_year)
                    # Overwrite by values from override and backfill
                    # unmatched values (use next non-missing value).
                    df[source_key] = override_df[get_basic_key(override_type)]
                    df[source_key].fillna(method="backfill", inplace=True)

        _scale_basic_production(
            df, pecd_data_map, pecd_normalization_factors, country, common_year, sources, installed_map_gw)

        df[Keys.WIND] = df[Keys.WIND_ONSHORE] + df[Keys.WIND_OFFSHORE]

        if 'load' in factors:
            df[Keys.LOAD] *= factors['load']
        else:
            df[Keys.LOAD_BASE], df[Keys.LOAD_HEAT_PUMPS] = scale_by_seasonal_factors(
                df[Keys.LOAD], factors)
            df[Keys.LOAD] = df[Keys.LOAD_BASE] + df[Keys.LOAD_HEAT_PUMPS]

        df['VRE'] = df[Keys.WIND] + df[Keys.SOLAR]
        df['Residual'] = df[Keys.LOAD] - df['VRE']
        df['Total'] = df['Production'] = df['VRE'] + df[Keys.NUCLEAR] + df[Keys.HYDRO]
        df['Curtailment'] = df['Storable'] = df['Total'] - df[Keys.LOAD]
        df['Shortage'] = df[Keys.LOAD] - df['Total']
        df[Keys.IMPORT] = 0
        df[Keys.EXPORT] = 0
        df[Keys.NET_IMPORT] = 0

        return df

    @staticmethod
    def estimate_spot_prices(shortage: pd.DataFrame,
                             flexible_sources: list[FlexibleSource]) -> pd.DataFrame:
        def get_price(item: FlexibleSource):
            return item.economics.variable_costs_per_mwh_eur

        flexible_sources.sort(key=get_price)

        def get_uptime_ratio(source: FlexibleSource):
            if source.max_total_twh is None:
                return 1.0
            full_utilization_twh = (source.capacity_mw * 8760) / 1_000_000
            return source.max_total_twh / full_utilization_twh

        def estimate_spot_price(shortage):
            if (shortage < 0):
                return shortage, 0, 'VRE'
            remaining = shortage
            for source in flexible_sources:
                remaining -= source.capacity_mw * get_uptime_ratio(source)
                if (remaining < 0):
                    return shortage, source.economics.variable_costs_per_mwh_eur, source.type.value
            return shortage, 1000, "backup"

        price_type = shortage.map(estimate_spot_price)
        prices = pd.DataFrame(price_type.tolist(),
                              columns=['shortage', 'price', 'type'],
                              index=price_type.index)
        return prices

    def estimate_interconnector_flows(
            self,
            grids: dict[Region, CountryGrid],
            interconnectors: Interconnectors):
        # TODO: Finish implementation.
        pass
