"""Sensor driver tests: availability honesty, parsing, and event derivation."""

from __future__ import annotations

import asyncio

import pytest

from nexus.core.config import Settings, load_settings
from nexus.core.errors import ValidationFailed
from nexus.sensors import SENSOR_DRIVERS, describe_drivers, resolve_driver
from nexus.sensors.arp_discovery import ArpDiscoverySensor
from nexus.sensors.base import Availability, RawObservation
from nexus.sensors.lab_synthetic import SCENARIOS, LabSyntheticSensor
from nexus.sensors.packet_capture import PacketCaptureSensor
from nexus.sensors.syslog import SyslogSensor


@pytest.fixture
def settings() -> Settings:
    return load_settings(monitored_networks=["192.168.1.0/24"])


class TestRegistry:
    def test_every_driver_declares_its_identity(self) -> None:
        for name, driver_class in SENSOR_DRIVERS.items():
            assert driver_class.driver == name
            assert driver_class.description
            assert isinstance(driver_class.produces_simulated_data, bool)

    def test_only_the_lab_driver_produces_simulated_data(self) -> None:
        """The honesty guarantee at the driver level: exactly one driver in the
        system fabricates data, and it says so."""
        simulated = {name for name, cls in SENSOR_DRIVERS.items() if cls.produces_simulated_data}
        assert simulated == {"lab_synthetic"}

    def test_unknown_driver_is_rejected_with_the_valid_list(self) -> None:
        with pytest.raises(ValidationFailed) as exc:
            resolve_driver("../../etc/passwd")
        assert "available_drivers" in (exc.value.details or {})

    def test_catalogue_marks_privileges_and_simulation(self) -> None:
        catalogue = {item["driver"]: item for item in describe_drivers()}
        assert catalogue["packet_capture"]["requires_privileges"] is True
        assert catalogue["arp_discovery"]["requires_privileges"] is False
        assert catalogue["lab_synthetic"]["produces_simulated_data"] is True


class TestAvailabilityHonesty:
    def test_unconfigured_capture_says_not_configured_not_unavailable(
        self, settings: Settings
    ) -> None:
        """The two states have different remedies, so they are different
        answers: 'you have not chosen an interface' is not 'this host cannot
        capture packets'."""
        report = PacketCaptureSensor.check_availability({}, settings)
        assert report.status is Availability.NOT_CONFIGURED
        assert report.remedy

    def test_nonexistent_interface_is_reported_with_the_real_list(self, settings: Settings) -> None:
        report = PacketCaptureSensor.check_availability({"interface": "eth99"}, settings)
        assert report.status is Availability.NOT_CONFIGURED
        assert "Available interfaces" in (report.remedy or "")

    def test_availability_checks_never_raise(self, settings: Settings) -> None:
        """This is what an operator consults when something is broken, so it
        has to work when things are broken."""
        for driver_class in SENSOR_DRIVERS.values():
            for config in ({}, {"interface": "does-not-exist"}, {"scenario": "nope"}):
                report = driver_class.check_availability(config, settings)
                assert report.detail

    def test_unavailable_reports_carry_a_remedy(self, settings: Settings) -> None:
        report = PacketCaptureSensor.check_availability({"interface": "nope0"}, settings)
        assert not report.is_available
        assert report.remedy, "an operator told 'unavailable' with no remedy has a dead end"


