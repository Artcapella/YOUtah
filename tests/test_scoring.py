from datetime import datetime, timedelta, timezone

from backend.intelligence.schemas import ComfortFeatures, EventSignal
from backend.intelligence.scoring import fallback_comfort, local_pulse


def features(pm25: float, wind_mph: float = 4) -> ComfortFeatures:
    return ComfortFeatures(
        pm25_24hr_avg=pm25,
        temp_f=40,
        humidity=50,
        wind_mph=wind_mph,
        elevation_m=1300,
        precip_probability=0,
        observed_at=datetime.now(timezone.utc),
    )


def test_fallback_marks_high_pm25_poor() -> None:
    prediction = fallback_comfort(features(48, wind_mph=1))
    assert prediction.label == "poor"
    assert prediction.model_kind == "conservative_fallback"


def test_pulse_rewards_nearby_events_but_does_not_claim_crowds() -> None:
    now = datetime.now(timezone.utc)
    event = EventSignal(
        id="e1", title="Free cultural night", starts_at=now + timedelta(hours=1),
        lat=40.7608, lng=-111.8910, category="arts", is_free=True,
    )
    comfort = fallback_comfort(features(8))
    pulse = local_pulse(40.7608, -111.8910, now, [event], comfort)
    assert pulse.nearby_event_count == 1
    assert pulse.kind == "explainable_estimate"
    assert pulse.score > 25
