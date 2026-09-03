import { useState } from 'react';
import { List, Notice, Row, SectionLabel, Spinner } from '../ui';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import * as api from '../lib/api';

/**
 * Where an account waits. A pending user can do exactly one thing here: say
 * which restaurant they work at, so the approving admin has something to go on.
 */
export default function Pending() {
  const { profile, refresh } = useSession();
  const locations = useAsync(() => api.fetchLocations(), []);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const disabled = profile?.status === 'disabled';

  async function choose(id: string) {
    setSaving(id);
    setError(null);
    try {
      await api.setRequestedLocation(id);
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="screen">
      <div className="screen__body" style={{ paddingTop: 'calc(32px + env(safe-area-inset-top))' }}>
        <h1 className="page-title">{disabled ? 'Access turned off' : 'Waiting for approval'}</h1>
        <p className="page-subtitle">
          {disabled
            ? 'Your account has been switched off. Talk to a manager if that is a mistake.'
            : 'A manager at your restaurant needs to approve your account. This usually takes a few minutes.'}
        </p>

        {!disabled && (
          <>
            <SectionLabel>Where do you work?</SectionLabel>
            {locations.loading && <Spinner />}
            {locations.error && <Notice>{locations.error}</Notice>}
            {error && <Notice>{error}</Notice>}
            {locations.data && (
              <List>
                {locations.data.map((location) => (
                  <Row
                    key={location.id}
                    title={location.name}
                    onClick={() => choose(location.id)}
                    disabled={saving !== null}
                    trailing={
                      profile?.location_id === location.id ? <span className="tag tag--ok">Selected</span> : undefined
                    }
                  />
                ))}
              </List>
            )}
          </>
        )}

        <div style={{ marginTop: 32 }}>
          <button type="button" className="btn btn--secondary" onClick={() => void api.signOut()}>
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
