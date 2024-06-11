library(arrow)
library(readxl)
library(tidyverse)

pecd_year <- 2025
spreadsheet_filename <- paste0("./Demand_Timeseries_TY", pecd_year, ".xlsx")
parquet_filename <- paste0("PECD-country-demand_national_estimates-", pecd_year, ".parquet")

# Transform a zone identifier to a country code by picking the first
# two letters only.
zone_to_country_code <- \(zone) substr(zone, 1, 2)
# Transform the wide format of the original spreadsheet into a long
# format with the appropriate column names.
to_long_tibble <- \(.data) {
  .data |>
    separate_wider_delim(Date, ".", names = c("day", "month", NA)) |>
    rename(hour = Hour) |>
    mutate(across(c(day, month), as.numeric)) |>
    pivot_longer(
      !c(hour, day, month, country),
      names_to = "year",
      names_transform = list(year = as.integer),
      values_to = "dem_MW"
    )
}

all_countries_long <- spreadsheet_filename |>
  excel_sheets() |>
  map(
    ~ read_excel(spreadsheet_filename, sheet = .x, skip = 1) |>
      mutate(country = zone_to_country_code(.x))
  ) |>
  map(to_long_tibble) |>
  list_rbind() |>
  # Sum demand across zones within a single country.
  summarise(
    dem_MW = sum(dem_MW),
    .by = c(country, year, month, day, hour)
  ) |>
  arrange(country, year, month, day, hour)

all_countries_long |>
  write_parquet(parquet_filename)

