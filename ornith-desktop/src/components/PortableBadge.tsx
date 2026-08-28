import { classifyCapacity, formatBytes, type PortableInfo } from '../../shared/portable';

const RUNTIME_LABELS: Record<PortableInfo['runtime']['source'], string> = {
  bundled: 'runtime on drive',
  external: 'using this computer\u2019s Ollama',
  starting: 'starting the runtime\u2026',
  unavailable: 'no runtime',
};

interface Props {
  info: PortableInfo | null;
}

/**
 * Shown only when running from a drive. Two things matter to someone using a
 * USB install and neither is visible anywhere else: how much room is left
 * before writes start failing, and whether the model server that is answering
 * came off the drive or was already on the machine.
 */
export default function PortableBadge({ info }: Props) {
  if (!info?.portable) return null;

  const level = classifyCapacity(info.volume);
  const space = info.volume ? `${formatBytes(info.volume.freeBytes)} free` : 'space unknown';

  const runtimeLabel = RUNTIME_LABELS[info.runtime.source];

  // The host matters here: while a bundled server is running it is the one
  // answering, whatever the Ollama URL in Settings says.
  const detail = [
    info.root,
    `Models: ${info.modelsDir}`,
    `Serving: ${info.runtime.host}`,
    info.runtime.reason,
  ]
    .filter(Boolean)
    .join('\n');

  return (
    <div className="portable-badge" data-testid="portable-badge" data-capacity={level} title={detail}>
      <span className="portable-label">{info.label}</span>
      <span className="portable-detail">
        {space} · {runtimeLabel}
      </span>
      {info.volume && !info.volume.writable ? (
        <span className="portable-detail status-error">Drive is read-only</span>
      ) : null}

      {info.runtime.source === 'unavailable' && info.runtime.reason ? (
        <span className="portable-reason" data-testid="portable-reason">
          {info.runtime.reason}
        </span>
      ) : null}
    </div>
  );
}
