import { useState, type FormEvent } from 'react';
import { Empty, Field, List, Notice, Row, Screen, SectionLabel, Spinner } from '../../ui';
import { useAsync } from '../../state/useAsync';
import * as api from '../../lib/api';

export default function Locations() {
  const locations = useAsync(() => api.fetchLocations(true), []);
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      locations.reload();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function add(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    void act(() => api.upsertLocation({ name: name.trim() })).then(() => setName(''));
  }

  function rename(id: string, current: string) {
    const next = window.prompt('Location name', current);
    if (next && next.trim() && next.trim() !== current) {
      void act(() => api.upsertLocation({ id, name: next.trim() }));
    }
  }

  return (
    <Screen title="Locations" back="/admin">
      {error && <Notice>{error}</Notice>}
      {locations.loading && <Spinner />}

      <SectionLabel>Restaurants</SectionLabel>
      {(locations.data ?? []).length === 0 ? (
        <Empty>No locations yet.</Empty>
      ) : (
        <List>
          {(locations.data ?? []).map((location) => (
            <Row
              key={location.id}
              title={location.name}
              subtitle={location.active ? undefined : 'Inactive'}
              onClick={() => rename(location.id, location.name)}
              trailing={
                <button
                  type="button"
                  className="linkbtn linkbtn--quiet"
                  disabled={busy}
                  onClick={(event) => {
                    event.stopPropagation();
                    void act(() =>
                      api.upsertLocation({
                        id: location.id,
                        name: location.name,
                        active: !location.active,
                      }),
                    );
                  }}
                >
                  {location.active ? 'Deactivate' : 'Activate'}
                </button>
              }
            />
          ))}
        </List>
      )}

      <form onSubmit={add}>
        <SectionLabel>Add a location</SectionLabel>
        <div className="card">
          <Field
            label="Name"
            value={name}
            placeholder="Hibachio 4"
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div style={{ marginTop: 12 }}>
          <button type="submit" className="btn" disabled={busy || !name.trim()}>
            Add location
          </button>
        </div>
      </form>

      <p className="meta" style={{ marginTop: 24 }}>
        Deactivating a location keeps its history. It just stops appearing when someone records a
        new transfer.
      </p>
    </Screen>
  );
}