class TestArpDiscovery:
    def test_available_on_linux(self, settings: Settings) -> None:
        report = ArpDiscoverySensor.check_availability({}, settings)
        assert report.status is Availability.AVAILABLE
        # States its own limits rather than implying a complete inventory.
        assert "recently exchanged traffic" in report.detail

    def test_config_validation(self) -> None:
        assert ArpDiscoverySensor.validate_config({"poll_seconds": 60})["poll_seconds"] == 60
        with pytest.raises(ValidationFailed):
            ArpDiscoverySensor.validate_config({"poll_seconds": 1})
        with pytest.raises(ValidationFailed):
            ArpDiscoverySensor.validate_config({"interface": "eth0; rm -rf /"})

    def test_parses_the_kernel_table(self, settings: Settings, tmp_path, monkeypatch) -> None:
        table = tmp_path / "arp"
        table.write_text(
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0\n"
            "192.168.1.99     0x1         0x0         00:00:00:00:00:00     *        eth0\n"
            "192.168.1.50     0x1         0x2         11:22:33:44:55:66     *        wlan0\n"
        )
        monkeypatch.setattr("nexus.sensors.arp_discovery.ARP_TABLE_PATH", table)

        sensor = ArpDiscoverySensor("arp", {}, settings)
        entries = sensor._read_arp_table()

        # The incomplete entry (flags 0x0) is excluded: it is a pending lookup,
        # not a device, and reporting it would invent a host.
        assert [entry["ip"] for entry in entries] == ["192.168.1.1", "192.168.1.50"]

    def test_interface_filter(self, settings: Settings, tmp_path, monkeypatch) -> None:
        table = tmp_path / "arp"
        table.write_text(
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:ff     *        eth0\n"
            "192.168.1.50     0x1         0x2         11:22:33:44:55:66     *        wlan0\n"
        )
        monkeypatch.setattr("nexus.sensors.arp_discovery.ARP_TABLE_PATH", table)
        sensor = ArpDiscoverySensor("arp", {"interface": "wlan0"}, settings)
        assert [entry["ip"] for entry in sensor._read_arp_table()] == ["192.168.1.50"]

    def test_discovery_is_emitted_once_per_device(self, settings: Settings) -> None:
        """The id is derived from the MAC alone, so a restart re-reading the
        same table cannot re-announce a device the database already knows."""
        sensor = ArpDiscoverySensor("arp", {}, settings)
        entry = {"ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:ff", "device": "eth0"}

        first = sensor._observations_for(entry)
        second = sensor._observations_for(entry)

        assert [obs.kind for obs in first] == ["device.discovered", "device.seen"]
        # The second poll produces only the heartbeat.
        assert [obs.kind for obs in second] == ["device.seen"]
        # And the heartbeat collapses to the same id within its window.
        assert first[-1].event_uid("arp") == second[-1].event_uid("arp")

    def test_address_change_is_an_event(self, settings: Settings) -> None:
        sensor = ArpDiscoverySensor("arp", {}, settings)
        sensor._observations_for(
            {"ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:ff", "device": "eth0"}
        )
        moved = sensor._observations_for(
            {"ip": "192.168.1.11", "mac": "aa:bb:cc:dd:ee:ff", "device": "eth0"}
        )
        kinds = [obs.kind for obs in moved]
        assert "device.address_changed" in kinds
        change = next(obs for obs in moved if obs.kind == "device.address_changed")
        assert change.attributes["previous_ip"] == "192.168.1.10"

    def test_monitored_network_membership_is_recorded_not_filtered(
        self, settings: Settings
    ) -> None:
        """Passive observation is not gated by the monitored-networks boundary —
        that boundary governs actions. Membership is recorded as context."""
        sensor = ArpDiscoverySensor("arp", {}, settings)
        inside = sensor._observations_for(
            {"ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:01", "device": "eth0"}
        )
        outside = sensor._observations_for(
            {"ip": "10.9.9.9", "mac": "aa:bb:cc:dd:ee:02", "device": "eth0"}
        )
        assert inside[0].attributes["in_monitored_network"] is True
        assert outside[0].attributes["in_monitored_network"] is False


