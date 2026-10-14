"""注入器的纯逻辑与剪贴板测试（不触发按键注入，避免干扰桌面）。"""

from miao.injector import (
    read_clipboard_text,
    should_restore_clipboard,
    write_clipboard_text,
)


def test_restore_policy_matches_original():
    # 对齐 PasteboardRestorePolicy：剪贴板未被改动才恢复
    assert should_restore_clipboard("我们写入的", "我们写入的")
    assert not should_restore_clipboard("我们写入的", "用户复制了新内容")
    assert should_restore_clipboard(None, None)


def test_clipboard_round_trip():
    snapshot = read_clipboard_text()
    try:
        write_clipboard_text("米遥剪贴板测试")
        assert read_clipboard_text() == "米遥剪贴板测试"
    finally:
        if snapshot is not None:
            write_clipboard_text(snapshot)


def test_empty_transcript_gate():
    from miao.injector import inject_text

    assert inject_text("   ") is False
    assert inject_text("") is False
