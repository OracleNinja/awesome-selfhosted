"""Detection engine tests.

Everything here runs against real PostgreSQL, because the guarantees being
tested *are* database guarantees: a unique index on the fingerprint, a composite
primary key on the evidence link, an upsert that distinguishes insert from
update. None of that can be verified against an in-memory substitute.
"""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from nexus.db.base import utcnow
from nexus.db.models.audit import AuditEvent
from nexus.db.models.device import Device
from nexus.db.models.event import SecurityEvent
from nexus.db.models.finding import DetectionState, Finding, FindingObservation
from nexus.db.models.setting import AppSetting
from nexus.detection.config import DEFAULT_CONFIG, SETTING_KEY, load_config
from nexus.detection.engine import DetectionEngine
from nexus.detection.observation import from_event
from nexus.detection.rules import ALL_DETECTORS
from nexus.services.audit import AuditService
from tests.detection_helpers import discovery, flow, ingest, make_sensor, minutes_ago, scan

pytestmark = pytest.mark.integration


async def analyze(session, settings, **kwargs):
    return await DetectionEngine(session, settings).analyze_pending(**kwargs)


class TestCanonicalObservation:
    """The observation model must answer the three provenance questions."""

    async def test_projection_preserves_provenance(self, session, settings) -> None:
        sensor = await make_sensor(session, name="arp-1")
        await ingest(session, sensor, [discovery()])
        event = (await session.execute(select(SecurityEvent))).scalar_one()

        observation = from_event(event)

        # Which sensor produced it?
        assert observation.provenance.sensor_name == "arp-1"
        assert observation.provenance.driver == "arp_discovery"
        assert observation.provenance.sensor_id == sensor.id
        # When was it produced?
        assert observation.provenance.occurred_at == event.occurred_at
        assert observation.provenance.received_at == event.received_at
        # Which raw event supports it?
        assert observation.provenance.event_id == event.id
        assert observation.provenance.event_uid == event.event_uid
        assert observation.provenance.has_raw is (event.raw is not None)

    async def test_observations_are_immutable(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        observation = from_event(event)

        with pytest.raises(FrozenInstanceError):
            observation.severity = "CRITICAL"  # type: ignore[misc]

    async def test_addresses_are_strings_not_driver_objects(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [flow()])
        event = (await session.execute(select(SecurityEvent))).scalar_one()
        observation = from_event(event)
        assert isinstance(observation.source_ip, str)
        assert isinstance(observation.destination_ip, str)


class TestNewDevice:
    async def test_first_observation_produces_a_finding(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])

        result = await analyze(session, settings)

        assert result.findings_created == 1
        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.detection_type == "NEW_DEVICE"
        assert finding.detector == "new_device"
        assert finding.status == "OPEN"
        assert finding.observation_count == 1
        assert "observed for the first time" in finding.explanation

    async def test_second_sighting_does_not_produce_a_second_finding(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery(at=minutes_ago(10), seed="first")])
        await analyze(session, settings)

        await ingest(session, sensor, [discovery(at=utcnow(), seed="second")])
        await analyze(session, settings)

        total = (
            await session.execute(
                select(func.count()).select_from(Finding).where(Finding.detector == "new_device")
            )
        ).scalar_one()
        assert total == 1

    async def test_confidence_is_lower_without_a_mac(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="syslog")
        await ingest(
            session,
            sensor,
            [
                discovery(mac=None, ip="192.168.1.77", seed="no-mac"),  # type: ignore[arg-type]
            ],
        )
        await analyze(session, settings)

        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.confidence == 60
        assert finding.evidence["identified_by"] == "ip_address"

    async def test_no_findings_without_a_device(self, session, settings) -> None:
        """An observation that resolves to no device is not evidence about one."""
        sensor = await make_sensor(session, driver="syslog")
        from nexus.sensors.base import RawObservation

        await ingest(
            session,
            sensor,
            [
                RawObservation(
                    kind="syslog.message",
                    category="LOG",
                    summary="something happened",
                    uid_seed="orphan",
                )
            ],
        )
        result = await analyze(session, settings)
        assert result.findings_created == 0


