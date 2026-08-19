"""Packet parser tests.

Every byte a parser sees here came off the wire, which means an attacker chose
it. These tests are mostly about malformed input: the parser must discard a bad
frame, never raise, and never read past what it checked. A crash in the sensor
is a monitoring outage, which is precisely what an attacker would want.
"""

from __future__ import annotations

import socket
import struct

from nexus.sensors.packet_capture import parse_frame

MAC_A = bytes.fromhex("aabbccddeeff")
MAC_B = bytes.fromhex("112233445566")


def ethernet(payload: bytes, ethertype: int = 0x0800, vlan: bool = False) -> bytes:
    if vlan:
        return (
            MAC_B
            + MAC_A
            + struct.pack("!H", 0x8100)
            + struct.pack("!HH", 0x0064, ethertype)
            + payload
        )
    return MAC_B + MAC_A + struct.pack("!H", ethertype) + payload


def ipv4(
    payload: bytes,
    *,
    protocol: int = 6,
    source: str = "192.168.1.10",
    destination: str = "8.8.8.8",
    ihl: int = 5,
    ttl: int = 64,
) -> bytes:
    version_ihl = (4 << 4) | ihl
    header = struct.pack(
        "!BBHHHBBH4s4s",
        version_ihl,
        0,
        20 + len(payload),
        0,
        0,
        ttl,
        protocol,
        0,
        socket.inet_aton(source),
        socket.inet_aton(destination),
    )
    # Options padding when IHL claims a longer header.
    header += b"\x00" * ((ihl - 5) * 4)
    return header + payload


def tcp(source_port: int = 44444, destination_port: int = 443, flags: int = 0x02) -> bytes:
    return struct.pack("!HHIIBBHHH", source_port, destination_port, 0, 0, (5 << 4), flags, 0, 0, 0)


def udp(source_port: int = 5353, destination_port: int = 53) -> bytes:
    return struct.pack("!HHHH", source_port, destination_port, 8, 0)


class TestValidFrames:
    def test_ipv4_tcp(self) -> None:
        parsed = parse_frame(ethernet(ipv4(tcp())))
        assert parsed is not None
        assert parsed["source_ip"] == "192.168.1.10"
        assert parsed["destination_ip"] == "8.8.8.8"
        assert parsed["protocol"] == "TCP"
        assert parsed["destination_port"] == 443
        assert parsed["source_mac"] == "aa:bb:cc:dd:ee:ff"

    def test_tcp_flags_are_decoded(self) -> None:
        """SYN without ACK is a connection attempt; a burst of them across
        ports is a scan. The detection engine reads these rather than
        re-parsing packets."""
        syn = parse_frame(ethernet(ipv4(tcp(flags=0x02))))
        syn_ack = parse_frame(ethernet(ipv4(tcp(flags=0x12))))
        assert syn is not None and syn["tcp_flags"] == ["SYN"]
        assert syn_ack is not None and set(syn_ack["tcp_flags"]) == {"SYN", "ACK"}

    def test_ipv4_udp(self) -> None:
        parsed = parse_frame(ethernet(ipv4(udp(), protocol=17)))
        assert parsed is not None
        assert parsed["protocol"] == "UDP"
        assert parsed["destination_port"] == 53

    def test_ipv4_icmp(self) -> None:
        parsed = parse_frame(ethernet(ipv4(b"\x08\x00rest", protocol=1)))
        assert parsed is not None
        assert parsed["protocol"] == "ICMP"
        assert parsed["icmp_type"] == 8

    def test_ipv4_options_are_respected(self) -> None:
        """IHL can exceed 20 bytes. Assuming a fixed header would read option
        bytes as ports and report traffic that never happened."""
        parsed = parse_frame(ethernet(ipv4(tcp(destination_port=8443), ihl=7)))
        assert parsed is not None
        assert parsed["destination_port"] == 8443

    def test_vlan_tagged_frame(self) -> None:
        """A VLAN tag shifts the real ethertype by four bytes. Not handling it
        would misparse every frame on a tagged network."""
        parsed = parse_frame(ethernet(ipv4(tcp()), vlan=True))
        assert parsed is not None
        assert parsed["protocol"] == "TCP"

    def test_ipv6(self) -> None:
        source = socket.inet_pton(socket.AF_INET6, "fd00::1")
        destination = socket.inet_pton(socket.AF_INET6, "2606:4700::1111")
        header = struct.pack("!IHBB", 6 << 28, 20, 6, 64) + source + destination
        parsed = parse_frame(ethernet(header + tcp(), ethertype=0x86DD))
        assert parsed is not None
        assert parsed["source_ip"] == "fd00::1"
        assert parsed["protocol"] == "TCP"
        assert parsed["destination_port"] == 443


class TestMalformedFrames:
    """A bad frame is a discarded frame — never an exception."""

    def test_empty(self) -> None:
        assert parse_frame(b"") is None

    def test_truncated_ethernet(self) -> None:
        assert parse_frame(b"\x00" * 8) is None

    def test_truncated_ip_header(self) -> None:
        assert parse_frame(ethernet(b"\x45\x00\x00")) is None

    def test_ip_header_length_beyond_frame(self) -> None:
        """IHL claims 15 words (60 bytes) but the frame is shorter. Trusting
        the claim would read past the buffer."""
        header = struct.pack(
            "!BBHHHBBH4s4s",
            (4 << 4) | 15,
            0,
            100,
            0,
            0,
            64,
            6,
            0,
            socket.inet_aton("10.0.0.1"),
            socket.inet_aton("10.0.0.2"),
        )
        assert parse_frame(ethernet(header)) is None

    def test_version_mismatch(self) -> None:
        """Ethertype says IPv4, the header says version 6."""
        header = bytes([0x60]) + b"\x00" * 30
        assert parse_frame(ethernet(header)) is None

    def test_truncated_tcp_header(self) -> None:
        """The IP layer parses; the transport does not. The conversation is
        still reported, without ports — honest, rather than guessed."""
        parsed = parse_frame(ethernet(ipv4(b"\x00\x01")))
        assert parsed is not None
        assert parsed["source_ip"] == "192.168.1.10"
        assert "destination_port" not in parsed

    def test_truncated_vlan_tag(self) -> None:
        assert parse_frame(MAC_B + MAC_A + struct.pack("!H", 0x8100) + b"\x00") is None

    def test_unknown_ethertype_is_ignored(self) -> None:
        # ARP (0x0806) belongs to a different sensor; this one is about IP.
        assert parse_frame(ethernet(b"\x00" * 20, ethertype=0x0806)) is None

    def test_random_bytes_never_raise(self) -> None:
        import random

        generator = random.Random(20260818)
        for _ in range(500):
            length = generator.randint(0, 200)
            frame = bytes(generator.randint(0, 255) for _ in range(length))
            parse_frame(frame)  # must not raise

    def test_all_zero_frame(self) -> None:
        assert parse_frame(b"\x00" * 64) is None

    def test_unknown_ip_protocol_is_reported_by_number(self) -> None:
        """An unrecognised protocol is named, not dropped: the conversation
        happened even if we cannot decode the transport."""
        parsed = parse_frame(ethernet(ipv4(b"payload", protocol=47)))
        assert parsed is not None
        assert parsed["protocol"] == "IP:47"
