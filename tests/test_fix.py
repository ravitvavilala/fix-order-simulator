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


def test_encode_refuses_a_tag_without_a_value():
    for bad in (None, ""):
        with pytest.raises(FixError, match="no value"):
            encode([(Tag.MSG_TYPE, "0"), (Tag.TEXT, bad)])


def test_non_ascii_bytes_are_garbled_not_a_crash():
    raw = sample().replace(b"55=AAPL", b"55=AAP\xc3")
    with pytest.raises(FixError):
        decode(raw)


def framed(body: str) -> bytes:
    raw = f"8=FIX.4.4{SOH}9={len(body)}{SOH}{body}".encode()
    return raw + f"10={sum(raw) % 256:03d}{SOH}".encode()


def test_intact_message_with_empty_field_keeps_its_header_and_records_the_problem():
    msg = decode(framed(f"35=0{SOH}34=7{SOH}58={SOH}"))
    assert msg.get(Tag.MSG_SEQ_NUM) == "7"
    assert (msg.error.ref_tag, msg.error.reason) == (58, 4)


@pytest.mark.parametrize("body, tag, reason", [
    (f"35=D{SOH}34=2{SOH}55=AAPL{SOH}55=MSFT{SOH}", 55, 13),
    (f"35=0{SOH}34=2{SOH}0112=X{SOH}", None, 0),
    (f"35=0{SOH}34=2{SOH}abc=1{SOH}", None, 0),
])
def test_duplicate_and_malformed_tags_are_field_errors(body, tag, reason):
    msg = decode(framed(body))
    assert (msg.error.ref_tag, msg.error.reason) == (tag, reason)
    assert msg.get(Tag.SYMBOL) in (None, "AAPL")


def test_framing_survives_a_read_that_ends_inside_the_begin_string():
    one = sample()
    frames, rest = split_frames(one[:1])
    frames2, _ = split_frames(rest + one[1:])
    assert frames == [] and frames2 == [one]


def test_framing_finds_messages_after_newlines_and_leading_garbage():
    one, two = sample(), sample()
    frames, rest = split_frames(b"junk" + one + b"\r\n" + two + b"\n")
    assert frames == [one, two] and rest == b""


def test_framing_is_the_same_whatever_way_tcp_splits_the_bytes():
    stream = b"xyz" + sample() + b"\n" + sample()
    for size in (1, 2, 3, 7, 50, len(stream)):
        frames, buffer = [], b""
        for i in range(0, len(stream), size):
            got, buffer = split_frames(buffer + stream[i:i + size])
            frames += got
        assert frames == [sample(), sample()], size


def test_begin_string_text_inside_a_value_does_not_start_a_frame():
    inside = encode([(Tag.MSG_TYPE, MsgType.HEARTBEAT), (Tag.TEXT, "copy of 8=FIX.4.4 here")])
    frames, _ = split_frames(inside + sample())
    assert frames == [inside, sample()]


def test_a_truncated_message_does_not_swallow_the_next_one():
    frames, _ = split_frames(sample()[:30] + sample())
    assert frames == [sample()]
