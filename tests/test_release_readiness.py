import dataclasses
import hashlib
from pathlib import Path
import zipfile

from miao.buttons import ButtonAction, ButtonPresetCatalog, RemoteButton, default_standard_preset
from miao.config import load_config, save_config, Config
from miao.readiness import Check, check_audio, check_doubao, unpack_cable
from miao.setup_wizard import completion_errors, PROVIDERS
from miao.single_instance import SingleInstance


def test_first_run_has_keyboard_directions_and_no_broken_tv_action(tmp_path):
    config = load_config(tmp_path / 'config.json')
    preset = ButtonPresetCatalog().preset(config.active_preset_id)
    assert preset.binding_for(RemoteButton.DPAD_LEFT).action is ButtonAction.KEYBOARD_ARROW_LEFT
    assert preset.binding_for(RemoteButton.TV).kind == 'none'
    assert config.device_address is None and not config.enhanced_keys_enabled
    assert config.doubao_hotkey_modifiers == ['right_alt']


def test_upgrade_preserves_explicit_user_values_and_does_not_mark_acceptance(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{"active_preset_id":"user.mine","device_address":"AA:BB:CC:DD:EE:FF","doubao_hotkey_vk":90,"enhanced_keys_enabled":true}', encoding='utf-8')
    config = load_config(path)
    assert config.active_preset_id == 'user.mine' and config.doubao_hotkey_vk == 90
    assert config.enhanced_keys_enabled and not config.setup_completed
    save_config(config, path)
    assert load_config(path) == config


def test_audio_requires_both_correct_endpoints():
    devices = [{'name':'CABLE Input','max_output_channels':2,'max_input_channels':0}]
    assert not check_audio(devices).ready
    devices.append({'name':'CABLE Output','max_output_channels':0,'max_input_channels':2})
    assert check_audio(devices).ready
    devices[1]['name'] = 'Laptop microphone'
    assert not check_audio(devices).ready


def test_unknown_doubao_hash_is_never_accepted(tmp_path, monkeypatch):
    folder = tmp_path / 'v0.9.2.7'
    folder.mkdir()
    (folder / 'rpc.dll').write_bytes(b'unknown')
    assert not check_doubao([tmp_path]).ready
    monkeypatch.setattr('miao.readiness.DLL_SHA256', hashlib.sha256(b'unknown').hexdigest())
    assert check_doubao([tmp_path]).ready
    folder.rename(tmp_path / 'v9.0')
    assert not check_doubao([tmp_path]).ready


def test_setup_cannot_succeed_using_only_software_checks():
    checks = {key:Check(key,key,True,'') for key in ('system','audio','provider')}
    errors = completion_errors(checks,PROVIDERS[0],False,False,True,object(),False)
    assert len(errors) == 3
    assert not completion_errors(checks,PROVIDERS[0],True,True,True,object(),True)
    assert completion_errors(checks,PROVIDERS[0],True,True,False,object(),True)
    checks['provider'] = Check('provider','',False,'')
    assert completion_errors(checks,PROVIDERS[0],True,True,True,object(),True)
    assert not completion_errors(checks,PROVIDERS[1],True,True,True,object(),True)
    assert not completion_errors({'system':checks['system']},PROVIDERS[2],True,True,False,None,True)
    assert not completion_errors({'system':checks['system']},PROVIDERS[2],False,True,False,None,True)


def test_vendor_archive_hash_and_path_traversal_are_rejected(tmp_path, monkeypatch):
    archive = tmp_path / 'driver.zip'
    with zipfile.ZipFile(archive,'w') as package:
        package.writestr('../outside.exe', b'bad')
    try:
        unpack_cable(archive,tmp_path/'out')
        assert False
    except RuntimeError as error:
        assert '校验' in str(error)
    monkeypatch.setattr('miao.readiness.CABLE_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    try:
        unpack_cable(archive,tmp_path/'out')
        assert False
    except RuntimeError as error:
        assert '路径' in str(error)
    assert not (tmp_path/'outside.exe').exists()


def test_single_instance_wakes_existing_owner_and_allows_clean_restart():
    import uuid
    name = 'Local\\MiAoWin.Test.'+uuid.uuid4().hex
    first = SingleInstance(name)
    second = SingleInstance(name)
    try:
        assert first.primary and not second.primary
        assert first.consume_show() and not first.consume_show()
    finally:
        second.close()
        first.close()
    third = SingleInstance(name)
    try:
        assert third.primary
    finally:
        third.close()
