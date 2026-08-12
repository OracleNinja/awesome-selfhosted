import type { AiMode } from '../../shared/types';

interface Props {
  onChoose: (mode: AiMode) => void;
}

/**
 * First-run choice, shown once.
 *
 * The app defaults to Local and does not send a conversation anywhere until
 * this is answered — a silent online default would mean transmitting the user's
 * first message before they knew there was a choice.
 */
export default function ModeChooser({ onChoose }: Props) {
  return (
    <div className="modal-backdrop" data-testid="mode-chooser">
      <div className="modal mode-modal" role="dialog" aria-modal="true" aria-label="Choose how Ornith answers">
        <header className="modal-header">
          <h2>Choose how Ornith answers</h2>
        </header>

        <div className="modal-body mode-options">
          <button
            type="button"
            className="mode-option"
            onClick={() => onChoose('local')}
            data-testid="choose-local"
          >
            <span className="mode-option-title">Local</span>
            <span className="mode-option-body">
              Runs through Ollama on this Mac. Private and offline. Requires a local model.
            </span>
          </button>

          <button
            type="button"
            className="mode-option"
            onClick={() => onChoose('online')}
            data-testid="choose-online"
          >
            <span className="mode-option-title">Online</span>
            <span className="mode-option-body">
              Uses cloud AI and web retrieval. Faster, and better for current information.{' '}
              <strong>Your conversation is sent to the configured online provider.</strong>
            </span>
          </button>
        </div>

        <footer className="modal-footer">
          <span className="field-hint">
            You can change this at any time in Settings. Voice input and speech stay on this Mac in
            both modes.
          </span>
        </footer>
      </div>
    </div>
  );
}
