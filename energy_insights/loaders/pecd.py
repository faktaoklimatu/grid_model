import enum
from datetime import datetime, timedelta
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from ..grid_plot_utils import get_basic_key, Keys
from ..params_library.basic_source import load_basic_sources_from_ember_ng
from ..params_library.flexible_source import load_flexible_sources_from_ember_ng
from ..params_library.installed import load_installed_and_production_from_ember_ng
from ..params_library.load_factors import LoadFactors, load_load_factors_from_ember_ng
from ..params_library.storage import load_hydro_storage_from_pecd
from ..region import GREAT_BRITAIN, Region, Zone
from ..sources.basic_source import BasicSourceType, ProfileOverride
from ..sources.flexible_source import FlexibleSourceType


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
                          freq="h", inclusive="left")
    pecd_week = ((index.weekday == 0) & (index.hour == 0)).cumsum()
    if pecd_week[0] == 0:
        # The year doesn't start with a Monday: add one so that the first week is not zero.
        pecd_week += 1
    hourly = pd.DataFrame(data={"Week": pecd_week}, index=index)

    return hourly.reset_index().rename({"index": "date"}, axis=1)


def _get_pecd_country(country: Zone) -> Region:
    if country == GREAT_BRITAIN:
        return "UK"
    return country


def _override_year(new_year: int):
    def mapper(x: Any) -> str:
        return f"{new_year}-{x['month']:.0f}-{x['day']:.0f} {x['hour'] - 1:.0f}:00:00"
    return mapper


