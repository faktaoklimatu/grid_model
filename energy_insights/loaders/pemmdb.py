from collections import defaultdict
from collections.abc import Collection
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Optional, Union

import pandas

from ..grid_plot_utils import Keys
from ..params_library.interconnectors import (
    InterconnectorsDict,
    add_distances_type_and_loss_to_interconnectors,
    aggregate_interconnectors,
)
from ..params_library.load_factors import LoadFactors
# TODO: Avoid private access.
from ..params_library.storage import (
    __grid_lion_battery,
    __hydro_pecd,
    __pecd_hydro_fill_ratio,
    __pumped_hydro_closed_pecd,
    __pumped_hydro_open_pecd,
    __reservoir_pecd,
    __ror_pecd,
)
from ..region import *
from ..sources.basic_source import BasicSourceType, ProfileOverride
from ..sources.flexible_source import FlexibleSourceType
from ..sources.reserves import Reserves
from ..sources.storage import StorageType

# Dataset "PEMMDB Generation" downloaded from the ENTSO-E ERAA
# 2023 page[1].
# PEMMDB stands for "Pan-European Market Database". It is maintained by
# the System Adequacy and Market Modelling working group[2] (WG SAMM) at
# ENTSO-E.
# ERAA stands for "European Resource Adequacy Assessment".
# [1]: https://www.entsoe.eu/outlooks/eraa/2023/eraa-downloads/
# [2]: https://docstore.entsoe.eu/about-entso-e/system-development/system-adequacy-and-market-modeling/Pages/default.aspx


# Deal with differences in country codes vs. PEMMDB dataset.
_PEMMDB_COUNTRY_MAP: dict[Zone, Region] = {
    GREAT_BRITAIN: Region("UK")
}

_PEMMDB_2023_BASIC_SOURCES_MAP: dict[str, BasicSourceType] = {
    # NOTE: At the moment, we load hydro parameters from
    # the pre-processed PECD dataset rather than from PEMMDB.
    "Nuclear": BasicSourceType.NUCLEAR,
    "Solar (Photovoltaic)": BasicSourceType.SOLAR,
    "Wind Offshore": BasicSourceType.OFFSHORE,
    "Wind Onshore": BasicSourceType.ONSHORE,
}

_PEMMDB_2023_FLEXIBLE_SOURCES_MAP: dict[str, tuple[FlexibleSourceType, dict]] = {
    "Biofuel": (
        FlexibleSourceType.SOLID_BIOMASS,
        {"uptime_ratio": 0.8, "overnight_costs_per_kw_eur": 1800},
    ),
    "Demand Side Response capacity": (FlexibleSourceType.DSR, {}),
    # NOTE: The trailing space is intentional.
    "Gas ": (FlexibleSourceType.GAS_CCGT, {}),
    "Hard Coal": (FlexibleSourceType.COAL, {}),
    "Lignite": (FlexibleSourceType.LIGNITE, {}),
    # TODO: Can we map this more precisely?
    "Oil": (FlexibleSourceType.GAS_ENGINE, {}),
    # This can be country-specific, e.g. mostly lig_ex/lig_bp in Czechia,
    # but gas_peak in Norway.
    "Others non-renewable": (FlexibleSourceType.LIGNITE_EXTRACTION, {}),
    "Others renewable": (
        FlexibleSourceType.BIOGAS,
        {"uptime_ratio": 0.8, "overnight_costs_per_kw_eur": 970},
    ),
}

# Copied from params_library.interconnectors.
_generic_transmission_loss = .02


def _map_to_pemmdb_region(country: Zone) -> Region:
    return _PEMMDB_COUNTRY_MAP.get(country, country)


def _pemmdb_load_batteries(sources: pandas.Series,
                           storage: pandas.Series,
                           allow_capex_optimization: bool) -> Optional[dict]:
    installed_mw_charging = sources["Batteries (Injection)"]
    installed_mw_discharging = sources["Batteries (Offtake)"]
    max_energy_mwh = storage["Batteries"]

    if max_energy_mwh == 0:
        return

    return __grid_lion_battery | {
        "capacity_mw": installed_mw_discharging,
        "capacity_mw_charging": installed_mw_charging,
        "max_energy_mwh": max_energy_mwh,
        "min_capacity_mw": 0 if allow_capex_optimization else installed_mw_discharging,
        "min_capacity_mw_charging": 0 if allow_capex_optimization else installed_mw_charging,
    }


