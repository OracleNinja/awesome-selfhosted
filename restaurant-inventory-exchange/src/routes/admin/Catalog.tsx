import { useMemo, useState, type FormEvent } from 'react';
import { Empty, Field, List, Notice, Row, Screen, Search, SectionLabel, SelectField, Spinner } from '../../ui';
import { useAsync } from '../../state/useAsync';
import * as api from '../../lib/api';
import type { InventoryItem } from '../../lib/types';

const UNITS = ['sleeve', 'case', 'box', 'bag', 'bundle', 'roll', 'tray', 'bottle', 'each'];

export default function Catalog() {
  const items = useAsync(() => api.fetchItems(true), []);
  const locations = useAsync(() => api.fetchLocations(true), []);
  const [search, setSearch] = useState('');
  const [editing, setEditing] = useState<InventoryItem | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    const pool = items.data ?? [];
    if (!term) return pool;
    return pool.filter(
      (i) => i.name.toLowerCase().includes(term) || i.category.toLowerCase().includes(term),
    );
  }, [items.data, search]);

  async function save(input: Parameters<typeof api.upsertItem>[0]) {
    setBusy(true);
    setError(null);
    try {
      await api.upsertItem(input);
      items.reload();
      setEditing(null);
      setCreating(false);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (creating || editing) {
    return (
      <ItemForm
        item={editing}
        locations={locations.data ?? []}
        busy={busy}
        error={error}
        onCancel={() => {
          setEditing(null);
          setCreating(false);
          setError(null);
        }}
        onSave={save}
      />
    );
  }

  return (
    <Screen
      title="Catalog"
      back="/admin"
      action={
        <button type="button" className="linkbtn" onClick={() => setCreating(true)}>
          Add
        </button>
      }
    >
      <Search value={search} onChange={setSearch} placeholder="Search catalog" />
      {items.loading && <Spinner />}
      {error && <Notice>{error}</Notice>}
      {!items.loading && visible.length === 0 && <Empty>No items match.</Empty>}

      {groupByCategory(visible).map(([category, list]) => (
        <div key={category}>
          <SectionLabel>{category}</SectionLabel>
          <List>
            {list.map((item) => (
              <Row
                key={item.id}
                title={item.name}
                subtitle={[
                  item.unit,
                  item.sku ?? null,
                  item.active ? null : 'Inactive',
                  item.location_id
                    ? locations.data?.find((l) => l.id === item.location_id)?.name ?? 'One location'
                    : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                onClick={() => setEditing(item)}
              />
            ))}
          </List>
        </div>
      ))}
    </Screen>
  );
}

function ItemForm({
  item,
  locations,
  busy,
  error,
  onCancel,
  onSave,
}: {
  item: InventoryItem | null;
  locations: Array<{ id: string; name: string }>;
  busy: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (input: Parameters<typeof api.upsertItem>[0]) => void;
}) {
  const [name, setName] = useState(item?.name ?? '');
  const [category, setCategory] = useState(item?.category ?? 'General');
  const [unit, setUnit] = useState(item?.unit ?? 'each');
  const [sku, setSku] = useState(item?.sku ?? '');
  const [notes, setNotes] = useState(item?.notes ?? '');
  const [active, setActive] = useState(item?.active ?? true);
  const [locationId, setLocationId] = useState(item?.location_id ?? '');

  function submit(event: FormEvent) {
    event.preventDefault();
    onSave({
      id: item?.id ?? null,
      name,
      category,
      unit,
      sku: sku || null,
      notes: notes || null,
      active,
      locationId: locationId || null,
    });
  }

  return (
    <Screen title={item ? 'Edit item' : 'New item'} back={onCancel}>
      <form onSubmit={submit}>
        {error && <Notice>{error}</Notice>}
        <SectionLabel>Item</SectionLabel>
        <div className="card">
          <Field label="Name" value={name} required onChange={(e) => setName(e.target.value)} />
          <Field
            label="Category"
            value={category}
            placeholder="Cups"
            onChange={(e) => setCategory(e.target.value)}
          />
          <SelectField label="Unit" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {UNITS.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </SelectField>
          <Field label="SKU (optional)" value={sku} onChange={(e) => setSku(e.target.value)} />
          <Field label="Notes (optional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>

        <SectionLabel>Availability</SectionLabel>
        <div className="card">
          <SelectField
            label="Belongs to"
            value={locationId}
            onChange={(e) => setLocationId(e.target.value)}
          >
            <option value="">Every location (shared)</option>
            {locations.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} only
              </option>
            ))}
          </SelectField>
          <SelectField
            label="Status"
            value={active ? 'active' : 'inactive'}
            onChange={(e) => setActive(e.target.value === 'active')}
          >
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </SelectField>
        </div>

        <div className="stack" style={{ marginTop: 20 }}>
          <button type="submit" className="btn" disabled={busy || !name.trim()}>
            {busy ? 'Saving…' : 'Save item'}
          </button>
          <button type="button" className="btn btn--secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </form>
    </Screen>
  );
}

function groupByCategory(items: InventoryItem[]): Array<[string, InventoryItem[]]> {
  const map = new Map<string, InventoryItem[]>();
  for (const item of items) {
    const list = map.get(item.category) ?? [];
    list.push(item);
    map.set(item.category, list);
  }
  return [...map.entries()].sort(([a], [b]) => a.localeCompare(b));
}