class PecdLoader:
    """
    # TODO: Update docstring.
    """

    def __init__(self, data_path: Union[str, Path]) -> None:
        self._data_path = Path(data_path)

    def _load_country_year_data(self,
                                path: Path,
                                country: Zone,
                                pecd_year: int,
                                common_year: int,
                                key: Optional[str] = None) -> Optional[pd.Series]:
        df = self._load_pecd_data(path, pecd_year, common_year)
        pecd_country = _get_pecd_country(country)
        if pecd_country not in df["country"].unique():
            return None
        df = df[df["country"] == pecd_country]
        # Hourly power demand.
        if "dem_MW" in df:
            return df["dem_MW"]
        # Daily power generation.
        if "gen_MW" in df:
            return df["gen_MW"]
        # Hydro pondage inflows.
        if "pondage" in df and key == Keys.HYDRO_INFLOW_PONDAGE:
            return df["pondage"]
        # Hydro pondage inflows.
        if "ror" in df and key == Keys.HYDRO_INFLOW_ROR:
            return df["ror"]
        # Hydro reservoir inflows.
        if "reservoir" in df and key == Keys.HYDRO_INFLOW_RESERVOIR:
            return df["reservoir"]
        # Open-loop pumped hydro inflows.
        if "pumped_open" in df and key == Keys.HYDRO_INFLOW_PUMPED_OPEN:
            return df["pumped_open"]
        # Hourly capacity factors.
        return df["cf"]

    @cache
    def _load_pecd_data(self, path: Path, pecd_year: int, common_year: int) -> pd.DataFrame:
        """
        NOTE: This method returns a cached copy. Its return value must
        be copied if you want to modify it.
        """
        df = pd.read_parquet(path)
        unique_years = df["year"].unique()

        if pecd_year in unique_years:
            df = df[df["year"] == pecd_year]
        elif str(pecd_year) in unique_years:
            df = df[df["year"] == str(pecd_year)]
        else:
            available_years = ", ".join(f"{y:.0f}" for y in unique_years)
            raise ValueError(
                f"PECD year {pecd_year} unavailable, choose one of {available_years}"
            )

        # Table with hourly capacity factors or hourly demand.
        if "cf" in df or "dem_MW" in df:
            date = df.apply(_override_year(common_year), axis=1)
            df["date"] = pd.to_datetime(date)
            df.drop(["year", "month", "day", "hour"], axis=1, inplace=True)
        # Table with daily generation.
        elif "gen_GWh" in df:
            # Convert daily generation to average generation.
            df["gen_MW"] = df["gen_GWh"] / 24 * 1000
            df = df.pivot_table(values="gen_MW",
                                index=["country", "Day"],
                                columns="technology",
                                aggfunc="sum").reset_index()
            # Turn one-row-per-day into one-row-per-hour.
            hourly = pd.DataFrame(data={"hour": range(24)})
            df = df.join(hourly, how="cross")
            # Create a proper datetime column.
            start_date = datetime(common_year, 1, 1)
            datetimes = df.apply(
                lambda x: start_date + timedelta(days=x["Day"] - 1, hours=x["hour"]),
                axis=1
            )
            df["date"] = datetimes
            df.drop(["Day", "hour"], axis=1, inplace=True)
        # Table with weekly inflows into hydro power plants (reservoirs
        # and open-loop pumped storage).
        elif "inflow_GWh" in df:
            # Remove pumped_closed which by definition has no inflows - the data is useless (all 0).
            df = df[df["technology"] != "pumped_closed"]
            # Convert weekly inflow to average inflow. The PECD data for the first week and the last
            # week is normalized, thus the operation is correct for those weeks as well.
            df["inflow_MW"] = df["inflow_GWh"] / (24 * 7) * 1000
            df = df.pivot_table(values="inflow_MW",
                                index=["country", "Week"],
                                columns=["technology"],
                                aggfunc="sum").reset_index()
            # Turn one-row-per-week into one-row-per-hour.
            hourly = _get_hours_with_pecd_week_numbers(common_year)
            df = hourly.merge(df, how="left", on="Week").drop(["Week"], axis=1)

        df = (
            # Leap vs. non-leap year discrepancies can cause date to
            # overflow to next year -- trim it.
            df[df["date"].dt.year == common_year]
            .set_index("date")
            .fillna(0)
        )
        return df

    @cache
    def _load_parameters(self, filename: str) -> pd.DataFrame:
        """
        NOTE: This method returns a cached copy. Its return value must
        be copied if you want to modify it.
        """
        return pd.read_csv(self._data_path / "pecd" / filename)

    @cache
    def load_basic_sources_map(self,
                               country: Zone,
                               pecd_year: int,
                               common_year: int,
                               target_year: int = 2025) -> dict[BasicSourceType, Optional[pd.Series]]:
        """
        NOTE: This method returns a cached copy. Its return value must
        be copied if you want to modify it.
        """
        offshore_path = self._data_path / "pecd" / \
            f"PECD-ERAA2023-Wind_Offshore-{target_year}.parquet"
        onshore_path = self._data_path / "pecd" / \
            f"PECD-ERAA2023-Wind_Onshore-{target_year}.parquet"
        solar_path = self._data_path / "pecd" / \
            f"PECD-ERAA2023-LFSolarPV-{target_year}.parquet"

        return {
            BasicSourceType.OFFSHORE: self._load_country_year_data(
                offshore_path, country, pecd_year, common_year
            ),
            BasicSourceType.ONSHORE: self._load_country_year_data(
                onshore_path, country, pecd_year, common_year
            ),
            BasicSourceType.SOLAR: self._load_country_year_data(
                solar_path, country, pecd_year, common_year
            ),
        }

    @cache
    def load_demand(self,
                    country: Zone,
                    pecd_year: int,
                    common_year: int,
                    target_year: int = 2025) -> Optional[pd.Series]:
        """
        NOTE: This method returns a cached copy. Its return value must
        be copied if you want to modify it.
        """
        demand_filename = f"PECD-country-demand_national_estimates-{target_year}.parquet"
        file_path = self._data_path / "pecd" / demand_filename
        demand = self._load_country_year_data(file_path, country, pecd_year, common_year)

        if demand is None:
            print(f"Warning: PECD demand is unspecified for country {country}")
            return None

        if demand.sum() == 0:
            print(f"Warning: PECD demand is zero for country {country}")
            return None

        return demand

    @cache
    def load_hydro_map(self,
                       country: Zone,
                       pecd_year: int,
                       common_year: int,
                       target_year: int = 2025) -> dict[str, Optional[pd.Series]]:
        """
        NOTE: This method returns a cached copy. Its return value must
        be copied if you want to modify it.
        """
        def _load_hydro(series: str, key: str) -> Optional[pd.Series]:
            file_path = self._data_path / "pecd" / \
                f"PECD-ERAA2023-{series}-inflows-{target_year}.parquet"
            return self._load_country_year_data(file_path, country, pecd_year, common_year, key)

        return {
            Keys.HYDRO_INFLOW_PONDAGE: _load_hydro("RoR+pondage", Keys.HYDRO_INFLOW_PONDAGE),
            Keys.HYDRO_INFLOW_ROR: _load_hydro("RoR+pondage", Keys.HYDRO_INFLOW_ROR),
            Keys.HYDRO_INFLOW_RESERVOIR: _load_hydro("reservoir+pumped", Keys.HYDRO_INFLOW_RESERVOIR),
            Keys.HYDRO_INFLOW_PUMPED_OPEN: _load_hydro("reservoir+pumped", Keys.HYDRO_INFLOW_PUMPED_OPEN),
        }

    def load_hydro_storage(self, country: Zone) -> list[dict]:
        return load_hydro_storage_from_pecd(
            self._load_parameters("PECD_EERA2021_reservoir_pumping_2030_country_table.csv"),
            self._load_parameters("PECD_EERA2021_ROR_2030_country_table.csv"),
            _get_pecd_country(country)
        )