class TestSyslog:
    def test_rejects_privileged_ports_with_a_remedy(self, settings: Settings) -> None:
        report = SyslogSensor.check_availability({"port": 514}, settings)
        assert report.status is Availability.NOT_CONFIGURED
        assert "forward" in (report.remedy or "").lower()

    def test_parses_rfc5424(self, settings: Settings) -> None:
        sensor = SyslogSensor("syslog", {}, settings)
        message = (
            b"<34>1 2026-08-18T20:14:15.003Z router1 sshd 1234 ID47 - Failed password for admin"
        )
        observation = sensor._parse(message, "192.168.1.1")
        assert observation is not None
        assert observation.attributes["format"] == "rfc5424"
        assert observation.attributes["facility"] == "auth"
        assert observation.attributes["tag"] == "sshd"
        assert observation.category == "AUTH"
        assert observation.severity == "CRITICAL"  # syslog severity 2

    def test_parses_rfc3164(self, settings: Settings) -> None:
        sensor = SyslogSensor("syslog", {}, settings)
        observation = sensor._parse(
            b"<134>Oct 11 22:14:15 firewall kernel: DROP IN=eth0 SRC=10.0.0.5",
            "192.168.1.1",
        )
        assert observation is not None
        assert observation.attributes["format"] == "rfc3164"
        assert observation.attributes["tag"] == "kernel"
        # No year, no timezone: reconstructing it would be a guess on a
        # security event, so the receive time is used instead.
        assert observation.attributes["clock_skew_seconds"] is None

    def test_source_address_comes_from_the_packet_not_the_message(self, settings: Settings) -> None:
        """The hostname in a syslog message is whatever the sender typed. The
        packet's source address is the one thing it cannot forge."""
        sensor = SyslogSensor("syslog", {}, settings)
        observation = sensor._parse(
            b"<134>Oct 11 22:14:15 i-am-the-firewall kernel: hello", "203.0.113.9"
        )
        assert observation is not None
        assert observation.source_ip == "203.0.113.9"
        assert observation.hostname == "i-am-the-firewall"

    def test_control_characters_are_stripped(self, settings: Settings) -> None:
        """A raw escape sequence in a log line can rewrite the terminal of
        whoever runs `tail` on it."""
        sensor = SyslogSensor("syslog", {}, settings)
        observation = sensor._parse(
            b"<134>Oct 11 22:14:15 host app: hello\x1b[2Jworld\x00", "192.168.1.1"
        )
        assert observation is not None
        assert "\x1b" not in observation.summary
        assert "\x00" not in (observation.raw or "")

    def test_unparseable_message_is_still_recorded(self, settings: Settings) -> None:
        """Something sent something. Dropping it silently would hide a
        misconfigured device."""
        sensor = SyslogSensor("syslog", {}, settings)
        observation = sensor._parse(b"this is not syslog at all", "192.168.1.1")
        assert observation is not None
        assert observation.kind == "syslog.unparsed"
        assert observation.confidence < 100

    def test_empty_datagram_is_ignored(self, settings: Settings) -> None:
        assert SyslogSensor("syslog", {}, settings)._parse(b"   ", "192.168.1.1") is None

    def test_allowed_sources_filter(self, settings: Settings) -> None:
        sensor = SyslogSensor("syslog", {"allowed_sources": ["192.168.1.1"]}, settings)
        sensor._offer(b"<13>hello", "203.0.113.5")
        assert sensor._queue.empty()
        sensor._offer(b"<13>hello", "192.168.1.1")
        assert not sensor._queue.empty()

    def test_queue_overflow_is_counted_not_unbounded(self, settings: Settings) -> None:
        """Anyone on the network can send unlimited UDP. The receiver must
        survive that, and must say that it dropped things."""
        sensor = SyslogSensor("syslog", {}, settings)
        sensor._queue = asyncio.Queue(maxsize=2)
        for _ in range(10):
            sensor._offer(b"<13>flood", "192.168.1.1")
        assert sensor._dropped == 8
        report = sensor._drop_report()
        assert report.kind == "sensor.syslog_overflow"
        assert report.attributes["dropped"] == 8


