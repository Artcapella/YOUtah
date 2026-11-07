from datetime import datetime, timezone

from backend.intelligence.service import comfort_features_from_readings
from backend.models import Coordinate, HourlyConditions
from backend.providers.utah_daq import STATIONS_BY_ID, nearest_stations, parse_station_feed, pm25_aqi

# Shape of https://air.utah.gov/csvFeed.php?id=slc: tab-separated, newest row first.
FEED = "\n".join([
    "dtstamp\tozone\tpm25\tpm25_24hr_avg\ttemp\trh\tws\twd",
    "11/06/2026 18:00:00\t0.022\t9.9\t10.2\t47.95\t69\t2.5\t216",
    "11/06/2026 17:00:00\t0.03\t10.1\t10\t50.45\t65\t4.1\t275",
    "11/06/2026 16:00:00\t0.033\t16.1\t9.9\t-9999\t65\t5.0\t272",
    "11/06/2026 15:00:00\t0.036\t13.9\t9.5\t53.71\t62\t4.0\t271",
    "",
])


def test_parser_keeps_newest_first_and_converts_mst_to_utc() -> None:
    readings = parse_station_feed("slc", FEED)
    assert [reading.pm25_24hr_avg for reading in readings] == [10.2, 10, 9.9, 9.5]
    # 18:00 Mountain Standard Time is 01:00 UTC the next day.
    assert readings[0].observed_at == datetime(2026, 11, 7, 1, tzinfo=timezone.utc)
    assert readings[0].wind_mph == 2.5


def test_parser_turns_sentinel_into_none() -> None:
    assert parse_station_feed("slc", FEED)[2].temp_f is None


def test_parser_orders_rows_even_if_feed_order_changes() -> None:
    header, *rows = FEED.strip().split("\n")
    readings = parse_station_feed("slc", "\n".join([header, *reversed(rows)]))
    assert readings[0].pm25_24hr_avg == 10.2


def test_nearest_station_to_downtown_salt_lake_is_hawthorne() -> None:
    station, distance_km = nearest_stations(Coordinate(lat=40.7608, lng=-111.8910))[0]
    assert station.id == "slc"
    assert distance_km < 5


def test_pm25_aqi_uses_2024_breakpoints() -> None:
    assert pm25_aqi(9.0) == (50, "Good")
    assert pm25_aqi(35.4) == (100, "Moderate")
    assert pm25_aqi(55.5) == (151, "Unhealthy")


def test_features_use_station_elevation_trend_and_hourly_pm25() -> None:
    features = comfort_features_from_readings(STATIONS_BY_ID["slc"], parse_station_feed("slc", FEED))
    assert features is not None
    assert features.pm25_24hr_avg == 10.2
    assert features.pm25_hourly == 9.9
    assert round(features.pm25_trend_3h, 2) == 0.7  # 10.2 now minus 9.5 three hours earlier
    assert features.elevation_m == STATIONS_BY_ID["slc"].elevation_m


def test_missing_station_weather_falls_back_to_nws() -> None:
    header, newest, *rest = FEED.strip().split("\n")
    newest = newest.replace("47.95", "-9999")
    weather = HourlyConditions(starts_at=datetime.now(timezone.utc), temperature_f=44, relative_humidity=80, wind_mph=3, precip_probability=30)
    features = comfort_features_from_readings(STATIONS_BY_ID["slc"], parse_station_feed("slc", "\n".join([header, newest, *rest])), weather)
    assert features is not None
    assert features.temp_f == 44
    assert features.precip_probability == 30