class TestIdentityChange:
    async def test_address_claimed_by_a_different_mac(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:01", ip="192.168.1.60", at=minutes_ago(30))],
        )
        await analyze(session, settings)

        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:02", ip="192.168.1.60", at=minutes_ago(1))],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        assert finding.evidence["change"] == "IP_REASSIGNED"
        assert finding.evidence["previous_value"] == "aa:bb:cc:00:00:01"
        assert finding.evidence["current_value"] == "aa:bb:cc:00:00:02"

    async def test_severity_high_with_low_confidence(self, session, settings) -> None:
        """The two axes are independent, and this rule is where it matters most."""
        sensor = await make_sensor(session)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:11", ip="192.168.1.61", at=minutes_ago(120))],
        )
        await analyze(session, settings)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:12", ip="192.168.1.61", at=minutes_ago(1))],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        assert finding.severity == "HIGH"
        assert finding.confidence <= 40
        assert "DHCP" in finding.explanation

    async def test_abrupt_change_raises_confidence(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:21", ip="192.168.1.62", at=minutes_ago(2))],
        )
        await analyze(session, settings)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:22", ip="192.168.1.62", at=minutes_ago(1))],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        assert finding.confidence == 60
        assert finding.evidence["abrupt"] is True

    async def test_baseline_outside_the_lookback_is_forgotten(self, session, settings) -> None:
        sensor = await make_sensor(session)
        long_ago = utcnow() - timedelta(days=30)
        await ingest(
            session, sensor, [discovery(mac="aa:bb:cc:00:00:31", ip="192.168.1.63", at=long_ago)]
        )
        await analyze(session, settings)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:32", ip="192.168.1.63", at=utcnow())],
        )
        await analyze(session, settings)

        changes = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        assert changes == 0

    async def test_baseline_links_are_recorded_as_evidence(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:41", ip="192.168.1.64", at=minutes_ago(30))],
        )
        await analyze(session, settings)
        await ingest(
            session,
            sensor,
            [discovery(mac="aa:bb:cc:00:00:42", ip="192.168.1.64", at=minutes_ago(1))],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        roles = (
            (
                await session.execute(
                    select(FindingObservation.role).where(
                        FindingObservation.finding_id == finding.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(roles) == {"TRIGGER", "BASELINE"}


class TestRepeatedAnomaly:
    async def test_threshold_must_be_reached(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(
            session,
            sensor,
            [scan(index=index, at=minutes_ago(5)) for index in range(4)],
        )
        result = await analyze(session, settings)

        anomalies = [
            item
            for item in (await session.execute(select(Finding))).scalars().all()
            if item.detection_type == "REPEATED_ANOMALY"
        ]
        assert anomalies == []
        assert result.events_examined == 4

    async def test_fires_once_the_window_threshold_is_met(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(
            session,
            sensor,
            [scan(index=index, at=minutes_ago(5)) for index in range(6)],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert finding.evidence["observed_occurrences"] == 6
        assert finding.evidence["min_occurrences"] == 5
        assert finding.evidence["window_seconds"] == 900
        assert finding.observation_count == 6

    async def test_window_is_configurable(self, session, settings) -> None:
        session.add(
            AppSetting(
                key=SETTING_KEY,
                value={
                    **DEFAULT_CONFIG.model_dump(mode="json"),
                    "rules": {
                        **DEFAULT_CONFIG.rules.model_dump(mode="json"),
                        "repeated_anomaly": {
                            "enabled": True,
                            "window_seconds": 60,
                            "min_occurrences": 3,
                            "categories": ["ANOMALY", "SCAN"],
                            "min_severity": "LOW",
                        },
                    },
                },
            )
        )
        await session.flush()

        sensor = await make_sensor(session, driver="packet_capture")
        # Three inside a 60-second window, one well outside it.
        await ingest(
            session,
            sensor,
            [
                scan(index=0, at=minutes_ago(30)),
                scan(index=1, at=minutes_ago(1)),
                scan(index=2, at=minutes_ago(1)),
                scan(index=3, at=minutes_ago(1)),
            ],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert finding.evidence["window_seconds"] == 60
        assert finding.evidence["observed_occurrences"] == 3

    async def test_counts_history_not_only_the_batch(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(3)
        await ingest(session, sensor, [scan(index=index, at=moment) for index in range(4)])
        await analyze(session, settings)
        await ingest(session, sensor, [scan(index=index, at=moment) for index in range(4, 6)])
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert finding.evidence["observed_occurrences"] == 6


class TestEndpointEnumeration:
    """The flow-shaped half of the repeated-anomaly rule.

    Every shipped sensor that sees flows emits `TRAFFIC`/`INFO` records, so a
    rate rule over classified categories can never see a port scan. This
    condition is what closes that gap, and these tests exist because the gap was
    real: the first version of this rule could not detect the laboratory's own
    `port_scan` scenario.
    """

    async def test_many_distinct_endpoints_produce_a_finding(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(1)
        await ingest(
            session,
            sensor,
            [
                flow(destination="192.168.1.20", port=port, at=moment, seed=f"enum-{port}")
                for port in range(1, 22)
            ],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert finding.evidence["condition"] == "ENDPOINT_ENUMERATION"
        assert finding.evidence["distinct_endpoints"] == 21
        assert finding.evidence["distinct_destinations"] == 1
        assert finding.evidence["min_distinct_endpoints"] == 15
        # Reports the pattern, never an intent.
        assert "legitimate" in finding.explanation
        assert "attack" not in finding.explanation.lower()

    async def test_ordinary_traffic_does_not_fire(self, session, settings) -> None:
        """A device talking to a couple of endpoints repeatedly is just busy."""
        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(1)
        await ingest(
            session,
            sensor,
            [
                flow(destination="198.51.100.20", port=443, at=moment, seed=f"busy-{index}")
                for index in range(60)
            ],
        )
        await analyze(session, settings)

        enumerations = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert enumerations == 0

    async def test_threshold_is_configurable(self, session, settings) -> None:
        session.add(
            AppSetting(
                key=SETTING_KEY,
                value={
                    **DEFAULT_CONFIG.model_dump(mode="json"),
                    "rules": {
                        **DEFAULT_CONFIG.rules.model_dump(mode="json"),
                        "repeated_anomaly": {
                            **DEFAULT_CONFIG.rules.repeated_anomaly.model_dump(mode="json"),
                            "min_distinct_endpoints": 4,
                        },
                    },
                },
            )
        )
        await session.flush()

        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(1)
        await ingest(
            session,
            sensor,
            [
                flow(destination="192.168.1.30", port=port, at=moment, seed=f"small-{port}")
                for port in (5001, 5002, 5003, 5004)
            ],
        )
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert finding.evidence["distinct_endpoints"] == 4

    async def test_rate_and_enumeration_are_separate_findings(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(1)
        await ingest(session, sensor, [scan(index=index, at=moment) for index in range(20)])
        await analyze(session, settings)

        findings = (
            (
                await session.execute(
                    select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
                )
            )
            .scalars()
            .all()
        )
        conditions = {item.evidence["condition"] for item in findings}
        assert conditions == {"RATE", "ENDPOINT_ENUMERATION"}
        assert len({item.fingerprint for item in findings}) == 2

    async def test_enumeration_is_idempotent(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        moment = minutes_ago(1)
        await ingest(
            session,
            sensor,
            [
                flow(destination="192.168.1.21", port=port, at=moment, seed=f"idem-{port}")
                for port in range(1, 22)
            ],
        )
        await analyze(session, settings)

        for _ in range(3):
            state = (await session.execute(select(DetectionState))).scalar_one()
            state.last_event_id = 0
            await session.flush()
            await analyze(session, settings)

        total = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        assert total == 1


class TestUnexpectedCommunication:
    async def test_external_endpoint_on_an_unexpected_port(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [discovery(ip="192.168.1.50")])
        await analyze(session, settings)
        await ingest(session, sensor, [flow(destination="198.51.100.9", port=4444)])
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "UNEXPECTED_COMMUNICATION")
            )
        ).scalar_one()
        assert finding.evidence["destination_ip"] == "198.51.100.9"
        assert finding.evidence["destination_port"] == 4444
        assert finding.severity == "MEDIUM"
        # The finding describes an observation, and refuses to characterise the
        # endpoint it has no information about.
        assert "reputation" in finding.explanation
        assert "malicious" not in finding.explanation.lower()

    async def test_allowed_ports_do_not_fire(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [flow(destination="198.51.100.9", port=443)])
        await analyze(session, settings)

        count = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "UNEXPECTED_COMMUNICATION")
            )
        ).scalar_one()
        assert count == 0

    async def test_internal_destinations_do_not_fire(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [flow(destination="192.168.1.99", port=4444)])
        await analyze(session, settings)

        count = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "UNEXPECTED_COMMUNICATION")
            )
        ).scalar_one()
        assert count == 0

    async def test_reports_not_configured_without_monitored_networks(self, settings) -> None:
        """No declared networks means the rule cannot run — and says so."""
        bare = settings.model_copy(update={"monitored_networks": []})
        detector = next(item for item in ALL_DETECTORS if item.id == "unexpected_communication")
        availability = detector.availability(DEFAULT_CONFIG, bare, is_simulation=False)
        assert availability.state == "NOT_CONFIGURED"
        assert availability.remedy is not None
        assert not availability.can_run

    async def test_simulation_scope_is_available_regardless(self, settings) -> None:
        bare = settings.model_copy(update={"monitored_networks": []})
        detector = next(item for item in ALL_DETECTORS if item.id == "unexpected_communication")
        assert detector.availability(DEFAULT_CONFIG, bare, is_simulation=True).can_run


class TestIdempotency:
    """The specification's hardest requirement: at-least-once must be safe."""

    async def test_reanalysing_the_same_events_creates_no_duplicates(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        first = await analyze(session, settings)
        assert first.findings_created == 1

        # Rewind the watermark: exactly what a redelivered job or a crash
        # between the handler and the commit produces.
        state = (await session.execute(select(DetectionState))).scalar_one()
        state.last_event_id = 0
        await session.flush()

        second = await analyze(session, settings)
        assert second.findings_created == 0
        assert second.findings_updated == 1

        total = (await session.execute(select(func.count()).select_from(Finding))).scalar_one()
        assert total == 1

    async def test_replay_does_not_inflate_the_observation_count(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [scan(index=index, at=minutes_ago(2)) for index in range(6)])
        await analyze(session, settings)

        finding = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        before = finding.observation_count

        for _ in range(3):
            state = (await session.execute(select(DetectionState))).scalar_one()
            state.last_event_id = 0
            await session.flush()
            await analyze(session, settings)

        await session.refresh(finding)
        assert finding.observation_count == before

        links = (
            await session.execute(
                select(func.count())
                .select_from(FindingObservation)
                .where(FindingObservation.finding_id == finding.id)
            )
        ).scalar_one()
        assert links == before

    async def test_replay_does_not_increase_risk(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await analyze(session, settings)

        device = (await session.execute(select(Device))).scalar_one()
        first_score = device.risk_score
        assert first_score is not None

        for _ in range(5):
            state = (await session.execute(select(DetectionState))).scalar_one()
            state.last_event_id = 0
            await session.flush()
            await analyze(session, settings)

        await session.refresh(device)
        assert device.risk_score == first_score

    async def test_duplicate_observations_do_not_duplicate_findings(
        self, session, settings
    ) -> None:
        """Re-delivered *observations*, not just re-delivered jobs."""
        sensor = await make_sensor(session)
        observation = discovery(seed="stable-seed")
        await ingest(session, sensor, [observation])
        await ingest(session, sensor, [discovery(seed="stable-seed")])

        events = (
            await session.execute(select(func.count()).select_from(SecurityEvent))
        ).scalar_one()
        assert events == 1  # ingestion deduplicated

        await analyze(session, settings)
        findings = (await session.execute(select(func.count()).select_from(Finding))).scalar_one()
        assert findings == 1

    async def test_watermark_only_advances_over_events_it_read(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery(seed=f"s{index}") for index in range(10)])

        result = await analyze(session, settings, batch_size=4)
        assert result.events_examined == 4
        assert result.backlog_remaining == 6

        state = (await session.execute(select(DetectionState))).scalar_one()
        highest_read = (
            (await session.execute(select(SecurityEvent.id).order_by(SecurityEvent.id).limit(4)))
            .scalars()
            .all()[-1]
        )
        assert state.last_event_id == highest_read


class TestSimulationIsolation:
    async def test_simulated_findings_are_flagged(self, session, settings) -> None:
        lab = await make_sensor(session, name="lab", driver="lab_synthetic", simulation=True)
        await ingest(session, lab, [discovery(mac="aa:bb:cc:99:00:01", ip="192.0.2.10")])
        await analyze(session, settings)

        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.is_simulation is True

    async def test_lab_and_real_devices_never_share_a_finding(self, session, settings) -> None:
        real = await make_sensor(session, name="real", driver="arp_discovery")
        lab = await make_sensor(session, name="lab", driver="lab_synthetic", simulation=True)

        await ingest(session, real, [discovery(mac="aa:bb:cc:11:00:01", ip="192.168.1.71")])
        await ingest(session, lab, [discovery(mac="aa:bb:cc:11:00:02", ip="192.0.2.20")])
        await analyze(session, settings)

        findings = (await session.execute(select(Finding))).scalars().all()
        assert len(findings) == 2
        assert {item.is_simulation for item in findings} == {True, False}
        # Two distinct fingerprints, so a lab finding can never collapse into a
        # real one even if every other component matched.
        assert len({item.fingerprint for item in findings}) == 2

    async def test_identity_baseline_does_not_cross_scopes(self, session, settings) -> None:
        """A laboratory observation must not become evidence about a real device."""
        real = await make_sensor(session, name="real", driver="arp_discovery")
        lab = await make_sensor(session, name="lab", driver="lab_synthetic", simulation=True)

        await ingest(
            session,
            real,
            [discovery(mac="aa:bb:cc:22:00:01", ip="192.168.1.72", at=minutes_ago(30))],
        )
        await analyze(session, settings)
        # Same address, different MAC, but simulated: it must not trigger an
        # identity-change finding against the real device.
        await ingest(
            session,
            lab,
            [discovery(mac="aa:bb:cc:22:00:02", ip="192.168.1.72", at=minutes_ago(1))],
        )
        await analyze(session, settings)

        changes = (
            await session.execute(
                select(func.count())
                .select_from(Finding)
                .where(Finding.detection_type == "IDENTITY_CHANGE")
            )
        ).scalar_one()
        assert changes == 0


class TestConcurrency:
    async def test_second_pass_skips_while_one_holds_the_lock(self, settings, database) -> None:
        """The advisory lock keeps two workers from analysing the same window.

        Two genuinely separate connections, because the lock is
        transaction-scoped and a second session on the same connection would
        acquire it trivially. The inner pass must return rather than block: a
        worker waiting on work another worker is already doing is a wasted slot.
        """
        async with database.session() as first:
            await DetectionEngine(first, settings).analyze_pending()
            # Still inside the first transaction, so the lock is still held.
            async with database.session() as second:
                result = await DetectionEngine(second, settings).analyze_pending()
                assert result.skipped_locked is True
                assert result.findings_created == 0
                assert result.events_examined == 0


class TestAudit:
    async def test_finding_creation_is_audited_on_the_existing_chain(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await analyze(session, settings)

        actions = (
            (await session.execute(select(AuditEvent.action).order_by(AuditEvent.id)))
            .scalars()
            .all()
        )
        assert "DETECTION_FINDING_CREATED" in actions

        verification = await AuditService(session, settings).verify_chain()
        assert verification.ok is True

    async def test_risk_band_change_is_audited(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await analyze(session, settings)

        actions = (await session.execute(select(AuditEvent.action))).scalars().all()
        assert "DEVICE_RISK_CHANGED" in actions

    async def test_burst_is_summarised_rather_than_flooding_the_log(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(
            session,
            sensor,
            [
                discovery(mac=f"aa:bb:cc:33:{index:02x}:01", ip=f"192.168.1.{100 + index}")
                for index in range(30)
            ],
        )
        await analyze(session, settings)

        rows = (await session.execute(select(AuditEvent.action, AuditEvent.details))).all()
        summary = [row for row in rows if row[0] == "DETECTION_FINDINGS_CREATED"]
        individual = [row for row in rows if row[0] == "DETECTION_FINDING_CREATED"]
        assert len(summary) == 1
        assert individual == []
        assert summary[0][1]["created"] == 30
        assert summary[0][1]["audited_individually"] is False

        verification = await AuditService(session, settings).verify_chain()
        assert verification.ok is True


class TestEvidenceRetention:
    async def test_pruning_an_event_removes_the_link_but_keeps_the_finding(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await analyze(session, settings)

        finding = (await session.execute(select(Finding))).scalar_one()
        assert finding.observation_count == 1

        await session.execute(
            SecurityEvent.__table__.delete()
        )  # what retention does, at its most extreme
        await session.flush()
        await session.refresh(finding)

        remaining = (
            await session.execute(
                select(func.count())
                .select_from(FindingObservation)
                .where(FindingObservation.finding_id == finding.id)
            )
        ).scalar_one()
        assert remaining == 0
        # The count of what was recorded survives, so the API can report that
        # evidence was pruned rather than that there never was any.
        assert finding.observation_count == 1
        assert finding.explanation


class TestConfiguration:
    async def test_defaults_when_nothing_is_stored(self, session) -> None:
        loaded = await load_config(session)
        assert loaded.source == "DEFAULT"
        assert loaded.config == DEFAULT_CONFIG

    async def test_invalid_stored_config_falls_back_and_says_so(self, session) -> None:
        session.add(AppSetting(key=SETTING_KEY, value={"weights": {"recent_activity": "wrong"}}))
        await session.flush()

        loaded = await load_config(session)
        assert loaded.source == "DEFAULT_AFTER_INVALID_CONFIGURATION"
        assert loaded.detail is not None
        assert "recent_activity" in loaded.detail
        assert loaded.config == DEFAULT_CONFIG

    async def test_unknown_keys_are_rejected(self, session) -> None:
        session.add(
            AppSetting(
                key=SETTING_KEY,
                value={**DEFAULT_CONFIG.model_dump(mode="json"), "nonsense": True},
            )
        )
        await session.flush()
        loaded = await load_config(session)
        assert loaded.source == "DEFAULT_AFTER_INVALID_CONFIGURATION"

    async def test_fingerprint_changes_with_the_weights(self) -> None:
        other = DEFAULT_CONFIG.model_copy(
            update={"weights": DEFAULT_CONFIG.weights.model_copy(update={"recent_activity": 20.0})}
        )
        assert other.fingerprint() != DEFAULT_CONFIG.fingerprint()

    async def test_disabled_rule_does_not_run(self, session, settings) -> None:
        session.add(
            AppSetting(
                key=SETTING_KEY,
                value={
                    **DEFAULT_CONFIG.model_dump(mode="json"),
                    "rules": {
                        **DEFAULT_CONFIG.rules.model_dump(mode="json"),
                        "new_device": {"enabled": False},
                    },
                },
            )
        )
        await session.flush()

        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        result = await analyze(session, settings)

        assert result.findings_created == 0
        assert "new_device[real]" in result.detectors_unavailable


class TestWatermark:
    async def test_empty_stream_is_not_an_error(self, session, settings) -> None:
        result = await analyze(session, settings)
        assert result.events_examined == 0
        assert result.findings_created == 0

        state = (await session.execute(select(DetectionState))).scalar_one()
        assert state.last_run_status == "SUCCEEDED"
        assert state.last_event_id == 0

    async def test_state_row_is_created_once(self, session, settings) -> None:
        await analyze(session, settings)
        await analyze(session, settings)
        count = (
            await session.execute(select(func.count()).select_from(DetectionState))
        ).scalar_one()
        assert count == 1


class TestFingerprints:
    def test_scope_is_part_of_the_identity(self) -> None:
        from nexus.detection.base import Candidate

        common = {
            "detector": "new_device",
            "rule_version": 1,
            "detection_type": "NEW_DEVICE",
            "fingerprint_parts": ("device", str(uuid.uuid4())),
            "severity": "MEDIUM",
            "confidence": 90,
            "device_id": None,
            "first_observed_at": utcnow(),
            "last_observed_at": utcnow(),
            "explanation": "x",
        }
        real = Candidate(**common, is_simulation=False)
        simulated = Candidate(**common, is_simulation=True)
        assert real.fingerprint() != simulated.fingerprint()

    def test_rule_version_is_part_of_the_identity(self) -> None:
        from nexus.detection.base import Candidate

        common = {
            "detector": "new_device",
            "detection_type": "NEW_DEVICE",
            "fingerprint_parts": ("device", "x"),
            "severity": "MEDIUM",
            "confidence": 90,
            "device_id": None,
            "first_observed_at": utcnow(),
            "last_observed_at": utcnow(),
            "explanation": "x",
        }
        assert (
            Candidate(**common, rule_version=1).fingerprint()
            != Candidate(**common, rule_version=2).fingerprint()
        )
