"""
Provides extrapolated entsoe hourly/15min data based on provided factors.
"""

import warnings
from copy import deepcopy
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import beta

from .country_grid import CountryGrid
from .data_utils import scale_by_seasonal_factors
from .grid_plot_utils import Keys, get_basic_key
from .loaders import EntsoeLoader, PecdLoader
from .params_library.interconnectors import Interconnectors
from .params_library.load_factors import LoadFactors
from .region import Region, Zone
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

        source_data = pecd_data_map.get(source_type)
        if source_data is not None:
            normalization_factor = pecd_normalization_factors.get(source_type, 1.0)
            df[key] = (
                source.capacity_mw * _normalize_pecd_series(
                    source_data, normalization_factor, country
                )
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
            if df[key].max() <= 0 and source.capacity_mw > 0:
                warnings.warn(
                    f"Cannot scale up {key} production in {country} in {year_for_logging} because "
                    "it's zero in the whole time series. Did you forget to specify an override?"
                )
                # Nothing else can be done here.
            else:
                scale_factor = source.capacity_mw / installed_mw
                df[key] *= scale_factor


class HourlyDataExtrapolator:
    def __init__(
        self,
        entsoe_loader: EntsoeLoader,
        pecd_loader: Optional[PecdLoader] = None
    ) -> None:
        self._entsoe_loader = entsoe_loader
        self._pecd_loader = pecd_loader

    def extrapolate_hourly_country_data(self,
                                        country: Zone,
                                        entsoe_year: int,
                                        pecd_year: Optional[int],
                                        common_year: int,
                                        factors: LoadFactors,
                                        sources: dict[BasicSourceType, Source],
                                        installed_map_gw: dict[BasicSourceType, float],
                                        pecd_normalization_factors: dict[BasicSourceType, float],
                                        load_hydro_from_pecd: bool = False,
                                        load_demand_from_pecd: bool = False,
                                        pecd_target_year: int = 2025) -> pd.DataFrame:
        """
        Load demand and generation time series for the given country
        and extrapolate according to the given parameters.

        Arguments:
            country: Country for which to load data.
            entsoe_year: Historical year for which to load time series
                from ENTSO-E datasets. This includes non-dispatchable
                source generation and hourly load.
            pecd_year: Historical weather year for which to load demand
                and/or hydropower generation data, if requested.
            common_year: Reindex the data frame to the given year.
            factors: Electricity demand scaling factors.
            sources: Parameters of basic sources in the region.
                Generation of basic sources not present in this
                dictionary will be zero.
            installed_map_gw: Mapping of historical installed capacities
                of basic sources.
            pecd_normalization_factors: Normalization factors to use
                when rescaling basic source generation.
            load_hydro_from_pecd: Load hydropower generation and inflows
                from the PECD dataset.
            load_demand_from_pecd: Load electricity demand from PECD.
            pecd_target_year: Target year for the PECD demand, hydro
                and renewables generation datasets.
        """
        if load_demand_from_pecd or load_hydro_from_pecd:
            if not pecd_year:
                raise ValueError(
                    "PECD year must be specified for loading demand and hydropower time series"
                )
            if not self._pecd_loader:
                raise ValueError(
                    "PECD loader is required for loading demand and hydropower time series"
                )

        df = self._entsoe_loader.load_country_year_data(country, entsoe_year, common_year).copy()

        pecd_data_map = {}
        pecd_hydro_map = {}
        if pecd_year:
            # To make the types check. This is ensured above.
            assert self._pecd_loader
            # Take a deep copy as the map may get modified with overrides below.
            pecd_data_map = deepcopy(
                self._pecd_loader.load_basic_sources_map(
                    country, pecd_year, common_year, target_year=pecd_target_year
                )
            )
            if load_demand_from_pecd:
                demand = self._pecd_loader.load_demand(
                    country, pecd_year, common_year, target_year=pecd_target_year
                )
                if df.empty:
                    # Use the index of solar
                    df.index = pecd_data_map[BasicSourceType.SOLAR].index
                if demand is not None:
                    df[Keys.LOAD] = demand
            if load_hydro_from_pecd:
                pecd_hydro_map = self._pecd_loader.load_hydro_map(
                    country, pecd_year, common_year, target_year=pecd_target_year
                )

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

                if pecd_year and self._pecd_loader:
                    override_data_map = self._pecd_loader.load_basic_sources_map(
                        override.country, pecd_year, common_year
                    )
                    pecd_data_map[type] = override_data_map.get(override_type)

                # Provide an ENTSOE fallback if PECD data did not provide one.
                if pecd_data_map.get(type) is None:
                    override_df = self._entsoe_loader.load_country_year_data(
                        override.country, entsoe_year, common_year)
                    if override_df.empty:
                        raise Exception(f"Entsoe data for {override.country} and {entsoe_year} missing")
                    # Overwrite by values from override and backfill
                    # unmatched values (use next non-missing value).
                    df[source_key] = override_df[get_basic_key(override_type)]
                    df[source_key].fillna(method="backfill", inplace=True)

        _scale_basic_production(
            df, pecd_data_map, pecd_normalization_factors, country, common_year, sources,
            installed_map_gw
        )

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
