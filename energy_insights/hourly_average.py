"""
Takes a Pandas Dataframe and returns average values for each hour (keeping DateTime index)
"""

from typing import Optional

import pandas as pd


class HourlyAverage:
    def __init__(self, df, reindex_to_year: Optional[int] = None):
        self.df = df
        self._reindex_to_year = reindex_to_year

    def mean_by_hour(self):
        index_name = self.df.index.name
        datetime_format = "%Y-%m-%d %H:00"
        if self._reindex_to_year:
            datetime_format = f"{self._reindex_to_year}-%m-%d %H:00"
        self.df["Date_grouped"] = self.df.index.strftime(datetime_format)

        self.df['Date_grouped'] = pd.to_datetime(self.df['Date_grouped'], format='%Y-%m-%d %H:%M')
        self.df = self.df.groupby('Date_grouped').mean()
        self.df.index.set_names(index_name, inplace=True)
        return self.df
