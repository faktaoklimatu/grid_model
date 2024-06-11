"""
Provides a class for regions (and countries in entsoe data).
"""

Region = str
"""
Abstract class for a region for parts of code that does not care about it's semantics.

It is internally a string to allow easy manipulations and type casting.
TODO: potentially avoid string manipulations and convert it to enums.
"""


class Zone(Region):
    """
    Zone is a region for which we have hourly data. Each Zone exists in ENTSO-E Transparency Platform (or other entity from which we source data). Technically, it is either a bidding zone or a control area or a country. For the time being, the exact differentiation between those concepts is not needed.
    """


class AggregateRegion(Region):
    """
    AggregateRegion is an aggregate of multiple atomic Zones.
    """


AUSTRIA = Zone("AT")
BELGIUM = Zone("BE")
BULGARIA = Zone("BG")
CROATIA = Zone("HR")
CYPRUS = Zone("CY")
CZECHIA = Zone("CZ")
DENMARK = Zone("DK")
ESTONIA = Zone("EE")
FINLAND = Zone("FI")
FRANCE = Zone("FR")
GERMANY = Zone("DE")
GREECE = Zone("GR")
HUNGARY = Zone("HU")
ITALY = Zone("IT")
IRELAND = Zone("IE")
LATVIA = Zone("LV")
LITHUANIA = Zone("LT")
LUXEMBOURG = Zone("LU")
MALTA = Zone("MT")
NETHERLANDS = Zone("NL")
POLAND = Zone("PL")
PORTUGAL = Zone("PT")
ROMANIA = Zone("RO")
SLOVAKIA = Zone("SK")
SLOVENIA = Zone("SI")
SPAIN = Zone("ES")
SWEDEN = Zone("SE")

# Subzones of countries
DENMARK_1 = Zone("DK-DK1")
DENMARK_2 = Zone("DK-DK2")
ITALY_NORTH = Zone("IT-NO")
ITALY_CENTRAL_NORTH = Zone("IT-CNO")
ITALY_CENTRAL_SOUTH = Zone("IT-CSO")
ITALY_SOUTH = Zone("IT-SO")
ITALY_SICILY = Zone("IT-SIC")
ITALY_SARDINIA = Zone("IT-SAR")
NORWAY_1 = Zone("NO-NO1")
NORWAY_2 = Zone("NO-NO2")
NORWAY_3 = Zone("NO-NO3")
NORWAY_4 = Zone("NO-NO4")
NORWAY_5 = Zone("NO-NO5")
SWEDEN_1 = Zone("SE-SE1")
SWEDEN_2 = Zone("SE-SE2")
SWEDEN_3 = Zone("SE-SE3")
SWEDEN_4 = Zone("SE-SE4")

# Superzones
GERMANY_LUXEMBOURG = Zone("DE-LU")
IRELAND_WHOLE = Zone("IE(SEM)")

# Schengen non-EU countries
GREAT_BRITAIN = Zone("GB")
GREAT_BRITAIN_NORTHERN_IRELAND = Zone("GB-NIR")
NORWAY = Zone("NO")
SWITZERLAND = Zone("CH")

ALBANIA = Zone("AL")
AZERBAIJAN = Zone("AZ")
BELARUS = Zone("BY")
BOSNIA_HERZEGOVINA = Zone("BA")
GEORGIA = Zone("GE")
MOLDOVA = Zone("MD")
MONTENEGRO = Zone("ME")
NORTH_MACEDONIA = Zone("MK")
KOSOVO = Zone("XK")
RUSSIA = Zone("RU")
RUSSIA_KALININGRAD = Zone("RU-KGD")
SERBIA = Zone("RS")
TURKEY = Zone("TR")
UKRAINE = Zone("UA")

