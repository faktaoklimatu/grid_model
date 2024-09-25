import functools
from collections.abc import Collection
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from ..grid_plot_utils import Keys
from ..hourly_average import HourlyAverage
from ..region import Zone


class EntsoeLoader:
    # Columns extracted from the original ENTSO-E data file, mainly
    # generation from basic sources.
    _COLUMNS = [
        Keys.BIOMASS, Keys.HYDRO, Keys.LOAD, Keys.NUCLEAR, Keys.PRICE, Keys.SOLAR,
        Keys.WIND_OFFSHORE, Keys.WIND_ONSHORE
    ]

    def __init__(self, data_path: Union[str, Path]) -> None:
        self._data_path = Path(data_path)

    def _get_paths(self, country: Zone, year: int) -> Collection[Path]:
        return [
            self._data_path / "entsoe" / f"{country}-{year}.csv",
            self._data_path / "entsoe" / "local" / f"{country}-{year}.csv",
        ]

    def get_entsoe_path_if_exists(self, country: Zone, year: int) -> Optional[Path]:
        for path in self._get_paths(country, year):
            if path.exists():
                return path

        return None

    @functools.cache
    def load_country_year_data(
            self, country: Zone, entsoe_year: int, common_year: int) -> pd.DataFrame:
        path = self.get_entsoe_path_if_exists(country, entsoe_year)
        if not path:
            return pd.DataFrame()

        df_all = pd.read_csv(path, index_col=Keys.DATE, parse_dates=True).fillna(0)

        # Sometimes, price data may be missing, default that with 0.
        if Keys.PRICE not in df_all:
            df_all[Keys.PRICE] = 0

        # Limit further processing to a subset of the columns.
        df_subset = df_all.loc[df_all.index.year == entsoe_year, EntsoeLoader._COLUMNS]
        # Some countries provide 30- or 15-min data. For our purposes,
        # average the numbers for each hour before any further processing.
        df_hourly = HourlyAverage(df_subset, reindex_to_year=common_year).mean_by_hour()

        return df_hourly
