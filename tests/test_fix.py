import pytest

from fixsim.fix import SOH, FixError, MsgType, Tag, decode, encode, split_frames


def sample() -> bytes:
    return encode([(Tag.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (Tag.CL_ORD_ID, "C1"), (Tag.SYMBOL, "AAPL"),
                   (Tag.SIDE, "1"), (Tag.ORDER_QTY, 100), (Tag.ORD_TYPE, "2"), (Tag.PRICE, 190.0)])


def test_round_trip_keeps_header_order_and_values():
    msg = decode(sample())
    assert [t for t, _ in msg.fields][:3] == [8, 9, 35]
    assert msg.get(Tag.SYMBOL) == "AAPL" and msg.get(Tag.PRICE) == "190"


def test_body_length_and_checksum_follow_the_spec():
    raw = sample().decode()
    body = raw[raw.index("35="):raw.rindex("10=")]
    assert f"9={len(body)}{SOH}" in raw
    assert raw.endswith(f"10={sum(raw[:raw.rindex('10=')].encode()) % 256:03d}{SOH}")


def test_wrong_checksum_is_rejected():
    raw = sample()
    tampered = raw[:-4] + b"000" + SOH.encode() if not raw.endswith(b"10=000\x01") else raw[:-4] + b"001\x01"
    with pytest.raises(FixError, match="CheckSum"):
        decode(tampered)


def test_wrong_body_length_is_rejected():
    raw = sample().replace(b"55=AAPL", b"55=AAPLX")
    with pytest.raises(FixError, match="BodyLength"):
        decode(raw)


def test_stream_framing_splits_messages_and_keeps_partial_tail():
    one, two = sample(), sample()
    frames, rest = split_frames(one + two + two[:10])
    assert frames == [one, two]
    assert rest == two[:10]
