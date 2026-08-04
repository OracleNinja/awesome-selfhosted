import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { computeStats, type EmbroideryDesign } from '../domain/design';
import { getHoop, getMachine, type MachineProfile } from '../domain/machine';
import { colorBlocks } from '../domain/stitch';
import { buildStitchSequence, colorStops } from '../domain/stitch-sequence';
import { buildReadinessReport, readinessReportText, type ExportSnapshot } from '../domain/readiness';
import { stitchOutWorksheetText } from '../app/worksheet';
import { PEC_THREADS, type Thread } from '../domain/thread';
import { mmToUnits, unitsToMm } from '../domain/units';
import type { EmbroideryObject, StitchType } from '../domain/embroidery-object';
import type { ArtworkAnalysis } from '../processing/image/analysis';
import { moveInOrder } from '../processing/optimize/order';
import { validateDesign } from '../processing/validate/validate-design';

import {
  analyze,
  createProject,
  digitizeAndGenerate,
  regenerate,
  type NewProjectInput,
} from '../app/pipeline';
import {
  centerDesign,
  deleteObject,
  duplicateObject,
  moveObject,
  replaceThread,
  rotateObject,
  scaleDesign,
  scaleObject,
  reversePath,
  setObjectProperty,
  setStartPoint,
  setStitchType,
} from '../app/editor-ops';
import { exportPes, type ExportResult } from '../app/export-pes';
import { canRedo, canUndo, createHistory, push, redo, undo, type History } from '../app/history';
import { decodeArtworkFile, decodeStoredArtwork, type DecodedArtwork } from '../app/image-decode';
import { readPes } from '../infra/pes/pes-reader';
import {
  projectFileName,
  projectFromJson,
  projectToJson,
  type ProjectState,
} from '../infra/project/project-format';
import {
  deleteProject as deleteStoredProject,
  duplicateProject,
  isStorageAvailable,
  listProjects,
  loadProject,
  saveProject,
  type ProjectSummary,
} from '../infra/storage/project-store';

import { DesignCanvas } from './DesignCanvas';
import { Timeline } from './Timeline';
import { NewProjectDialog } from './NewProjectDialog';
import { LeftPanel } from './panels/LeftPanel';
import { RightPanel } from './panels/RightPanel';

