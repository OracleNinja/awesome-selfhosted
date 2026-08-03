import { useMemo, useState } from 'react';
import { computeStats, estimateThreadMetres, formatRuntime, type EmbroideryDesign } from '../../domain/design';
import type { EmbroideryObject, StitchType, UnderlayType } from '../../domain/embroidery-object';
import { PEC_THREADS, searchThreads, threadHex, unbundledCharts, type Thread } from '../../domain/thread';
import type { MachineProfile, Hoop } from '../../domain/machine';
import { unitsToInches, unitsToMm } from '../../domain/units';
import type { ExportResult } from '../../app/export-pes';

export interface RightPanelProps {
  design: EmbroideryDesign;
  machine: MachineProfile;
  hoop: Hoop | null;
  selected: EmbroideryObject | null;
  exportResult: ExportResult | null;
  warningsAcknowledged: boolean;
  onAcknowledgeWarnings: (v: boolean) => void;
  onExport: () => void;
  onObjectChange: <K extends keyof EmbroideryObject>(key: K, value: EmbroideryObject[K]) => void;
  onStitchType: (type: StitchType) => void;
  onDeleteObject: () => void;
  onDuplicateObject: () => void;
  onMoveObject: (dx: number, dy: number) => void;
  onScaleObject: (factor: number) => void;
  onRotateObject: (degrees: number) => void;
  onShiftStartPoint: () => void;
  onReversePath: () => void;
  onReplaceThread: (index: number, thread: Thread) => void;
  onCenterDesign: () => void;
  onScaleDesign: (factor: number) => void;
}

type Tab = 'properties' | 'threads' | 'validate';

export function RightPanel(props: RightPanelProps): React.JSX.Element {
  const [tab, setTab] = useState<Tab>('properties');
  const stats = useMemo(() => computeStats(props.design), [props.design]);
  const report = props.design.validation;

  return (
    <aside className="panel right">
      <section className="section">
        <h3>Design</h3>
        <div className="body">
          <dl className="stat-grid">
            <dt>Width</dt>
            <dd>
              {unitsToMm(stats.width).toFixed(1)} mm / {unitsToInches(stats.width).toFixed(2)} in
            </dd>
            <dt>Height</dt>
            <dd>
              {unitsToMm(stats.height).toFixed(1)} mm / {unitsToInches(stats.height).toFixed(2)} in
            </dd>
            <dt>Stitches</dt>
            <dd>{stats.stitchCount.toLocaleString()}</dd>
            <dt>Colours</dt>
            <dd>{stats.colorCount}</dd>
            <dt>Colour changes</dt>
            <dd>{stats.colorChangeCount}</dd>
            <dt>Trims</dt>
            <dd>{stats.trimCount}</dd>
            <dt>Jumps</dt>
            <dd>{stats.jumpCount}</dd>
            <dt>Est. run time</dt>
            <dd>{formatRuntime(stats.estimatedRuntimeSeconds)}</dd>
            <dt>Est. thread</dt>
            <dd>{estimateThreadMetres(stats.totalThreadLength).toFixed(1)} m</dd>
            <dt>Stitch length</dt>
            <dd>
              {unitsToMm(stats.minStitchLength).toFixed(2)}–{unitsToMm(stats.maxStitchLength).toFixed(2)} mm
            </dd>
            <dt>Hoop</dt>
            <dd>{props.hoop?.name ?? '—'}</dd>
          </dl>
          <p className="note">
            Every figure above is measured from the generated stitch data. Run time assumes 650 spm plus 45 s per
            colour change, and thread length counts top and bobbin thread; both are estimates.
          </p>
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={props.onCenterDesign}>Centre in hoop</button>
            <button onClick={() => props.onScaleDesign(1.1)}>Scale +10%</button>
            <button onClick={() => props.onScaleDesign(1 / 1.1)}>Scale −10%</button>
          </div>
        </div>
      </section>

      <div className="tabs">
        <button className={tab === 'properties' ? 'active' : ''} onClick={() => setTab('properties')}>
          Object
        </button>
        <button className={tab === 'threads' ? 'active' : ''} onClick={() => setTab('threads')}>
          Threads
        </button>
        <button className={tab === 'validate' ? 'active' : ''} onClick={() => setTab('validate')}>
          Validate{report && report.errorCount + report.warningCount > 0 ? ` (${report.errorCount + report.warningCount})` : ''}
        </button>
      </div>

      {tab === 'properties' ? <ObjectProperties {...props} /> : null}
      {tab === 'threads' ? <ThreadPanel {...props} /> : null}
      {tab === 'validate' ? <ValidatePanel {...props} /> : null}
    </aside>
  );
}

const STITCH_TYPES: StitchType[] = ['fill', 'satin', 'running', 'bean', 'outline', 'manual'];
const UNDERLAY_TYPES: UnderlayType[] = ['none', 'edge-run', 'center-run', 'zigzag', 'full'];