def _pemmdb_load_pondage_hydro(sources: pandas.Series, storage: pandas.Series) -> Optional[dict]:
    capacity_mw = sources["Hydro - Pondage (Turbine)"]
    max_energy_mwh = storage["Hydro - Pondage"]

    if capacity_mw == 0:
        return

    return __hydro_pecd | {
        "type": StorageType.PONDAGE,
        "inflow_hourly_data_key": Keys.HYDRO_INFLOW_PONDAGE,
        "max_energy_mwh": max_energy_mwh,
        "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "capacity_mw": capacity_mw,
        "min_capacity_mw": capacity_mw,
        # Tiny variable costs, similar to run-of-river.
        "variable_costs_per_mwh_eur": 2,
    }


def _pemmdb_load_pumped_hydro(sources: pandas.Series, storage: pandas.Series) -> list[dict]:
    def _make_storage_dict(name: str) -> Optional[dict]:
        capacity_mw = sources[f"{name} (Turbine)"]
        capacity_mw_charging = -1 * sources[f"{name} (Pumping)"]
        max_energy_mwh = storage[name]

        if max_energy_mwh == 0:
            return

        return {
            "max_energy_mwh": max_energy_mwh,
            "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
            "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
            "capacity_mw": capacity_mw,
            "min_capacity_mw": capacity_mw,
            "capacity_mw_charging": capacity_mw_charging,
            "min_capacity_mw_charging": capacity_mw_charging,
        }

    storages: list[dict] = []

    # Load open- and closed-loop pumped hydro separately as closed-loop
    # has no natural inflows.
    if pumped_open := _make_storage_dict("Hydro - Pump Storage Open Loop"):
        storages.append(__pumped_hydro_open_pecd | pumped_open)
    if pumped_closed := _make_storage_dict("Hydro - Pump Storage Closed Loop"):
        storages.append(__pumped_hydro_closed_pecd | pumped_closed)

    return storages


def _pemmdb_load_reservoir_hydro(sources: pandas.Series, storage: pandas.Series) -> Optional[dict]:
    capacity_mw = sources["Hydro - Reservoir (Turbine)"]
    max_energy_mwh = storage["Hydro - Reservoir"]

    if max_energy_mwh == 0:
        return

    return __reservoir_pecd | {
        "inflow_min_discharge_ratio": 0.4,
        "max_energy_mwh": max_energy_mwh,
        "initial_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "final_energy_mwh": __pecd_hydro_fill_ratio * max_energy_mwh,
        "capacity_mw": capacity_mw,
        "min_capacity_mw": capacity_mw,
    }


def _pemmdb_load_ror_hydro(sources: pandas.Series) -> Optional[dict]:
    capacity_mw = sources["Hydro - Run of River (Turbine)"]

    if capacity_mw == 0:
        return

    return __ror_pecd | {
        # Approximately the minimum constraint (vs. average inflows)
        # in Norwegian inflows data.
        "inflow_min_discharge_ratio": 0.3,
        "max_energy_mwh": 0,
        "capacity_mw": capacity_mw,
        "min_capacity_mw": capacity_mw,
    }


def _zone_to_country(zone: str) -> str:
    return zone[:2]


