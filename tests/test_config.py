"""配置读写测试。"""

from miao.config import Config, load_config, save_config


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = Config(device_name_filter="米家", mic_gain_db=12.0, submit_enabled=False)
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.device_name_filter == "米家"
    assert loaded.mic_gain_db == 12.0
    assert loaded.submit_enabled is False


def test_doubao_and_mic_defaults(tmp_path):
    config = load_config(tmp_path / "nope.json")
    assert config.mic_forward_enabled is True
    assert config.doubao_hotkey_vk == 66
    assert config.active_preset_id == "standard"


def test_missing_or_corrupt_config_returns_defaults(tmp_path):
    missing = load_config(tmp_path / "nope.json")
    assert missing.silence_timeout == 1.5

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_config(bad).silence_threshold == 35.0
