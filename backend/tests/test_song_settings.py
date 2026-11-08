from app.images import song_settings

ACE = {"model_type": "ace_step_v1_5_turbo_lm_1_7b", "model_mode": 2,
       "alt_prompt": "90s grunge rock, distorted guitars, heavy drums, raspy male vocals, 117 bpm"}


def test_ace_step_keeps_the_owners_caption_and_tempo():
    s = song_settings(ACE)
    assert s["model_mode"] == 1  # fill in key and meter, but don't rewrite the caption
    assert s["custom_settings"] == {"bpm": 117}
    assert s["alt_prompt"] == ACE["alt_prompt"]
    assert song_settings(ACE | {"model_mode": 3})["model_mode"] == 4


def test_no_tempo_or_other_models_are_left_alone():
    s = song_settings(ACE | {"alt_prompt": "dreamy synth pop", "model_mode": 0})
    assert s["model_mode"] == 0 and "custom_settings" not in s
    assert song_settings(ACE | {"alt_prompt": "fast, 1170 bpm"}).get("custom_settings") is None
    other = {"model_type": "ace_step_v1", "model_mode": 2, "alt_prompt": "rock, 117 bpm"}
    assert song_settings(other) == other
