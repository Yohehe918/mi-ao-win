from miao.buttons import RemoteButton
from miao.dead_key_report import ExtraKeyReducer, report_usages


def report(*usages):
    return b'\x01\x00\x00'+b''.join(u.to_bytes(2,'little') for u in (*usages,0,0,0)[:3])


def test_three_missing_usages_decode_without_guessing_virtual_keys():
    assert report_usages(report(0xF1,0x80,0x81)) == frozenset({0xF1,0x80,0x81})


def test_invalid_report_differs_from_valid_release():
    assert report_usages(report()) == frozenset()
    assert report_usages(b'') is None
    assert report_usages(b'\x02'+report()[1:]) is None


def test_repeat_and_release_edges_are_balanced():
    reducer=ExtraKeyReducer()
    assert reducer.feed(report(0xF1)) == [(RemoteButton.BACK,True)]
    assert reducer.feed(report(0xF1)) == []
    assert reducer.feed(b'bad') == []
    assert reducer.feed(report(0x80)) == [(RemoteButton.BACK,False),(RemoteButton.VOLUME_UP,True)]
    assert reducer.feed(report()) == [(RemoteButton.VOLUME_UP,False)]


def test_regular_keys_never_duplicate_existing_raw_input():
    reducer=ExtraKeyReducer()
    assert reducer.feed(report(0x28,0x52)) == []
    assert reducer.reset() == []


def test_disconnect_releases_each_extra_key_once():
    reducer=ExtraKeyReducer()
    reducer.feed(report(0x80,0x81))
    assert reducer.reset() == [(RemoteButton.VOLUME_UP,False),(RemoteButton.VOLUME_DOWN,False)]
    assert reducer.reset() == []