function ObjectProperties(props: RightPanelProps): React.JSX.Element {
  const obj = props.selected;
  if (!obj) {
    return (
      <section className="section">
        <h3>Object properties</h3>
        <div className="body">
          <p className="note">Select an object in the list on the left to edit it.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="section">
      <h3>{obj.name}</h3>
      <div className="body">
        {obj.notes.length ? (
          <div className="issue INFO">
            <span className="sev">DIGITIZER NOTES</span>
            {obj.notes.map((n, i) => (
              <div key={i}>{n}</div>
            ))}
          </div>
        ) : null}

        <div className="field">
          <label htmlFor="type">Stitch type</label>
          <select id="type" value={obj.type} onChange={(e) => props.onStitchType(e.target.value as StitchType)}>
            {STITCH_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="thread">Thread</label>
          <select
            id="thread"
            value={obj.threadIndex}
            onChange={(e) => props.onObjectChange('threadIndex', Number(e.target.value))}
          >
            {props.design.threadPalette.map((t, i) => (
              <option key={i} value={i}>
                {i + 1}. {t.name} ({t.manufacturer} {t.code})
              </option>
            ))}
          </select>
        </div>

        <div className="row">
          <div className="field">
            <label htmlFor="density">Density (mm)</label>
            <input
              id="density"
              type="number"
              min={0.15}
              max={2}
              step={0.05}
              value={unitsToMm(obj.density).toFixed(2)}
              onChange={(e) => props.onObjectChange('density', Number(e.target.value) * 10)}
            />
          </div>
          <div className="field">
            <label htmlFor="stitchLength">Stitch length (mm)</label>
            <input
              id="stitchLength"
              type="number"
              min={0.6}
              max={12.7}
              step={0.1}
              value={unitsToMm(obj.stitchLength).toFixed(1)}
              onChange={(e) => props.onObjectChange('stitchLength', Number(e.target.value) * 10)}
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="angle">Fill angle ({obj.angle}°)</label>
          <input
            id="angle"
            type="range"
            min={0}
            max={179}
            value={obj.angle}
            disabled={obj.type !== 'fill'}
            onChange={(e) => props.onObjectChange('angle', Number(e.target.value))}
          />
        </div>

        <div className="field">
          <label htmlFor="underlay">Underlay</label>
          <select
            id="underlay"
            value={obj.underlay.type}
            onChange={(e) =>
              props.onObjectChange('underlay', { ...obj.underlay, type: e.target.value as UnderlayType })
            }
          >
            {UNDERLAY_TYPES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        </div>

        {obj.type === 'bean' ? (
          <div className="field">
            <label htmlFor="bean">Bean passes</label>
            <select
              id="bean"
              value={obj.beanRepeats ?? 3}
              onChange={(e) => props.onObjectChange('beanRepeats', Number(e.target.value))}
            >
              <option value={3}>3</option>
              <option value={5}>5</option>
              <option value={7}>7</option>
            </select>
          </div>
        ) : null}

        <div className="field">
          <label style={{ textTransform: 'none', fontSize: 12 }}>
            <input
              type="checkbox"
              style={{ width: 'auto', marginRight: 6 }}
              checked={obj.forceTrimBefore}
              onChange={(e) => props.onObjectChange('forceTrimBefore', e.target.checked)}
            />
            Always trim before this object
          </label>
        </div>

        <h4 style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--muted)', margin: '12px 0 6px' }}>
          Transform
        </h4>
        <div className="row" style={{ marginBottom: 6 }}>
          <button onClick={() => props.onMoveObject(-10, 0)}>← 1mm</button>
          <button onClick={() => props.onMoveObject(10, 0)}>1mm →</button>
          <button onClick={() => props.onMoveObject(0, -10)}>↑ 1mm</button>
          <button onClick={() => props.onMoveObject(0, 10)}>↓ 1mm</button>
        </div>
        <div className="row" style={{ marginBottom: 6 }}>
          <button onClick={() => props.onScaleObject(1.1)}>Scale +10%</button>
          <button onClick={() => props.onScaleObject(1 / 1.1)}>Scale −10%</button>
        </div>
        <div className="row" style={{ marginBottom: 6 }}>
          <button onClick={() => props.onRotateObject(-15)}>↺ 15°</button>
          <button onClick={() => props.onRotateObject(15)}>↻ 15°</button>
        </div>
        <div className="row" style={{ marginBottom: 6 }}>
          <button onClick={props.onShiftStartPoint} title="Move where the needle enters this object">
            Shift start point
          </button>
          <button onClick={props.onReversePath} title="Swap the start and end of an open path">
            Reverse direction
          </button>
        </div>
        <div className="row">
          <button onClick={props.onDuplicateObject}>Duplicate</button>
          <button className="danger" onClick={props.onDeleteObject}>
            Delete
          </button>
        </div>
      </div>
    </section>
  );
}

function ThreadPanel(props: RightPanelProps): React.JSX.Element {
  const [query, setQuery] = useState('');
  const [editing, setEditing] = useState<number | null>(null);
  const stats = computeStats(props.design);
  const matches = useMemo(() => searchThreads(query).slice(0, 60), [query]);

  return (
    <section className="section">
      <h3>Thread sequence</h3>
      <div className="body">
        {props.design.threadPalette.length === 0 ? (
          <p className="note">No threads yet. Digitize the artwork to build a palette.</p>
        ) : null}

        {props.design.threadPalette.map((thread, i) => (
          <div key={i}>
            <div className="thread-row">
              <span style={{ fontFamily: 'var(--mono)', fontSize: 11, color: 'var(--muted)', width: 16 }}>
                {i + 1}
              </span>
              <span className="swatch big" style={{ background: threadHex(thread) }} />
              <div className="info">
                <div className="name">{thread.name}</div>
                <div className="meta">
                  {thread.manufacturer} {thread.code} · {threadHex(thread)}
                  {stats.threadLengthByColor[i] !== undefined
                    ? ` · ${estimateThreadMetres(stats.threadLengthByColor[i]).toFixed(1)} m`
                    : ''}
                </div>
              </div>
              <button onClick={() => setEditing(editing === i ? null : i)}>
                {editing === i ? 'Close' : 'Change'}
              </button>
            </div>

            {editing === i ? (
              <div style={{ padding: '6px 4px 10px' }}>
                <input
                  placeholder="Search by name, code or hex…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  autoFocus
                />
                <div style={{ maxHeight: 190, overflowY: 'auto', marginTop: 5 }}>
                  {matches.map((t) => (
                    <div
                      key={t.id}
                      className="thread-row"
                      style={{ cursor: 'pointer' }}
                      onClick={() => {
                        props.onReplaceThread(i, t);
                        setEditing(null);
                        setQuery('');
                      }}
                    >
                      <span className="swatch" style={{ background: threadHex(t) }} />
                      <div className="info">
                        <div className="name">{t.name}</div>
                        <div className="meta">
                          {t.manufacturer} {t.code}
                        </div>
                      </div>
                    </div>
                  ))}
                  {matches.length === 0 ? <p className="note">No threads match "{query}".</p> : null}
                </div>
              </div>
            ) : null}
          </div>
        ))}

        <p className="note">
          Artwork colour, display colour and thread colour are separate. The preview draws the thread colour, which
          is what will actually be sewn.
        </p>
        <p className="note">
          Bundled chart: Brother PEC machine palette ({PEC_THREADS.length} colours), defined by the PES format
          itself. Charts not bundled because no verified colour data is available here:{' '}
          {unbundledCharts.map((c) => c.name).join(', ')}. Their codes are deliberately not guessed — ordering the
          wrong cone costs more than looking one up.
        </p>
      </div>
    </section>
  );
}

function ValidatePanel(props: RightPanelProps): React.JSX.Element {
  const report = props.design.validation;
  const result = props.exportResult;

  return (
    <>
      <section className="section">
        <h3>Validation</h3>
        <div className="body">
          {!report ? (
            <p className="note">Not validated yet.</p>
          ) : (
            <>
              <div style={{ marginBottom: 8, fontSize: 12 }}>
                {report.passed ? (
                  <span style={{ color: 'var(--ok)', fontWeight: 700 }}>No blocking errors</span>
                ) : (
                  <span style={{ color: 'var(--error)', fontWeight: 700 }}>
                    {report.errorCount} error{report.errorCount === 1 ? '' : 's'} — export blocked
                  </span>
                )}
                {report.warningCount > 0 ? ` · ${report.warningCount} warning(s)` : ''}
              </div>

              {report.issues.map((issue) => (
                <div key={issue.id} className={`issue ${issue.severity}`}>
                  <span className="sev">{issue.severity}</span>
                  {issue.message}
                  {issue.remedy ? <span className="remedy">{issue.remedy}</span> : null}
                </div>
              ))}
            </>
          )}
        </div>
      </section>

      <section className="section">
        <h3>Export PES</h3>
        <div className="body">
          <div className="field">
            <label style={{ textTransform: 'none', fontSize: 12 }}>
              <input
                type="checkbox"
                style={{ width: 'auto', marginRight: 6 }}
                checked={props.warningsAcknowledged}
                onChange={(e) => props.onAcknowledgeWarnings(e.target.checked)}
                disabled={!report || report.warningCount === 0}
              />
              I have read the {report?.warningCount ?? 0} warning(s) and accept them
            </label>
          </div>

          <button
            className="primary"
            style={{ width: '100%' }}
            onClick={props.onExport}
            disabled={!report || !report.passed || (report.warningCount > 0 && !props.warningsAcknowledged)}
          >
            Export .pes for {props.machine.name}
          </button>

          {result && !result.ok ? <div className="error-banner">{result.blockedReason}</div> : null}

          {result && result.ok ? (
            <>
              <div className="ok-banner" style={{ marginTop: 9 }}>
                Wrote {result.fileName} ({(result.bytes!.length / 1024).toFixed(1)} kB) and verified it.
              </div>
              <ul className="check-list">
                {result.verification.map((c) => (
                  <li key={c.name} className={c.passed ? '' : 'failed'}>
                    {c.name}: {c.detail}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          <p className="note">
            The exported file is a PES version 1 file containing a PEC stitch block. After encoding, the bytes are
            read back and compared with the design; if any check above fails the file is not offered for download.
          </p>
        </div>
      </section>
    </>
  );
}
