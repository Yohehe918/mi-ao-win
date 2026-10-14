"""ATVV 协议与 ADPCM 解码测试。

测试向量移植自 mi-ao 的 Tests/MiAoTests/ProtocolTests.swift，断言值保持一致。
"""

from miao.adpcm import ADPCMDecoder
from miao.atvv import (
    ATVVCapabilities,
    ATVVCodec,
    ATVVProtocol,
    ATVVVersion,
)
from miao.audio import linear_resample


def test_parses_v04_capabilities():
    data = bytes([0x0B, 0x00, 0x04, 0x00, 0x01, 0x00, 0x86, 0x00, 0x14])
    capabilities = ATVVProtocol.parse_capabilities(data)
    assert capabilities == ATVVCapabilities(
        version=ATVVVersion.V04, codecs=0x01, interaction_model=0, frame_size=134
    )


def test_parses_v10_capabilities():
    data = bytes([0x0B, 0x01, 0x00, 0x03, 0x03, 0x00, 0x80])
    capabilities = ATVVProtocol.parse_capabilities(data)
    assert capabilities == ATVVCapabilities(
        version=ATVVVersion.V10, codecs=0x03, interaction_model=0x03, frame_size=128
    )
    assert capabilities.selected_codec == ATVVCodec.ADPCM_16K


def test_parses_xiaomi_firmware_2671_swapped_v10_capabilities():
    data = bytes([0x0B, 0x01, 0x00, 0x00, 0x03, 0x00, 0x78, 0x00, 0x00])
    capabilities = ATVVProtocol.parse_capabilities(data)
    assert capabilities == ATVVCapabilities(
        version=ATVVVersion.V10, codecs=0x03, interaction_model=0x00, frame_size=120
    )
    assert capabilities.selected_codec == ATVVCodec.ADPCM_16K


def test_decodes_silent_v04_frame():
    handler = ATVVProtocol()
    handler.accept_capabilities(
        ATVVCapabilities(version=ATVVVersion.V04, codecs=0x01, interaction_model=0, frame_size=134)
    )
    frame = bytearray(134)
    frame[1] = 1
    decoded = handler.decode_audio(bytes(frame))
    assert decoded is not None
    sequence, samples = decoded
    assert sequence == 1
    assert len(samples) == 257
    assert all(abs(s) < 256 for s in samples)


def test_resamples_eight_to_sixteen_kilohertz():
    output = linear_resample([0, 100, 200, 300], 8_000, 16_000)
    assert len(output) == 8
    assert output[0] == 0


def test_adpcm_known_vector():
    # 手工计算：predictor=0, step_index=0
    # nibble 7：diff = 7>>3 + 7 + 7>>1 + 7>>2 = 0+7+3+1 = 11；step_index += 8
    # 第二个 nibble 7：step = stepTable[8] = 16，diff = 2+16+8+4 = 30 → 41
    decoder = ADPCMDecoder()
    assert decoder.decode([0x77]) == [11, 41]


def test_v10_sequence_wraps_and_sync_resets_decoder():
    from miao.atvv import AudioSync

    handler = ATVVProtocol()
    handler.accept_capabilities(
        ATVVCapabilities(version=ATVVVersion.V10, codecs=0x02, interaction_model=0x03, frame_size=120)
    )
    handler.apply_audio_sync(AudioSync(codec=ATVVCodec.ADPCM_16K, sequence=42, predictor=0, step_index=0))
    first = handler.decode_audio(bytes(120))
    second = handler.decode_audio(bytes(120))
    assert first is not None and second is not None
    assert first[0] == 42
    assert second[0] == 43
    assert len(second[1]) == 240