export function App(): React.JSX.Element {
  const [projectId, setProjectId] = useState<string>(() => crypto.randomUUID());
  const [machine, setMachine] = useState<MachineProfile | null>(null);
  const [history, setHistory] = useState<History | null>(null);
  const [artwork, setArtwork] = useState<DecodedArtwork | null>(null);
  const [artworkUrl, setArtworkUrl] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<ArtworkAnalysis | null>(null);
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showNewProject, setShowNewProject] = useState(true);
  const [showProjects, setShowProjects] = useState(false);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [previewMode, setPreviewMode] = useState<'stitch' | 'artwork'>('stitch');

  // Digitizing settings.
  const [colorCount, setColorCount] = useState(6);
  const [fillDensityMm, setFillDensityMm] = useState(0.4);
  const [useUnderlay, setUseUnderlay] = useState(true);

  // Simulator state.
  const [position, setPosition] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(600);
  const rafRef = useRef<number | null>(null);
  const lastFrame = useRef<number>(0);

  const design = history?.present.design ?? null;
  const hoop = machine && design ? (getHoop(machine, design.canvas.hoopId) ?? null) : null;
  const stats = useMemo(
    () => (design ? computeStats(design) : { width: 0, height: 0, stitchCount: 0, colorCount: 0, estimatedRuntimeSeconds: 0 }),
    [design],
  );

  // One artifact, built once, consumed by the preview, the timeline, the
  // status panel and the exporter.
  const sequence = useMemo(() => (design ? buildStitchSequence(design) : null), [design]);

  const selectedObject = useMemo(
    () => design?.objects.find((o) => o.id === selectedObjectId) ?? null,
    [design, selectedObjectId],
  );

  const highlightRange = useMemo(() => {
    if (!design || !selectedObject) return null;
    // Highlight the colour block the selected object belongs to.
    const block = colorBlocks(design.stitches)[selectedObject.threadIndex];
    return block ? { start: block.start, end: block.endExclusive } : null;
  }, [design, selectedObject]);

  const commit = useCallback(
    (next: EmbroideryDesign, label: string) => {
      setHistory((h) => (h ? push(h, next, label) : createHistory(next, label)));
      setExportResult(null);
      setWarningsAcknowledged(false);
    },
    [],
  );

  // --- project lifecycle -------------------------------------------------

  const handleCreateProject = (input: NewProjectInput): void => {
    try {
      const { design: fresh, machine: m } = createProject(input);
      setMachine(m);
      setProjectId(crypto.randomUUID());
      setHistory(createHistory(regenerate(fresh, m), 'New project'));
      setArtwork(null);
      setArtworkUrl(null);
      setAnalysis(null);
      setSelectedObjectId(null);
      setExportResult(null);
      setShowNewProject(false);
      setError(null);
      setPosition(-1);
    } catch (err) {
      setError(message(err));
    }
  };

  const handleUpload = async (file: File): Promise<void> => {
    if (!design) return;
    setBusy('Reading artwork…');
    setError(null);
    try {
      const decoded = await decodeArtworkFile(file);
      setArtwork(decoded);
      if (artworkUrl) URL.revokeObjectURL(artworkUrl);
      setArtworkUrl(URL.createObjectURL(file));
      setBusy('Analysing artwork…');
      // Yield so the busy label paints before the analysis blocks the thread.
      await tick();
      const result = analyze(decoded.image, design.canvas, { colorCount });
      setAnalysis(result);
    } catch (err) {
      setError(message(err));
      setArtwork(null);
      setAnalysis(null);
    } finally {
      setBusy(null);
    }
  };

  const handleDigitize = async (): Promise<void> => {
    if (!design || !machine || !artwork) return;
    setBusy('Digitizing…');
    setError(null);
    try {
      await tick();
      const fresh = analyze(artwork.image, design.canvas, { colorCount });
      setAnalysis(fresh);
      const result = digitizeAndGenerate(design, fresh, machine, {
        threads: PEC_THREADS,
        digitize: { fillDensity: mmToUnits(fillDensityMm), underlay: useUnderlay },
      });
      commit(result.design, 'Digitize artwork');
      setSelectedObjectId(null);
      setPosition(-1);
      if (result.digitize.warnings.length) setError(result.digitize.warnings.join(' '));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(null);
    }
  };

  // A compact record of the last export, used by the readiness report so it can
  // say whether the file on disk still matches what is on screen.
  const lastExport: ExportSnapshot | null = exportResult
    ? {
        ok: exportResult.ok,
        sequenceId: exportResult.sequenceId,
        fileName: exportResult.fileName,
        byteLength: exportResult.bytes?.length ?? 0,
        checks: exportResult.verification.map((c) => ({ name: c.name, passed: c.passed, detail: c.detail })),
        blockedReason: exportResult.blockedReason,
      }
    : null;

  const handleDownloadReport = (): void => {
    if (!design || !machine) return;
    const report = buildReadinessReport({ design, machine, artworkLoaded: artwork !== null, lastExport });
    const text = readinessReportText(report, design, machine);
    download(new TextEncoder().encode(text), `${sanitize(design.metadata.name)}-readiness.txt`, 'text/plain');
  };

  const handleDownloadWorksheet = (): void => {
    if (!design || !machine || !sequence) return;
    const text = stitchOutWorksheetText({
      design,
      machine,
      hoop,
      sequence,
      stops: colorStops(sequence),
      exportedFileName: exportResult?.ok ? exportResult.fileName : null,
    });
    download(new TextEncoder().encode(text), `${sanitize(design.metadata.name)}-stitchout.txt`, 'text/plain');
  };

  const handleExport = (): void => {
    if (!design || !machine) return;
    const result = exportPes(design, machine, { acknowledgeWarnings: warningsAcknowledged });
    setExportResult(result);
    if (result.ok && result.bytes) {
      download(result.bytes, result.fileName, 'application/octet-stream');
    }
  };

  const handleSaveProject = async (): Promise<void> => {
    if (!design) return;
    const state: ProjectState = {
      id: projectId,
      design,
      artwork: artwork
        ? {
            fileName: artwork.fileName,
            mimeType: artwork.mimeType,
            base64: artwork.base64,
            width: artwork.width,
            height: artwork.height,
            byteLength: artwork.byteLength,
          }
        : null,
      analysis: analysis ? stripHeavyFields(analysis) : null,
    };
    try {
      if (isStorageAvailable()) await saveProject(state);
      setError(null);
    } catch (err) {
      setError(message(err));
    }
  };

  const handleSaveAsFile = (): void => {
    if (!design) return;
    const state: ProjectState = {
      id: projectId,
      design,
      artwork: artwork
        ? {
            fileName: artwork.fileName,
            mimeType: artwork.mimeType,
            base64: artwork.base64,
            width: artwork.width,
            height: artwork.height,
            byteLength: artwork.byteLength,
          }
        : null,
      analysis: analysis ? stripHeavyFields(analysis) : null,
    };
    const json = projectToJson(state);
    download(new TextEncoder().encode(json), projectFileName(design.metadata.name), 'application/json');
  };

  const openProjectState = async (state: ProjectState): Promise<void> => {
    const m = getMachine(state.design.canvas.machineId);
    if (!m) {
      setError(`This project needs machine profile "${state.design.canvas.machineId}", which is not installed.`);
      return;
    }
    setMachine(m);
    setProjectId(state.id);
    setHistory(createHistory(state.design, 'Opened project'));
    setSelectedObjectId(null);
    setExportResult(null);
    setPosition(-1);
    setShowProjects(false);
    setShowNewProject(false);
    setAnalysis(null);

    if (state.artwork) {
      try {
        const decoded = await decodeStoredArtwork(
          state.artwork.base64,
          state.artwork.mimeType,
          state.artwork.fileName,
        );
        setArtwork(decoded);
        if (artworkUrl) URL.revokeObjectURL(artworkUrl);
        const blob = new Blob([decoded.image.data.slice(0) as unknown as BlobPart]);
        // Rebuild a displayable URL from the stored original bytes.
        void blob;
        setArtworkUrl(`data:${state.artwork.mimeType};base64,${state.artwork.base64}`);
        setAnalysis(analyze(decoded.image, state.design.canvas, { colorCount }));
      } catch (err) {
        setError(`The project opened, but its artwork could not be decoded: ${message(err)}`);
      }
    } else {
      setArtwork(null);
      setArtworkUrl(null);
    }
  };

  const handleOpenProjectFile = async (file: File): Promise<void> => {
    try {
      const text = await file.text();
      await openProjectState(projectFromJson(text));
      setError(null);
    } catch (err) {
      setError(message(err));
    }
  };

  const handleImportStitchFile = async (file: File): Promise<void> => {
    if (!machine) return;
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const imported = readPes(bytes);
      const threads: Thread[] = imported.threads.length ? imported.threads : [PEC_THREADS[19]];
      const base = design ?? createProject({
        name: imported.name,
        machineId: machine.id,
        hoopId: machine.defaultHoopId,
        width: mmToUnits(100),
        height: mmToUnits(100),
      }).design;

      const next: EmbroideryDesign = {
        ...base,
        metadata: { ...base.metadata, name: imported.name, modifiedAt: new Date().toISOString() },
        objects: [],
        threadPalette: threads,
        stitches: imported.stitches,
        // Imported files carry one palette entry per colour block already.
        colorSequence: null,
        validation: null,
      };
      // Validate without regenerating: there are no objects to generate from.
      commit({ ...next, validation: validateDesign(next, machine) }, `Import ${file.name}`);
      setSelectedObjectId(null);
      setPosition(-1);
      setError(
        imported.limitations.length
          ? `Imported ${file.name}. ${imported.limitations.join(' ')}`
          : null,
      );
    } catch (err) {
      setError(message(err));
    }
  };

  const refreshProjects = async (): Promise<void> => {
    if (!isStorageAvailable()) return;
    try {
      setProjects(await listProjects());
    } catch (err) {
      setError(message(err));
    }
  };

  // --- editing -----------------------------------------------------------

  const applyOp = (
    op: (d: EmbroideryDesign, m: MachineProfile) => EmbroideryDesign,
    label: string,
  ): void => {
    if (!design || !machine) return;
    try {
      commit(op(design, machine), label);
    } catch (err) {
      setError(message(err));
    }
  };

  // --- simulation --------------------------------------------------------

  useEffect(() => {
    if (!playing || !design) return;
    const step = (now: number): void => {
      const dt = lastFrame.current ? (now - lastFrame.current) / 1000 : 0;
      lastFrame.current = now;
      setPosition((p) => {
        const start = p < 0 ? 0 : p;
        const next = start + speed * dt;
        if (next >= design.stitches.length) {
          setPlaying(false);
          return design.stitches.length;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(step);
    };
    rafRef.current = requestAnimationFrame(step);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      lastFrame.current = 0;
    };
  }, [playing, speed, design]);

  const jumpColor = (delta: number): void => {
    if (!design) return;
    const blocks = colorBlocks(design.stitches);
    const current = position < 0 ? design.stitches.length : position;
    const starts = blocks.map((b) => b.start);
    if (delta > 0) {
      const next = starts.find((s) => s > current);
      setPosition(next ?? design.stitches.length);
    } else {
      const previous = [...starts].reverse().find((s) => s < current - 1);
      setPosition(previous ?? 0);
    }
    setPlaying(false);
  };

  // --- keyboard shortcuts -------------------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const target = e.target as HTMLElement;
      if (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'TEXTAREA') return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'z' && !e.shiftKey) {
        e.preventDefault();
        setHistory((h) => (h ? undo(h) : h));
      } else if (mod && (e.key.toLowerCase() === 'y' || (e.key.toLowerCase() === 'z' && e.shiftKey))) {
        e.preventDefault();
        setHistory((h) => (h ? redo(h) : h));
      } else if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void handleSaveProject();
      } else if (e.key === 'Delete' && selectedObjectId) {
        e.preventDefault();
        applyOp(deleteObject(selectedObjectId), 'Delete object');
        setSelectedObjectId(null);
      } else if (e.key === ' ') {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  useEffect(() => {
    void refreshProjects();
  }, []);

  // --- render ------------------------------------------------------------

  if (showNewProject || !design || !machine || !history) {
    return (
      <>
        <div className="app">
          <div className="topbar">
            <span className="brand">
              NORTON <span>THREAD CO.</span>
            </span>
            <span style={{ color: '#9aa2ad' }}>Embroidery digitizing</span>
          </div>
        </div>
        <NewProjectDialog
          onCreate={handleCreateProject}
          onCancel={() => setShowNewProject(false)}
          canCancel={design !== null && machine !== null}
        />
      </>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          NORTON <span>THREAD CO.</span>
        </span>
        <button onClick={() => setShowNewProject(true)}>New</button>
        <button
          onClick={() => {
            void refreshProjects();
            setShowProjects(true);
          }}
        >
          Projects
        </button>
        <button onClick={() => void handleSaveProject()}>Save</button>
        <button onClick={handleSaveAsFile}>Save as file</button>
        <FilePickerButton label="Open file" accept=".norton-embroidery-project,application/json" onPick={handleOpenProjectFile} />
        <FilePickerButton label="Import PES" accept=".pes,.pec" onPick={handleImportStitchFile} />

        <span style={{ width: 10 }} />
        <button disabled={!canUndo(history)} onClick={() => setHistory(undo(history))} title="Ctrl/Cmd+Z">
          ↶ Undo
        </button>
        <button disabled={!canRedo(history)} onClick={() => setHistory(redo(history))} title="Ctrl/Cmd+Shift+Z">
          ↷ Redo
        </button>

        <span style={{ width: 10 }} />
        <button
          className={previewMode === 'artwork' ? 'primary' : ''}
          onClick={() => setPreviewMode(previewMode === 'artwork' ? 'stitch' : 'artwork')}
          disabled={!artworkUrl}
        >
          {previewMode === 'artwork' ? 'Stitch preview' : 'Artwork preview'}
        </button>

        <span className="spacer" />
        <span className="hardware-banner" title="No design from this application has been confirmed on physical hardware yet.">
          HARDWARE NOT VERIFIED
        </span>
        <span className="project-name">
          {design.metadata.name}
          {design.metadata.customer ? ` · ${design.metadata.customer}` : ''} · {machine.name}
        </span>
      </header>

      <LeftPanel
        design={design}
        artwork={artwork}
        artworkUrl={artworkUrl}
        analysis={analysis}
        selectedObjectId={selectedObjectId}
        busy={busy}
        colorCount={colorCount}
        fillDensityMm={fillDensityMm}
        useUnderlay={useUnderlay}
        onColorCount={setColorCount}
        onFillDensity={setFillDensityMm}
        onUseUnderlay={setUseUnderlay}
        onUpload={(file) => void handleUpload(file)}
        onDigitize={() => void handleDigitize()}
        onSelectObject={setSelectedObjectId}
        onToggleVisible={(id) => {
          const obj = design.objects.find((o) => o.id === id);
          if (obj) applyOp(setObjectProperty(id, 'visible', !obj.visible), 'Toggle visibility');
        }}
        onReorder={(id, delta) => {
          const sorted = [...design.objects].sort((a, b) => a.order - b.order);
          const index = sorted.findIndex((o) => o.id === id);
          if (index < 0) return;
          const objects = moveInOrder(design.objects, id, index + delta);
          applyOp((d, m) => regenerate({ ...d, objects }, m), 'Reorder object');
        }}
      />

      <DesignCanvas
        stitches={design.stitches}
        blockThreads={sequence?.blockThreads ?? []}
        hoop={hoop}
        upTo={position < 0 ? -1 : Math.floor(position)}
        highlightRange={highlightRange}
        mode={previewMode}
        artworkUrl={artworkUrl}
        stats={stats}
      />

      <Timeline
        stitches={design.stitches}
        blockThreads={sequence?.blockThreads ?? []}
        position={position < 0 ? design.stitches.length : Math.floor(position)}
        playing={playing}
        speed={speed}
        estimatedRuntimeSeconds={stats.estimatedRuntimeSeconds ?? 0}
        onSeek={(p) => {
          setPosition(p);
          setPlaying(false);
        }}
        onPlayPause={() => {
          if (position < 0 || position >= design.stitches.length) setPosition(0);
          setPlaying((p) => !p);
        }}
        onRestart={() => {
          setPosition(0);
          setPlaying(false);
        }}
        onStep={(delta) => {
          setPlaying(false);
          setPosition((p) => {
            const base = p < 0 ? design.stitches.length : p;
            return Math.max(0, Math.min(design.stitches.length, Math.floor(base) + delta));
          });
        }}
        onJumpColor={jumpColor}
        onSpeed={setSpeed}
        onShowAll={() => {
          setPlaying(false);
          setPosition(-1);
        }}
      />

      <RightPanel
        design={design}
        machine={machine}
        hoop={hoop}
        selected={selectedObject}
        sequence={sequence}
        artworkLoaded={artwork !== null}
        lastExport={lastExport}
        onDownloadReport={handleDownloadReport}
        onDownloadWorksheet={handleDownloadWorksheet}
        exportResult={exportResult}
        warningsAcknowledged={warningsAcknowledged}
        onAcknowledgeWarnings={setWarningsAcknowledged}
        onExport={handleExport}
        onObjectChange={(key, value) => {
          if (!selectedObjectId) return;
          applyOp(setObjectProperty(selectedObjectId, key, value), `Change ${String(key)}`);
        }}
        onStitchType={(type: StitchType) => {
          if (!selectedObjectId) return;
          applyOp(setStitchType(selectedObjectId, type), `Change stitch type to ${type}`);
        }}
        onDeleteObject={() => {
          if (!selectedObjectId) return;
          applyOp(deleteObject(selectedObjectId), 'Delete object');
          setSelectedObjectId(null);
        }}
        onDuplicateObject={() => selectedObjectId && applyOp(duplicateObject(selectedObjectId), 'Duplicate object')}
        onMoveObject={(dx, dy) => selectedObjectId && applyOp(moveObject(selectedObjectId, dx, dy), 'Move object')}
        onScaleObject={(f) => selectedObjectId && applyOp(scaleObject(selectedObjectId, f, f), 'Scale object')}
        onRotateObject={(d) => selectedObjectId && applyOp(rotateObject(selectedObjectId, d), 'Rotate object')}
        onShiftStartPoint={() => {
          if (!selectedObject) return;
          const points =
            selectedObject.geometry.kind === 'polygon'
              ? selectedObject.geometry.polygon.outer.length
              : selectedObject.geometry.kind === 'path'
                ? selectedObject.geometry.points.length
                : 0;
          if (points < 2) return;
          // Advance by a tenth of the outline, which is a visible step at any size.
          applyOp(setStartPoint(selectedObject.id, Math.max(1, Math.round(points / 10))), 'Shift start point');
        }}
        onReversePath={() => selectedObjectId && applyOp(reversePath(selectedObjectId), 'Reverse path')}
        onReplaceThread={(index, thread) =>
          applyOp((d, m) => regenerate(replaceThread(d, index, thread), m), 'Replace thread')
        }
        onCenterDesign={() =>
          hoop && applyOp((d, m) => centerDesign(d, m, hoop.width, hoop.height), 'Centre design')
        }
        onScaleDesign={(f) => applyOp((d, m) => scaleDesign(d, m, f), 'Scale design')}
      />

      {error ? (
        <div
          className="error-banner"
          style={{ position: 'fixed', bottom: 128, left: '50%', transform: 'translateX(-50%)', zIndex: 40, maxWidth: 640 }}
          onClick={() => setError(null)}
        >
          {error} <em>(click to dismiss)</em>
        </div>
      ) : null}

      {showProjects ? (
        <ProjectsDialog
          projects={projects}
          onOpen={async (id) => {
            const state = await loadProject(id);
            if (state) await openProjectState(state);
            else setError('That project could not be found in local storage.');
          }}
          onDuplicate={async (id) => {
            const existing = await loadProject(id);
            const copy = await duplicateProject(id, `${existing?.design.metadata.name ?? 'Project'} copy`);
            if (copy) await refreshProjects();
            else setError('That project could not be duplicated.');
          }}
          onDelete={async (id) => {
            await deleteStoredProject(id);
            await refreshProjects();
          }}
          onClose={() => setShowProjects(false)}
        />
      ) : null}
    </div>
  );
}

function ProjectsDialog(props: {
  projects: ProjectSummary[];
  onOpen: (id: string) => Promise<void>;
  onDuplicate: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onClose: () => void;
}): React.JSX.Element {
  return (
    <div className="modal-backdrop" onClick={props.onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>Saved projects</h2>
        <div className="body">
          {props.projects.length === 0 ? (
            <p className="note">No projects saved on this computer yet. Use Save to store one.</p>
          ) : (
            <table className="project-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Customer</th>
                  <th>Stitches</th>
                  <th>Modified</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {props.projects.map((p) => (
                  <tr key={p.id}>
                    <td>{p.name}</td>
                    <td>{p.customer ?? '—'}</td>
                    <td style={{ fontFamily: 'var(--mono)' }}>{p.stitchCount.toLocaleString()}</td>
                    <td>{new Date(p.modifiedAt).toLocaleString()}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <button onClick={() => void props.onOpen(p.id)}>Open</button>{' '}
                      <button onClick={() => void props.onDuplicate(p.id)}>Duplicate</button>{' '}
                      <button className="danger" onClick={() => void props.onDelete(p.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="footer">
          <button onClick={props.onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function FilePickerButton(props: {
  label: string;
  accept: string;
  onPick: (file: File) => Promise<void> | void;
}): React.JSX.Element {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <>
      <button onClick={() => ref.current?.click()}>{props.label}</button>
      <input
        ref={ref}
        type="file"
        accept={props.accept}
        style={{ display: 'none' }}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void props.onPick(file);
          e.target.value = '';
        }}
      />
    </>
  );
}

function download(bytes: Uint8Array, fileName: string, mimeType: string): void {
  const blob = new Blob([bytes as unknown as BlobPart], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Drop the pixel buffers before storing an analysis in a project file. */
function stripHeavyFields(a: ArtworkAnalysis): Omit<ArtworkAnalysis, 'quantized' | 'regions'> {
  const { quantized: _q, regions: _r, ...rest } = a;
  return rest;
}

function sanitize(name: string): string {
  const cleaned = name.replace(/[^a-zA-Z0-9-_ ]/g, '').trim().replace(/\s+/g, '-');
  return cleaned || 'design';
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

const tick = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

export { unitsToMm, type EmbroideryObject };
