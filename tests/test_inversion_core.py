from backend.inversion.core import (daily_outlook, estimate_lid, gaussian_pm, headline, judge_trailheads,
                                    pm_category, smog_cells)


def st(elevs, temps):
    return [{"elevation_m": e, "air_temp_c": t} for e, t in zip(elevs, temps)]


INV = {"has_inversion": True, "lid_m": 1700}
NOINV = {"has_inversion": False, "lid_m": None}


def test_normal_lapse_no_inversion():
    assert estimate_lid(st([1300, 1600, 1900, 2200], [5, 3, 1, -1]))["has_inversion"] is False


def test_lid_found():
    r = estimate_lid(st([1290, 1310, 1500, 1700, 1900, 2400], [-6, -5, -1, 2, 1, -3]))
    assert r["has_inversion"] is True and r["lid_m"] == 1700


def test_too_few_and_missing_values():
    assert estimate_lid(st([1300, 2000], [-5, 3]))["reason"] == "not enough stations"
    r = estimate_lid(st([1290, 1310, 1500, 1700, 1900, 2400, 1800], [-6, -5, -1, 2, 1, -3, None]))
    assert r["lid_m"] == 1700


def test_small_warm_blip_is_not_inversion():
    assert estimate_lid(st([1290, 1300, 1400, 1410, 2000, 2600], [8, 8, 9, 9.5, 5, 1]))["has_inversion"] is False


def test_pm_category_epa():
    assert [pm_category(v) for v in [5, 9.0, 20, 40, 80, None]] == [0, 0, 1, 2, 3, 1]


TH = [{"name": "Low", "lat": 40.76, "lon": -111.84, "elevation_m": 1650},
      {"name": "Mid", "lat": 40.70, "lon": -111.80, "elevation_m": 1810},
      {"name": "High", "lat": 40.60, "lon": -111.65, "elevation_m": 2200}]


def test_above_lid_go_below_lid_follows_air():
    r = {t["name"]: t["verdict"] for t in judge_trailheads(TH, INV, 45, 40.7649, -111.8421)}
    assert r == {"Low": "CAUTION", "Mid": "GO", "High": "GO"}
    r = {t["name"]: t["verdict"] for t in judge_trailheads(TH, INV, 70, 40.7649, -111.8421)}
    assert r["Low"] == "SKIP"


def test_go_first_then_distance():
    out = judge_trailheads(TH, NOINV, 5, 40.7649, -111.8421)
    assert [t["name"] for t in out] == ["Low", "Mid", "High"]


def test_headline_cases():
    assert headline(INV, 45)["tone"] == "bad"
    assert headline(INV, 5)["tone"] == "warn"
    assert "not from an inversion" in headline(NOINV, 45)["title"]
    assert headline(NOINV, 5)["tone"] == "good"


MON = [{"lat": 40.70, "lon": -111.95, "pm25": 40.0}, {"lat": 40.70, "lon": -111.80, "pm25": 10.0}]


def test_gaussian():
    assert abs(gaussian_pm(40.70, -111.95, MON)[0] - 40) < 3
    assert abs(gaussian_pm(40.70, -111.875, MON)[0] - 25) < 1
    v, d = gaussian_pm(41.5, -110.0, MON)
    assert 10 <= v <= 40 and d > 100


def test_smog_cut_above_lid():
    g = [{"lat": 40.70, "lon": -111.95, "elevation_m": 1300}, {"lat": 40.70, "lon": -111.95, "elevation_m": 2200}]
    out = smog_cells(g, MON, 1800)
    assert out[0]["pm25"] > 30 and out[1]["pm25"] < 5
    assert smog_cells(g[1:], MON, None)[0]["pm25"] > 30


def _hours(inv_c, blh, days=4):
    return [{"time": f"2026-11-{7 + d:02d}T{h:02d}:00", "temperature_2m": 0.0, "temperature_850hPa": inv_c,
             "boundary_layer_height": blh} for d in range(days) for h in range(24)]


def test_outlook():
    d = daily_outlook(_hours(4.0, 300.0), 1300)
    assert all(x["risk"] == "High" for x in d) and d[0]["lid_elevation_m"] == 1600
    assert d[0]["confidence"] == "forecast" and d[3]["confidence"] == "trend"
    assert all(x["risk"] == "Low" for x in daily_outlook(_hours(-3.0, 1500.0), 1300))


def test_nearby_radius_and_minimum():
    from backend.inversion.core import nearby
    pts = [{"name": n, "lat": 40.76 - i * 0.5, "lon": -111.84} for i, n in enumerate("abcde")]  # ~55 km apart
    assert [p["name"] for p in nearby(pts, 40.76, -111.84, 60)] == ["a", "b"]
    assert [p["name"] for p in nearby(pts, 40.76, -111.84, 10, min_count=3)] == ["a", "b", "c"]


def test_headline_admits_missing_data():
    assert headline({"has_inversion": False, "lid_m": None, "reason": "not enough stations"}, None)["tone"] == "info"


def test_elevation_bands_keep_mountain_stations():
    from backend.inversion.sources import _by_elevation_band
    valley = [{"elevation_m": 1300 + i % 50} for i in range(200)]
    mountain = [{"elevation_m": 2600}, {"elevation_m": 3100}]
    out = _by_elevation_band(valley + mountain)
    assert {2600, 3100} <= {s["elevation_m"] for s in out} and len(out) < 20