# Our own aggregate regions
WHOLE_EU = AggregateRegion("EU")
# Coarse aggregates
NORDICS = AggregateRegion("Nord")
WEST = AggregateRegion("West")
SOUTH = AggregateRegion("South")
BALKANS = AggregateRegion("Balk")
# Midfine aggregates
BALTICS = AggregateRegion("Balt")
SCANDINAVIA = AggregateRegion("Scand")
ROMANIA_BULGARIA = AggregateRegion("RoBu")
SOUTH_CENTRAL_BALKAN = AggregateRegion("SC_Ba")
SLOVENIA_CROATIA_HUNGARY = AggregateRegion("SI_HR_HU")
FRANCE_SWITZERLAND = AggregateRegion("FrCh")
# Fine aggregates
ESTONIA_LATVIA = AggregateRegion("EstLat")
BRITISH_ISLES = AggregateRegion("Brit")
BENELUX = AggregateRegion("Bnl")
BELUX = AggregateRegion("Blx")
IBERIA = AggregateRegion("Iber")
WEST_BALKAN = AggregateRegion("W_Ba")
SOUTH_BALKAN = AggregateRegion("S_Ba")
CENTRAL_BALKAN = AggregateRegion("C_Ba")


# Exclusing Kosovo which is missing in the Ember dataset and Albania which is missing in entsoe.
__aggregate_region_map: dict[AggregateRegion, set[Zone]] = {
    WHOLE_EU: {AUSTRIA, BELGIUM, BULGARIA, CROATIA, CYPRUS, CZECHIA, DENMARK, ESTONIA, FINLAND,
               FRANCE, GERMANY, GREECE, HUNGARY, ITALY, IRELAND, LATVIA, LITHUANIA, LUXEMBOURG,
               MALTA, NETHERLANDS, POLAND, PORTUGAL, ROMANIA, SLOVAKIA, SLOVENIA, SPAIN, SWEDEN},
    # Coarse aggregation.
    NORDICS: {DENMARK, SWEDEN, FINLAND, NORWAY, ESTONIA, LATVIA, LITHUANIA},
    WEST: {FRANCE, LUXEMBOURG, NETHERLANDS, BELGIUM, SWITZERLAND},
    SOUTH: {ITALY, SLOVENIA, CROATIA},
    BRITISH_ISLES: {GREAT_BRITAIN, IRELAND},
    IBERIA: {SPAIN, PORTUGAL},
    BALKANS: {HUNGARY, ROMANIA, BULGARIA, SERBIA, BOSNIA_HERZEGOVINA, MONTENEGRO, NORTH_MACEDONIA,
              GREECE},
    # Mid-granular aggregation, this also uses BRITISH_ISLES and IBERIA.
    BALTICS: {ESTONIA, LATVIA, LITHUANIA},
    SCANDINAVIA: {SWEDEN, FINLAND, NORWAY, DENMARK},
    BENELUX: {BELGIUM, NETHERLANDS, LUXEMBOURG},
    ROMANIA_BULGARIA: {ROMANIA, BULGARIA},
    SOUTH_CENTRAL_BALKAN: {NORTH_MACEDONIA, MONTENEGRO, GREECE, SERBIA, BOSNIA_HERZEGOVINA},
    SLOVENIA_CROATIA_HUNGARY: {SLOVENIA, CROATIA, HUNGARY},
    FRANCE_SWITZERLAND: {FRANCE, SWITZERLAND},
    # Finer aggregation, this also uses BRITISH_ISLES, IBERIA, and {WEST, CENTRAL}_BALKAN
    WEST_BALKAN: {SLOVENIA, CROATIA},
    CENTRAL_BALKAN: {SERBIA, BOSNIA_HERZEGOVINA},
    SOUTH_BALKAN: {NORTH_MACEDONIA, MONTENEGRO},
    ESTONIA_LATVIA: {ESTONIA, LATVIA},
    BELUX: {BELGIUM, LUXEMBOURG},
}


def get_aggregated_countries(region: AggregateRegion) -> set[Zone]:
    if region not in __aggregate_region_map:
        raise Exception(f"Unknown aggregate region {region} used")
    return __aggregate_region_map[region]
