import type { EmbroideryDesign } from '../../domain/design';
import { formatRuntime } from '../../domain/design';
import type { MachineProfile } from '../../domain/machine';
import { colorStops, shortId, type StitchSequence } from '../../domain/stitch-sequence';
import { threadHex } from '../../domain/thread';
import { unitsToInches, unitsToMm } from '../../domain/units';
import {
  buildReadinessReport,
  tierLabel,
  type ExportSnapshot,
  type ReadinessTier,
  type TierState,
} from '../../domain/readiness';

export interface StatusPanelProps {
  design: EmbroideryDesign;
  machine: MachineProfile;
  sequence: StitchSequence | null;
  artworkLoaded: boolean;
  lastExport: ExportSnapshot | null;
  onDownloadReport: () => void;
  onDownloadWorksheet: () => void;
}

const stateClass = (state: TierState): string =>
  state === 'passed' ? 'ok' : state === 'failed' ? 'bad' : state === 'in-progress' ? 'partial' : 'pending';

export function StatusPanel(props: StatusPanelProps): React.JSX.Element {
  const { design, machine, sequence } = props;
  const report = buildReadinessReport({
    design,
    machine,
    artworkLoaded: props.artworkLoaded,
    lastExport: props.lastExport,
  });
  const stops = sequence ? colorStops(sequence) : [];

  return (
    <>
      <section className="section">
        <h3>Status</h3>
        <div className="body">
          <dl className="stat-grid">
            <dt>Artwork</dt>
            <dd>{props.artworkLoaded ? 'loaded' : 'none'}</dd>
            <dt>Digitization</dt>
            <dd>
              {design.objects.length > 0
                ? `complete · ${design.objects.length} object(s)`
                : design.stitches.length > 0
                  ? 'imported stitches'
                  : 'not run'}
            </dd>
            <dt>Stitch count</dt>
            <dd>{(sequence?.stats.stitchCount ?? 0).toLocaleString()}</dd>
            <dt>Dimensions</dt>
            <dd>
              {unitsToMm(sequence?.stats.width ?? 0).toFixed(1)} × {unitsToMm(sequence?.stats.height ?? 0).toFixed(1)} mm
            </dd>
            <dt> </dt>
            <dd>
              {unitsToInches(sequence?.stats.width ?? 0).toFixed(2)} × {unitsToInches(sequence?.stats.height ?? 0).toFixed(2)} in
            </dd>
            <dt>Thread cones</dt>
            <dd>{sequence?.distinctThreads.length ?? 0}</dd>
            <dt>Colour stops</dt>
            <dd>{sequence?.blocks.length ?? 0}</dd>
            <dt>Colour changes</dt>
            <dd>{sequence?.stats.colorChangeCount ?? 0}</dd>
            <dt>Trims</dt>
            <dd>{sequence?.stats.trimCount ?? 0}</dd>
            <dt>Est. run time</dt>
            <dd>{formatRuntime(sequence?.stats.estimatedRuntimeSeconds ?? 0)}</dd>
            <dt>Validation</dt>
            <dd>
              {!design.validation
                ? 'not run'
                : design.validation.passed
                  ? `pass · ${design.validation.warningCount} warning(s)`
                  : `${design.validation.errorCount} error(s)`}
            </dd>
            <dt>PES export</dt>
            <dd>
              {!props.lastExport
                ? 'not exported'
                : props.lastExport.ok
                  ? `${(props.lastExport.byteLength / 1024).toFixed(1)} kB`
                  : 'blocked'}
            </dd>
            <dt>Sequence</dt>
            <dd title={sequence?.id ?? ''}>{sequence ? shortId(sequence.id) : '—'}</dd>
          </dl>

          {sequence?.hasRepeatedColors ? (
            <p className="note">
              This design returns to a colour it already used. Follow the colour stop list below in order — it is
              longer than the list of thread cones.
            </p>
          ) : null}
        </div>
      </section>

      {stops.length ? (
        <section className="section">
          <h3>Colour stops (sew order)</h3>
          <div className="body" style={{ padding: 0 }}>
            {stops.map((stop) => (
              <div className="thread-row" key={stop.step} style={{ padding: '5px 9px' }}>
                <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', width: 16 }}>
                  {stop.step}
                </span>
                <span className="swatch big" style={{ background: stop.hex }} />
                <div className="info">
                  <div className="name">{stop.thread.name}</div>
                  <div className="meta">
                    {stop.thread.manufacturer} {stop.thread.code} · {stop.stitchCount.toLocaleString()} stitches
                    {stop.repeatOfStep !== null ? ` · same cone as stop ${stop.repeatOfStep}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="section">
        <h3>Production readiness</h3>
        <div className="body">
          {report.tiers.map((tier) => (
            <TierRow key={tier.id} tier={tier} />
          ))}

          <div className="issue WARNING" style={{ marginTop: 10 }}>
            <span className="sev">HARDWARE NOT VERIFIED</span>
            No design from this application has been confirmed to sew correctly on a physical {machine.name}. Software
            checks and file-format checks do not establish that. Run the procedure in
            <code> docs/PHYSICAL-VALIDATION.md </code> on scrap fabric before any customer job.
          </div>

          <div className="row" style={{ marginTop: 9 }}>
            <button onClick={props.onDownloadReport} disabled={!sequence}>
              Save readiness report
            </button>
            <button onClick={props.onDownloadWorksheet} disabled={!sequence}>
              Save stitch-out worksheet
            </button>
          </div>
          <p className="note">
            Keep the worksheet with the hooped sample. It records what the software predicted so the physical result
            can be compared against it.
          </p>
        </div>
      </section>
    </>
  );
}

function TierRow(props: { tier: ReadinessTier }): React.JSX.Element {
  const { tier } = props;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
        <span className={`tier ${stateClass(tier.state)}`}>{tierLabel(tier.state)}</span>
        <strong style={{ fontSize: 12 }}>{tier.title}</strong>
      </div>
      <div style={{ fontSize: 12, lineHeight: 1.45, color: 'var(--text)' }}>{tier.summary}</div>
      {tier.checks.length ? (
        <ul style={{ margin: '5px 0 0', padding: 0, listStyle: 'none' }}>
          {tier.checks.map((check) => (
            <li key={check.label} style={{ fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>
              <span className={`tier-dot ${stateClass(check.state)}`} /> {check.label}: {check.detail}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function threadSwatch(hex: string): React.JSX.Element {
  return <span className="swatch" style={{ background: hex }} />;
}

export { threadHex };