class TestLabSynthetic:
    def test_declares_itself_as_simulation(self) -> None:
        assert LabSyntheticSensor.produces_simulated_data is True
        assert "SIMULATION ONLY" in LabSyntheticSensor.description

    def test_unknown_scenario_is_rejected_with_the_list(self) -> None:
        with pytest.raises(ValidationFailed) as exc:
            LabSyntheticSensor.validate_config({"scenario": "nuke_the_router"})
        assert "available_scenarios" in (exc.value.details or {})

    async def test_every_scenario_produces_labelled_events(self, settings: Settings) -> None:
        for scenario in SCENARIOS:
            sensor = LabSyntheticSensor(
                "lab", {"scenario": scenario, "events_per_second": 100, "max_events": 5}, settings
            )
            observations = [obs async for obs in sensor.observations()]
            assert observations, scenario
            for observation in observations:
                assert observation.attributes["simulation"] is True
                assert observation.attributes["scenario"] == scenario

    async def test_addresses_are_reserved_documentation_ranges(self, settings: Settings) -> None:
        """RFC 5737 ranges can never collide with a real device, so a simulated
        host cannot be confused for one — or acted upon."""
        import ipaddress

        reserved = [
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        ]
        for scenario in SCENARIOS:
            sensor = LabSyntheticSensor(
                "lab", {"scenario": scenario, "events_per_second": 100, "max_events": 10}, settings
            )
            async for observation in sensor.observations():
                for address in (observation.source_ip, observation.destination_ip):
                    if address is None:
                        continue
                    parsed = ipaddress.ip_address(address)
                    assert any(parsed in network for network in reserved), (
                        f"{scenario} produced non-documentation address {address}"
                    )

    async def test_scenarios_are_reproducible(self, settings: Settings) -> None:
        """A test that cannot be reproduced cannot be debugged."""

        async def run() -> list[str]:
            sensor = LabSyntheticSensor(
                "lab",
                {"scenario": "port_scan", "events_per_second": 100, "max_events": 10, "seed": 7},
                settings,
            )
            return [obs.summary async for obs in sensor.observations()]

        assert await run() == await run()

    async def test_port_scan_carries_the_signature_a_rule_would_match(
        self, settings: Settings
    ) -> None:
        sensor = LabSyntheticSensor(
            "lab", {"scenario": "port_scan", "events_per_second": 100, "max_events": 20}, settings
        )
        observations = [obs async for obs in sensor.observations()]
        ports = {obs.destination_port for obs in observations}
        assert len(ports) > 10
        assert all(obs.attributes.get("tcp_flags") == ["SYN"] for obs in observations)

    async def test_max_events_is_respected(self, settings: Settings) -> None:
        sensor = LabSyntheticSensor(
            "lab",
            {"scenario": "quiet_network", "events_per_second": 100, "max_events": 3},
            settings,
        )
        assert len([obs async for obs in sensor.observations()]) == 3


class TestObservationIdentity:
    def test_same_observation_yields_the_same_id(self) -> None:
        observation = RawObservation(
            kind="device.discovered", category="DISCOVERY", summary="x", uid_seed="fixed"
        )
        assert observation.event_uid("sensor-a") == observation.event_uid("sensor-a")

    def test_different_sensors_yield_different_ids(self) -> None:
        """Two sensors observing the same fact are two pieces of evidence.
        Collapsing them would hide one of them failing."""
        observation = RawObservation(
            kind="device.discovered", category="DISCOVERY", summary="x", uid_seed="fixed"
        )
        assert observation.event_uid("sensor-a") != observation.event_uid("sensor-b")

    def test_long_fields_are_truncated(self) -> None:
        observation = RawObservation(
            kind="syslog.message",
            category="LOG",
            summary="x" * 5000,
            raw="y" * 50_000,
            hostname="z" * 1000,
        ).truncated()
        assert len(observation.summary) <= 500
        assert len(observation.raw or "") <= 8192
        assert len(observation.hostname or "") <= 255
