"""Risk scoring tests.

The scoring function is pure, so most of these need no database at all — which
is itself the point being tested: a score that can be computed from stored
inputs alone is a score that can be re-derived and argued with. The persistence
tests then run against real PostgreSQL, because "writes nothing when nothing
changed" is a claim about rows.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from nexus.db.base import utcnow
from nexus.db.models.device import Device
from nexus.db.models.finding import DeviceRiskAssessment, Finding
from nexus.detection.config import DEFAULT_CONFIG
from nexus.detection.engine import DetectionEngine
from nexus.detection.risk import (
    RiskService,
    compute_device_risk,
    level_for,
    score_finding,
)
from tests.detection_helpers import discovery, flow, ingest, make_sensor, minutes_ago, scan

pytestmark = pytest.mark.integration


def make_device(**overrides) -> Device:
    defaults = {
        "id": uuid.uuid4(),
        "mac_address": "aa:bb:cc:dd:ee:ff",
        "ip_address": "192.168.1.10",
        "first_seen_at": utcnow() - timedelta(days=30),
        "last_seen_at": utcnow(),
        "is_trusted": False,
        "is_simulation": False,
        "risk_score": None,
        "risk_level": "UNKNOWN",
        "risk_updated_at": None,
        "state": "ACTIVE",
    }
    return Device(**{**defaults, **overrides})


def make_finding(**overrides) -> Finding:
    now = utcnow()
    defaults = {
        "id": uuid.uuid4(),
        "fingerprint": uuid.uuid4().hex,
        "detector": "new_device",
        "rule_version": 1,
        "detection_type": "NEW_DEVICE",
        "severity": "MEDIUM",
        "confidence": 80,
        "status": "OPEN",
        "first_observed_at": now,
        "last_observed_at": now,
        "observation_count": 1,
        "evidence": {},
        "explanation": "x",
        "risk_factors": [],
        "is_simulation": False,
    }
    return Finding(**{**defaults, **overrides})


class TestPureScoring:
    def test_no_findings_scores_zero_not_unknown(self) -> None:
        """Assessed-and-clean is a different claim from never-assessed."""
        device = make_device()
        assessment = compute_device_risk(device, [], config=DEFAULT_CONFIG, now=utcnow())
        assert assessment.score == 0
        assert assessment.level == "LOW"
        # Every factor is still reported, including the ones that contributed
        # nothing: "considered and absent" is information.
        assert len(assessment.factors) == 7
        assert all(factor.magnitude == 0.0 for factor in assessment.factors)

    def test_deterministic_for_identical_inputs(self) -> None:
        device = make_device()
        findings = [make_finding(severity="HIGH", confidence=90)]
        now = utcnow()

        first = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=now)
        second = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=now)

        assert first.score == second.score
        assert first.factors_as_json() == second.factors_as_json()
        assert first.weights_fingerprint == second.weights_fingerprint

    def test_ordering_of_findings_does_not_change_the_score(self) -> None:
        device = make_device()
        now = utcnow()
        findings = [
            make_finding(severity="HIGH", confidence=90),
            make_finding(detection_type="IDENTITY_CHANGE", severity="HIGH", confidence=35),
            make_finding(detection_type="REPEATED_ANOMALY", severity="LOW", confidence=60),
        ]
        forward = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=now)
        backward = compute_device_risk(
            device, list(reversed(findings)), config=DEFAULT_CONFIG, now=now
        )
        assert forward.score == backward.score

    def test_score_is_the_sum_of_the_published_contributions(self) -> None:
        """The arithmetic in the response must actually be the arithmetic used."""
        device = make_device()
        findings = [make_finding(severity="CRITICAL", confidence=100)]
        assessment = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=utcnow())

        total = sum(factor.contribution for factor in assessment.factors)
        assert assessment.score == max(0, min(100, round(total)))

    def test_severity_and_confidence_are_independent(self) -> None:
        device = make_device()
        now = utcnow()
        high_certain = compute_device_risk(
            device, [make_finding(severity="HIGH", confidence=100)], config=DEFAULT_CONFIG, now=now
        )
        high_uncertain = compute_device_risk(
            device, [make_finding(severity="HIGH", confidence=20)], config=DEFAULT_CONFIG, now=now
        )
        assert high_certain.score > high_uncertain.score

    def test_finding_score_decomposes_into_its_axes(self) -> None:
        assert score_finding("HIGH", 35) == 28  # 0.8 x 35
        assert score_finding("CRITICAL", 100) == 100
        assert score_finding("INFO", 100) == 0
        # A HIGH finding nobody is sure of ranks below a MEDIUM one that is
        # certain, which is the behaviour an analyst wants from a queue.
        assert score_finding("HIGH", 20) < score_finding("MEDIUM", 100)

    def test_trust_reduces_but_does_not_suppress_detection(self) -> None:
        findings = [make_finding(severity="HIGH", confidence=90)]
        now = utcnow()
        untrusted = compute_device_risk(make_device(), findings, config=DEFAULT_CONFIG, now=now)
        trusted = compute_device_risk(
            make_device(is_trusted=True), findings, config=DEFAULT_CONFIG, now=now
        )
        assert trusted.score < untrusted.score
        # The findings themselves are untouched: trust changes the score, not
        # the record.
        assert trusted.contributing_finding_ids == untrusted.contributing_finding_ids

    def test_novel_device_scores_higher(self) -> None:
        now = utcnow()
        old = compute_device_risk(make_device(), [], config=DEFAULT_CONFIG, now=now)
        new = compute_device_risk(
            make_device(first_seen_at=now - timedelta(minutes=5)),
            [],
            config=DEFAULT_CONFIG,
            now=now,
        )
        assert new.score > old.score

    def test_recency_decays_in_whole_hours(self) -> None:
        """Quantised time is what makes repeated scoring a no-op."""
        device = make_device()
        base = utcnow()
        findings = [make_finding(severity="HIGH", confidence=90)]

        immediate = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=base)
        a_moment_later = compute_device_risk(
            device, findings, config=DEFAULT_CONFIG, now=base + timedelta(seconds=59)
        )
        much_later = compute_device_risk(
            device, findings, config=DEFAULT_CONFIG, now=base + timedelta(hours=10)
        )

        assert immediate.factors_as_json() == a_moment_later.factors_as_json()
        assert much_later.score < immediate.score

    def test_score_saturates_rather_than_overflowing(self) -> None:
        device = make_device(first_seen_at=utcnow())
        findings = [
            make_finding(
                severity="CRITICAL",
                confidence=100,
                detection_type=detection_type,
                evidence={"destination_ip": f"198.51.100.{index}", "destination_port": 4444},
            )
            for index, detection_type in enumerate(
                [
                    "IDENTITY_CHANGE",
                    "UNEXPECTED_COMMUNICATION",
                    "REPEATED_ANOMALY",
                    "NEW_DEVICE",
                ]
                * 5
            )
        ]
        assessment = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=utcnow())
        assert assessment.score == 100

    def test_clock_skew_cannot_produce_a_magnitude_above_one(self) -> None:
        """A sensor whose clock runs ahead must not inflate the score."""
        device = make_device()
        future = utcnow() + timedelta(hours=5)
        findings = [make_finding(last_observed_at=future, first_observed_at=future)]
        assessment = compute_device_risk(device, findings, config=DEFAULT_CONFIG, now=utcnow())
        recency = next(f for f in assessment.factors if f.code == "RECENT_ACTIVITY")
        assert recency.magnitude == 1.0

    def test_levels_follow_the_configured_thresholds(self) -> None:
        assert level_for(0, DEFAULT_CONFIG) == "LOW"
        assert level_for(34, DEFAULT_CONFIG) == "LOW"
        assert level_for(35, DEFAULT_CONFIG) == "MEDIUM"
        assert level_for(60, DEFAULT_CONFIG) == "HIGH"
        assert level_for(80, DEFAULT_CONFIG) == "CRITICAL"

    def test_weights_are_configurable_and_move_the_score(self) -> None:
        findings = [make_finding(severity="HIGH", confidence=90)]
        now = utcnow()
        louder = DEFAULT_CONFIG.model_copy(
            update={
                "weights": DEFAULT_CONFIG.weights.model_copy(
                    update={"severity_weighted_findings": 90.0}
                )
            }
        )
        base = compute_device_risk(make_device(), findings, config=DEFAULT_CONFIG, now=now)
        tuned = compute_device_risk(make_device(), findings, config=louder, now=now)
        assert tuned.score > base.score
        assert tuned.weights_fingerprint != base.weights_fingerprint

    def test_window_describes_the_evidence_it_covers(self) -> None:
        earliest = utcnow() - timedelta(hours=6)
        latest = utcnow() - timedelta(hours=1)
        findings = [
            make_finding(first_observed_at=earliest, last_observed_at=earliest),
            make_finding(first_observed_at=latest, last_observed_at=latest),
        ]
        assessment = compute_device_risk(
            make_device(), findings, config=DEFAULT_CONFIG, now=utcnow()
        )
        assert assessment.window_started_at == earliest
        assert assessment.window_ended_at == latest


class TestPersistence:
    async def test_first_assessment_records_history_and_updates_the_device(
        self, session, settings
    ) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        assert device.risk_score is not None
        assert device.risk_level != "UNKNOWN"
        assert device.risk_updated_at is not None

        assessment = (await session.execute(select(DeviceRiskAssessment))).scalar_one()
        assert assessment.score == device.risk_score
        assert assessment.previous_score is None
        assert assessment.contributing_finding_ids
        assert assessment.weights_fingerprint == DEFAULT_CONFIG.fingerprint()
        assert len(assessment.factors) == 7

    async def test_unchanged_inputs_write_no_new_assessment(self, session, settings) -> None:
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        service = RiskService(session)
        for _ in range(5):
            _, changed = await service.rescore(device, config=DEFAULT_CONFIG)
            assert changed is False

        rows = (
            await session.execute(select(func.count()).select_from(DeviceRiskAssessment))
        ).scalar_one()
        assert rows == 1

    async def test_history_records_what_the_score_replaced(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [discovery()])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        first_score = device.risk_score

        await ingest(session, sensor, [scan(index=index, at=minutes_ago(2)) for index in range(6)])
        await ingest(session, sensor, [flow(destination="198.51.100.5", port=9001)])
        await DetectionEngine(session, settings).analyze_pending()

        await session.refresh(device)
        assert device.risk_score is not None
        assert device.risk_score > (first_score or 0)

        latest = (
            await session.execute(
                select(DeviceRiskAssessment).order_by(DeviceRiskAssessment.id.desc()).limit(1)
            )
        ).scalar_one()
        assert latest.previous_score == first_score
        assert latest.score == device.risk_score

    async def test_resolving_a_finding_lowers_the_score(self, session, settings) -> None:
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [discovery()])
        await ingest(session, sensor, [scan(index=index, at=minutes_ago(2)) for index in range(6)])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        before = device.risk_score
        assert before is not None

        anomaly = (
            await session.execute(
                select(Finding).where(Finding.detection_type == "REPEATED_ANOMALY")
            )
        ).scalar_one()
        anomaly.status = "RESOLVED"
        await session.flush()

        await RiskService(session).rescore(device, config=DEFAULT_CONFIG)
        assert device.risk_score is not None
        assert device.risk_score < before

    async def test_untouched_devices_stay_unassessed(self, session, settings) -> None:
        """A device nobody has scored reports UNKNOWN, not zero."""
        sensor = await make_sensor(session)
        await ingest(session, sensor, [discovery()])
        # Deliberately no analysis pass.
        device = (await session.execute(select(Device))).scalar_one()
        assert device.risk_score is None
        assert device.risk_level == "UNKNOWN"

    async def test_simulated_devices_get_simulated_assessments(self, session, settings) -> None:
        lab = await make_sensor(session, name="lab", driver="lab_synthetic", simulation=True)
        await ingest(session, lab, [discovery(mac="aa:bb:cc:44:00:01", ip="192.0.2.30")])
        await DetectionEngine(session, settings).analyze_pending()

        assessment = (await session.execute(select(DeviceRiskAssessment))).scalar_one()
        assert assessment.is_simulation is True

    async def test_moving_evidence_at_the_same_score_writes_nothing(
        self, session, settings
    ) -> None:
        """A burst in progress must not append a row per analysis pass.

        Found in live running: a device under a sustained laboratory scan
        accumulated seven consecutive assessments at score 47, because each pass
        moved a timestamp inside a factor's evidence blob while changing no part
        of the arithmetic.
        """
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [discovery()])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        before = (
            await session.execute(select(func.count()).select_from(DeviceRiskAssessment))
        ).scalar_one()

        finding = (await session.execute(select(Finding))).scalar_one()
        # Same severity, same confidence, same count — only the explanatory
        # text and the evidence timestamps move.
        finding.evidence = {**(finding.evidence or {}), "observations_in_batch": 99}
        finding.explanation = finding.explanation + " Re-observed."
        await session.flush()

        _, changed = await RiskService(session).rescore(device, config=DEFAULT_CONFIG)
        assert changed is False

        after = (
            await session.execute(select(func.count()).select_from(DeviceRiskAssessment))
        ).scalar_one()
        assert after == before

    async def test_a_changed_magnitude_still_records_an_assessment(self, session, settings) -> None:
        """The opposite guard: real movement in the arithmetic is not swallowed."""
        sensor = await make_sensor(session, driver="packet_capture")
        await ingest(session, sensor, [discovery()])
        await DetectionEngine(session, settings).analyze_pending()

        device = (await session.execute(select(Device))).scalar_one()
        finding = (await session.execute(select(Finding))).scalar_one()
        finding.confidence = 20  # changes the severity-weighted magnitude
        await session.flush()

        _, changed = await RiskService(session).rescore(device, config=DEFAULT_CONFIG)
        assert changed is True


class TestConcurrentRescoring:
    """Two writers, one device.

    This class exists because of a defect found by running the real system, not
    by the unit tests: `detection.analyze_pending` and `risk.rescore_stale` both
    score devices, and on a freshly started deployment they ran at the same
    moment. Under READ COMMITTED the job that began first saw a snapshot without
    the other's freshly committed findings, computed the no-findings score, and —
    committing last — wrote that stale score over the correct one. The device
    showed 10 when it should have shown 40.
    """

    async def test_second_writer_waits_and_sees_the_first_writers_findings(
        self, database, settings
    ) -> None:
        async with database.session() as setup:
            sensor = await make_sensor(setup, driver="packet_capture")
            await ingest(setup, sensor, [discovery()])
            device_id = (await setup.execute(select(Device.id))).scalar_one()

        started = asyncio.Event()
        release = asyncio.Event()

        async def analysing() -> int:
            """Create a finding and hold the transaction open."""
            async with database.session() as session:
                await DetectionEngine(session, settings).analyze_pending()
                started.set()
                await release.wait()
                device = await session.get(Device, device_id)
                return device.risk_score

        async def rescoring() -> int | None:
            """Rescore the same device while the first transaction is open."""
            await started.wait()
            async with database.session() as session:
                device = await session.get(Device, device_id)
                # Let the first transaction commit while this one blocks on the
                # row lock inside rescore().
                release.set()
                assessment, _ = await RiskService(session).rescore(device, config=DEFAULT_CONFIG)
                return assessment.score

        first, second = await asyncio.wait_for(asyncio.gather(analysing(), rescoring()), timeout=30)

        # The second writer must not have scored the device as though it had no
        # findings: it waited for the lock, then read the committed finding.
        assert second == first
        assert second > 0

        async with database.session() as session:
            device = await session.get(Device, device_id)
            assert device.risk_score == first
            scores = (
                (
                    await session.execute(
                        select(DeviceRiskAssessment.score).order_by(DeviceRiskAssessment.id)
                    )
                )
                .scalars()
                .all()
            )
        # No assessment recorded a lower "no findings" score behind the first.
        assert all(score >= first for score in scores)

    async def test_rescore_of_a_deleted_device_writes_nothing(self, database, settings) -> None:
        async with database.session() as setup:
            sensor = await make_sensor(setup)
            await ingest(setup, sensor, [discovery()])
            device = (await setup.execute(select(Device))).scalar_one()
            await DetectionEngine(setup, settings).analyze_pending()

        async with database.session() as session:
            stale = await session.get(Device, device.id)
            await session.execute(Device.__table__.delete().where(Device.id == device.id))
            await session.flush()
            _, changed = await RiskService(session).rescore(stale, config=DEFAULT_CONFIG)
            assert changed is False