class Pemmdb2023Loader:
    """
    Loader for the 2023 edition of the Pan-European Market Modelling
    Database (PEMMDB). This dataset is prepared regularly for the
    modelling needs of the European Resource Adequacy Assessment (ERAA).
    """

    _INTERCON_NUM_ROWS = 3
    _INTERCON_SKIP_ROWS = 7
    _RESERVES_SHEET_NAME = "Reserve Requirements"
    _SOURCES_NUM_ROWS = 24
    _SOURCES_SKIP_ROWS = 1
    _STORAGE_NUM_ROWS = 6
    _STORAGE_SKIP_ROWS = 28

    _TARGET_YEARS = (2025, 2028, 2030, 2033)

    def __init__(self, data_file: Union[str, Path]) -> None:
        self._data_file = Path(data_file)

    @staticmethod
    def _check_year(year: int) -> None:
        if year not in Pemmdb2023Loader._TARGET_YEARS:
            available_years = ", ".join(map(str, Pemmdb2023Loader._TARGET_YEARS))
            raise ValueError(
                f"Target year {year} not available in PEMMDB 2023 dataset. "
                f"Available years: {available_years}"
            )

    def _data_file_intercon(self, year: int) -> Path:
        data_directory = self._data_file.parent
        return data_directory / f"PEMMDB_Transfer_Capacities_{year}.xlsx"

    @cache
    def _df_intercon(self, year: int) -> pandas.DataFrame:
        Pemmdb2023Loader._check_year(year)

        def _load_from_sheet_header(sheet_name: str) -> pandas.DataFrame:
            df_raw = pandas.read_excel(self._data_file_intercon(year),
                                       sheet_name=sheet_name,
                                       skiprows=Pemmdb2023Loader._INTERCON_SKIP_ROWS,
                                       nrows=Pemmdb2023Loader._INTERCON_NUM_ROWS,
                                       index_col=1,
                                       engine="openpyxl")
            # The index now contains properties of the links (FROM, TO,
            # NET_CAP). Drop the first column which contains labels and
            # transpose to get a tidy table with one row for each link.
            df_long = df_raw.iloc[:, 1:].transpose()
            # Convert capacities to numbers for they were parsed as
            # strings.
            df_long["NET_CAP"] = pandas.to_numeric(df_long["NET_CAP"])
            # Translate zone IDs to country codes.
            df_long[["FROM", "TO"]] = (
                df_long[["FROM", "TO"]].apply(lambda columm: columm.apply(_zone_to_country))
            )
            # Select links between different countries only.
            df_cross = df_long[df_long["FROM"] != df_long["TO"]]
            return df_cross.groupby(["FROM", "TO"]).sum()

        def _load_from_sheet_body(sheet_name: str) -> pandas.DataFrame:
            df_raw = pandas.read_excel(self._data_file_intercon(year),
                                       sheet_name=sheet_name,
                                       skiprows=15,
                                       nrows=8760,
                                       engine="openpyxl")
            # Calculate a pandas Series of maximum link capacities.
            # Each entry corresponds to one link in on direction.
            max_link_capacities = (
                # Drop irrelevant and empty columns first.
                df_raw.drop(["Date", "Hour"], axis=1).dropna(axis=1, how="all")
                .max().rename(lambda column: column[-9:])
            )
            # Construct indexes for each endpoint of the links.
            index_from = max_link_capacities.index.map(lambda column: column.split("-")[0])
            index_to = max_link_capacities.index.map(lambda column: column.split("-")[1])

            df_links = pandas.DataFrame({
                "FROM": index_from,
                "TO": index_to,
                "NET_CAP": max_link_capacities,
            })
            # Translate zone IDs to country codes.
            df_links[["FROM", "TO"]] = (
                df_links[["FROM", "TO"]].apply(lambda columm: columm.apply(_zone_to_country))
            )
            # Select links between different countries only.
            df_cross = df_links[df_links["FROM"] != df_links["TO"]]
            return df_cross.groupby(["FROM", "TO"]).sum()

        # Load AC and DC links separately and sum their capacities.
        df_hvac = _load_from_sheet_body("HVAC")
        df_hvdc = _load_from_sheet_body("HVDC")

        return df_hvac.add(df_hvdc, fill_value=0)

    @cached_property
    def _df_reserves(self) -> pandas.DataFrame:
        df_raw = pandas.read_excel(self._data_file,
                                   sheet_name=Pemmdb2023Loader._RESERVES_SHEET_NAME,
                                   index_col=[0, 1],
                                   engine="openpyxl")
        return df_raw.groupby(lambda key: (_zone_to_country(key[0]), key[1])).sum()

    @cache
    def _df_sources(self, year: int) -> pandas.DataFrame:
        Pemmdb2023Loader._check_year(year)

        df_raw = pandas.read_excel(self._data_file,
                                   sheet_name=self._sheet_name(year),
                                   usecols="B:BE",
                                   skiprows=Pemmdb2023Loader._SOURCES_SKIP_ROWS,
                                   nrows=Pemmdb2023Loader._SOURCES_NUM_ROWS,
                                   index_col=0,
                                   engine="openpyxl")
        # The index (first column in the spreadsheet) lists something
        # like bidding zones with the first two characters being
        # the country code, so we sum all the columns (installed
        # capacities) across these country codes.
        return df_raw.transpose().groupby(_zone_to_country).sum()

    @cache
    def _df_storage(self, year: int) -> pandas.DataFrame:
        Pemmdb2023Loader._check_year(year)

        df_raw = pandas.read_excel(self._data_file,
                                   sheet_name=self._sheet_name(year),
                                   usecols="B:BE",
                                   skiprows=Pemmdb2023Loader._STORAGE_SKIP_ROWS,
                                   nrows=Pemmdb2023Loader._STORAGE_NUM_ROWS,
                                   index_col=0,
                                   engine="openpyxl")
        # The index (first column in the spreadsheet) lists something
        # like bidding zones with the first two characters being
        # the country code, so we sum all the columns (installed
        # capacities) across these country codes.
        return df_raw.transpose().groupby(_zone_to_country).sum()

    def _sheet_name(self, year: int):
        return f"TY{year}"

    def get_basic_sources(self,
                          country: Zone,
                          year: int,
                          allow_capex_optimization=False,
                          profile_overrides: Optional[dict[BasicSourceType, Zone]] = None) \
            -> dict[BasicSourceType, dict]:
        pemmdb_country = _map_to_pemmdb_region(country)
        installed_map = self.get_installed(pemmdb_country, year)
        sources: dict[BasicSourceType, dict] = {}

        for source_type, installed_gw in installed_map.items():
            installed_mw = 1000 * installed_gw
            # Ignore sources below 100 kW.
            if installed_mw < .1:
                continue

            sources[source_type] = {
                "capacity_mw": installed_mw,
                "min_capacity_mw": 0 if allow_capex_optimization else installed_mw,
            }

            if profile_overrides and source_type in profile_overrides:
                override_country = profile_overrides[source_type]
                override_installed = self.get_installed(override_country)[source_type]
                sources[source_type]["profile_override"] = ProfileOverride(
                    override_country, override_installed, source_type)

        return sources

    def get_countries_from_aggregate(
            self,
            region: AggregateRegion,
            year: int,
            overrides: Optional[dict[Zone, dict[BasicSourceType, Zone]]] = None,
            include_reserves=False) \
            -> dict[Zone, dict[str, Any]]:
        if not overrides:
            overrides = {}

        return {
            part: self.get_country(
                country=part,
                year=year,
                in_aggregate=region,
                profile_overrides=overrides.get(part),
                include_reserves=include_reserves)
            for part in get_aggregated_countries(region)
        }

    def get_country(self,
                    country: Zone,
                    year: int,
                    allow_capex_optimization=False,
                    in_aggregate: Optional[AggregateRegion] = None,
                    profile_overrides: Optional[dict[BasicSourceType, Zone]] = None,
                    include_reserves=False) \
            -> dict[str, Any]:
        result = {
            "basic_sources": self.get_basic_sources(country,
                                                    year,
                                                    allow_capex_optimization,
                                                    profile_overrides=profile_overrides),
            "flexible_sources": self.get_flexible_sources(country, year, allow_capex_optimization),
            "installed_gw": self.get_installed(country, year),
            "load_factors": self.get_load_factors(country, year),
            "storage": self.get_storage(country, year, allow_capex_optimization)
        }

        if include_reserves:
            reserves = self.get_reserve_requirements(country, year)
            if reserves:
                result["reserves"] = reserves

        if in_aggregate:
            result["in_aggregate"] = in_aggregate

        return result

    def get_flexible_sources(self,
                             country: Zone,
                             year: int,
                             allow_capex_optimization=False) -> dict[FlexibleSourceType, dict]:
        pemmdb_country = _map_to_pemmdb_region(country)
        sources: dict[FlexibleSourceType, dict] = {}

        if pemmdb_country not in self._df_sources(year).index:
            raise ValueError(f"Country ‘{pemmdb_country}’ not available in PEMMDB dataset")

        for pemmdb_key, (source_type, template) in _PEMMDB_2023_FLEXIBLE_SOURCES_MAP.items():
            installed_mw = float(self._df_sources(year).loc[pemmdb_country, pemmdb_key])
            # Ignore sources below 100 kW.
            if installed_mw < .1:
                continue
            source = template | {
                "capacity_mw": installed_mw,
                "min_capacity_mw": 0 if allow_capex_optimization else installed_mw,
            }
            sources[source_type] = source

        return sources

    @cache
    def get_installed(self, country: Zone, year: int) -> dict[BasicSourceType, float]:
        pemmdb_country = _map_to_pemmdb_region(country)
        sources: dict[BasicSourceType, float] = {}

        if pemmdb_country not in self._df_sources(year).index:
            raise ValueError(f"Country ‘{pemmdb_country}’ not available in PEMMDB dataset")

        for pemmdb_key, source_type in _PEMMDB_2023_BASIC_SOURCES_MAP.items():
            installed_mw = float(self._df_sources(year).loc[pemmdb_country, pemmdb_key])
            sources[source_type] = installed_mw / 1000

        return sources

    def get_interconnectors(self,
                            year: int,
                            countries: Optional[Collection[Zone]] = None,
                            aggregate_countries: Optional[Collection[AggregateRegion]] = None,
                            choke_factor: float = 1.0) \
            -> InterconnectorsDict:
        df_intercon = self._df_intercon(year)

        countries = {_map_to_pemmdb_region(c) for c in countries} if countries else None

        map_from_to: dict[Region, dict[Region, dict]] = defaultdict(dict)
        for (ix_from, ix_to), value_mw in df_intercon.iterrows():
            region_from = Region(ix_from)
            region_to = Region(ix_to)
            if countries and (region_from not in countries or region_to not in countries):
                continue
            capacity_mw = value_mw.iloc[0] * choke_factor
            map_from_to[region_from][region_to] = {
                "capacity_mw": capacity_mw,
                "loss": _generic_transmission_loss,
                "paid_off_capacity_mw": capacity_mw,
            }

        # Rename the PEMMDB region names back to our names.
        def rename_dict(d: dict[Region, Any], pemmdb_name: str, country: Zone):
            if pemmdb_name in d:
                d[country] = d[pemmdb_name]
                del d[pemmdb_name]

        for country, pemmdb_name in _PEMMDB_COUNTRY_MAP.items():
            rename_dict(map_from_to, pemmdb_name, country)
            for to_dict in map_from_to.values():
                rename_dict(to_dict, pemmdb_name, country)

        add_distances_type_and_loss_to_interconnectors(map_from_to)

        if aggregate_countries:
            map_from_to = aggregate_interconnectors(map_from_to, aggregate_countries)

        return dict(map_from_to)

    def get_load_factors(self, country: Zone, year: int) -> LoadFactors:
        # TODO: Be smarter about this? Do we need more precision right now?
        return {
            "heat_pumps_cooling_share": (.5, .5),
            "heat_pumps_share": (0, 0),
            "load_base": 1,
        }

    def get_reserve_requirements(self, country: Zone, year: int) -> Optional[Reserves]:
        pemmdb_country = _map_to_pemmdb_region(country)

        if (pemmdb_country, year) not in self._df_reserves.index:
            return

        pemmdb_reserves = self._df_reserves.loc[[(pemmdb_country, year)]].iloc[0]

        reserves = Reserves(
            additional_load_mw=pemmdb_reserves[
                "Sum of reserves provided by thermal units (MW)"
            ],
            hydro_capacity_reduction_mw=pemmdb_reserves[
                "Sum of reserves provided by hydro units (MW)"
            ],
        )

        return reserves

    def get_storage(self,
                    country: Zone,
                    year: int,
                    allow_capex_optimization=False) -> list[dict]:
        pemmdb_country = _map_to_pemmdb_region(country)
        storage_list: list[dict] = []

        df_sources = self._df_sources(year)
        df_storage = self._df_storage(year)
        if pemmdb_country not in df_sources.index:
            raise ValueError(f"Country ‘{pemmdb_country}’ not available in PEMMDB dataset")

        sources_series = df_sources.loc[pemmdb_country]
        storage_series = df_storage.loc[pemmdb_country]

        # Load Li-ion batteries parameters.
        batteries = _pemmdb_load_batteries(sources_series, storage_series, allow_capex_optimization)
        if batteries:
            storage_list.append(batteries)

        ror = _pemmdb_load_ror_hydro(sources_series)
        if ror:
            storage_list.append(ror)

        pondage = _pemmdb_load_pondage_hydro(sources_series, storage_series)
        if pondage:
            storage_list.append(pondage)

        pumped = _pemmdb_load_pumped_hydro(sources_series, storage_series)
        if pumped:
            storage_list.extend(pumped)

        reservoir = _pemmdb_load_reservoir_hydro(sources_series, storage_series)
        if reservoir:
            storage_list.append(reservoir)

        return storage_list
