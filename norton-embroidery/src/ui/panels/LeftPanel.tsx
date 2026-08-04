import { useRef, useState } from 'react';
import type { EmbroideryDesign } from '../../domain/design';
import type { EmbroideryObject } from '../../domain/embroidery-object';
import { threadHex } from '../../domain/thread';
import { unitsToMm } from '../../domain/units';
import type { ArtworkAnalysis } from '../../processing/image/analysis';
import type { DecodedArtwork } from '../../app/image-decode';

export interface LeftPanelProps {
  design: EmbroideryDesign;
  artwork: DecodedArtwork | null;
  artworkUrl: string | null;
  analysis: ArtworkAnalysis | null;
  selectedObjectId: string | null;
  busy: string | null;
  colorCount: number;
  fillDensityMm: number;
  useUnderlay: boolean;
  onColorCount: (n: number) => void;
  onFillDensity: (mm: number) => void;
  onUseUnderlay: (v: boolean) => void;
  onUpload: (file: File) => void;
  onDigitize: () => void;
  advanced: boolean;
  onSelectObject: (id: string | null) => void;
  onToggleVisible: (id: string) => void;
  onReorder: (id: string, delta: number) => void;
}

export function LeftPanel(props: LeftPanelProps): React.JSX.Element {
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { analysis, artwork } = props;

  const objects = [...props.design.objects].sort((a, b) => a.order - b.order);

  return (
    <aside className="panel left">
      <section className="section">
        <h3>{props.advanced ? '1 · Artwork' : 'Your artwork'}</h3>
        <div className="body">
          {props.artworkUrl ? (
            <img className="artwork-preview" src={props.artworkUrl} alt="Uploaded artwork" />
          ) : null}

          <div
            className={`dropzone${dragActive ? ' active' : ''}`}
            onClick={() => inputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragActive(false);
              const file = e.dataTransfer.files[0];
              if (file) props.onUpload(file);
            }}
          >
            {artwork ? 'Drop another file to replace' : 'Drop artwork here, or click to browse'}
            <br />
            <small>PNG · JPG · JPEG · SVG</small>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) props.onUpload(file);
              e.target.value = '';
            }}
          />

          {artwork ? (
            <dl className="stat-grid" style={{ marginTop: 9 }}>
              <dt>File</dt>
              <dd title={artwork.fileName}>{truncate(artwork.fileName, 18)}</dd>
              <dt>Type</dt>
              <dd>{artwork.isVector ? 'SVG (vector)' : artwork.mimeType.replace('image/', '').toUpperCase()}</dd>
              <dt>Pixels</dt>
              <dd>
                {artwork.width} × {artwork.height}
              </dd>
              <dt>Size</dt>
              <dd>{(artwork.byteLength / 1024).toFixed(1)} kB</dd>
              <dt>Transparency</dt>
              <dd>{artwork.hasTransparency ? 'yes' : 'no'}</dd>
            </dl>
          ) : null}
        </div>
      </section>

      {analysis ? (
        <section className="section">
          <h3>{props.advanced ? '2 · Analysis' : 'How this artwork looks'}</h3>
          <div className="body">
            <div style={{ marginBottom: 8 }}>
              <span className={`suitability ${analysis.suitability}`}>
                {analysis.suitability.replace(/-/g, ' ')}
              </span>{' '}
              <strong>{analysis.embroiderySuitabilityScore}/100</strong>
            </div>

            {!props.advanced ? (
              <p className="note" style={{ marginTop: 0 }}>
                {SUITABILITY_PLAIN[analysis.suitability]}
              </p>
            ) : null}

            <dl className="stat-grid" hidden={!props.advanced}>
              <dt>Colours found</dt>
              <dd>{analysis.detectedColors.length}</dd>
              <dt>Est. source colours</dt>
              <dd>{analysis.estimatedSourceColors}</dd>
              <dt>Background</dt>
              <dd>{analysis.backgroundDetected ? 'detected' : 'none'}</dd>
              <dt>Regions to stitch</dt>
              <dd>{analysis.foregroundRegions.length}</dd>
              <dt>Fill / satin / line</dt>
              <dd>
                {analysis.fillRegionCount} / {analysis.satinRegionCount} / {analysis.lineRegionCount}
              </dd>
              <dt>Edge detail</dt>
              <dd>{(analysis.edgeDensity * 100).toFixed(0)}%</dd>
              <dt>Gradients</dt>
              <dd>{(analysis.gradientScore * 100).toFixed(0)}%</dd>
              <dt>Complexity</dt>
              <dd>{(analysis.complexityScore * 100).toFixed(0)}%</dd>
              <dt>Confidence</dt>
              <dd>{analysis.confidence}</dd>
            </dl>

            <div className="color-chips">
              {analysis.detectedColors.map((c, i) => (
                <div
                  key={i}
                  className="chip"
                  style={{ background: c.hex, outline: c.isBackground ? '2px solid #999' : 'none' }}
                  title={`${c.hex} — ${(c.share * 100).toFixed(1)}%${c.isBackground ? ' (background, not stitched)' : ''}`}
                />
              ))}
            </div>

            {analysis.warnings.map((w, i) => (
              <div key={i} className={`issue ${w.severity}`} style={{ marginTop: 6 }}>
                <span className="sev">{w.severity}</span>
                {w.message}
              </div>
            ))}

            <p className="note" hidden={!props.advanced}>
              {analysis.textRegions.note}
            </p>
          </div>
        </section>
      ) : null}

      <section className="section">
        <h3>{props.advanced ? '3 · Digitize' : 'Design'}</h3>
        <div className="body">
          <div className="field" hidden={!props.advanced}>
            <label htmlFor="colorCount">Colours to use ({props.colorCount})</label>
            <input
              id="colorCount"
              type="range"
              min={2}
              max={12}
              value={props.colorCount}
              onChange={(e) => props.onColorCount(Number(e.target.value))}
            />
          </div>
          <div className="field" hidden={!props.advanced}>
            <label htmlFor="digitize-density">Fill row spacing ({props.fillDensityMm.toFixed(2)} mm)</label>
            <input
              id="digitize-density"
              type="range"
              min={0.25}
              max={1}
              step={0.05}
              value={props.fillDensityMm}
              onChange={(e) => props.onFillDensity(Number(e.target.value))}
            />
          </div>
          <div className="field" hidden={!props.advanced}>
            <label style={{ textTransform: 'none', fontSize: 12 }}>
              <input
                type="checkbox"
                style={{ width: 'auto', marginRight: 6 }}
                checked={props.useUnderlay}
                onChange={(e) => props.onUseUnderlay(e.target.checked)}
              />
              Generate underlay
            </label>
          </div>
          <button
            className="primary"
            style={{ width: '100%' }}
            onClick={props.onDigitize}
            disabled={!artwork || props.busy !== null}
          >
            {props.busy ?? (artwork ? 'Digitize artwork again' : 'Digitize artwork')}
          </button>
          {!artwork ? (
            <p className="note">Upload artwork above. It is turned into stitches automatically.</p>
          ) : (
            <p className="note">
              Stitches are generated automatically when you upload. Use this to redo it after changing a setting.
            </p>
          )}
        </div>
      </section>

      <section className="section" hidden={!props.advanced}>
        <h3>Objects ({objects.length})</h3>
        <ul className="object-list">
          {objects.map((obj, index) => (
            <ObjectRow
              key={obj.id}
              obj={obj}
              index={index}
              color={
                props.design.threadPalette[obj.threadIndex]
                  ? threadHex(props.design.threadPalette[obj.threadIndex])
                  : '#888'
              }
              selected={obj.id === props.selectedObjectId}
              onSelect={() => props.onSelectObject(obj.id === props.selectedObjectId ? null : obj.id)}
              onToggleVisible={() => props.onToggleVisible(obj.id)}
              onReorder={(d) => props.onReorder(obj.id, d)}
            />
          ))}
          {objects.length === 0 ? (
            <li style={{ color: 'var(--muted)', cursor: 'default' }}>No objects yet.</li>
          ) : null}
        </ul>
      </section>
    </aside>
  );
}

function ObjectRow(props: {
  obj: EmbroideryObject;
  index: number;
  color: string;
  selected: boolean;
  onSelect: () => void;
  onToggleVisible: () => void;
  onReorder: (delta: number) => void;
}): React.JSX.Element {
  const { obj } = props;
  return (
    <li className={props.selected ? 'selected' : ''} onClick={props.onSelect}>
      <span style={{ color: 'var(--muted)', fontFamily: 'var(--mono)', fontSize: 10, width: 18 }}>
        {props.index + 1}
      </span>
      <span className="swatch" style={{ background: props.color }} />
      <span className="object-name" title={obj.name}>
        {obj.name}
      </span>
      {obj.notes.length ? <span className="badge" title={obj.notes.join('\n')}>!</span> : null}
      <span className="badge">{obj.type}</span>
      <button
        style={{ padding: '0 4px' }}
        title="Sew earlier"
        onClick={(e) => {
          e.stopPropagation();
          props.onReorder(-1);
        }}
      >
        ↑
      </button>
      <button
        style={{ padding: '0 4px' }}
        title="Sew later"
        onClick={(e) => {
          e.stopPropagation();
          props.onReorder(1);
        }}
      >
        ↓
      </button>
      <button
        style={{ padding: '0 4px' }}
        title={obj.visible ? 'Hide (excluded from the file)' : 'Show'}
        onClick={(e) => {
          e.stopPropagation();
          props.onToggleVisible();
        }}
      >
        {obj.visible ? '●' : '○'}
      </button>
    </li>
  );
}

/**
 * What the suitability rating means, without embroidery vocabulary. The score
 * itself stays visible — this explains what to do about it.
 */
const SUITABILITY_PLAIN: Record<string, string> = {
  good: 'This artwork should turn into a good design automatically. Check the preview, then export.',
  workable:
    'This should work. Look over the preview for anything that has come out thinner or smaller than you expect.',
  'manual-cleanup-likely':
    'This will need some tidying up. Simplify the artwork — fewer colours, no fine detail, no soft edges — and upload it again for a better result.',
  'not-suitable':
    'This artwork is not a good fit for automatic digitizing. Photographs and images with soft shading cannot be stitched directly; redraw it as flat blocks of colour first.',
};

function truncate(s: string, n: number): string {
  return s.length <= n ? s : `${s.slice(0, n - 1)}…`;
}

export function mm(units: number): string {
  return `${unitsToMm(units).toFixed(1)} mm`;
}
