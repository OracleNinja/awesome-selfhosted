import { useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  CheckMark,
  Empty,
  List,
  Notice,
  Row,
  Screen,
  Search,
  SectionLabel,
  Spinner,
  Stepper,
} from '../ui';
import { useAsync } from '../state/useAsync';
import { useSession } from '../state/session';
import { amount } from '../lib/format';
import * as api from '../lib/api';
import type { DraftLine, InventoryItem, TransferKind } from '../lib/types';

type Step = 'location' | 'item' | 'quantity' | 'done';

/**
 * The whole point of the app: pick a shop, pick an item, pick a number, done.
 * Each step is one screen with one decision on it, so it stays usable one
 * handed on a phone in a kitchen.
 */
export default function RecordTransfer() {
  const { kind } = useParams<{ kind: string }>();
  const transferKind: TransferKind = kind === 'give' ? 'give' : 'take';
  const navigate = useNavigate();
  const { profile } = useSession();
  const myLocationId = profile?.location_id ?? null;

  const locations = useAsync(() => api.fetchLocations(), []);
  const items = useAsync(() => api.fetchItems(), []);

  const [step, setStep] = useState<Step>('location');
  const [counterpartyId, setCounterpartyId] = useState<string | null>(null);
  const [current, setCurrent] = useState<InventoryItem | null>(null);
  const [draftQuantity, setDraftQuantity] = useState(1);
  const [cart, setCart] = useState<DraftLine[]>([]);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const counterparty = locations.data?.find((l) => l.id === counterpartyId) ?? null;
  const myLocation = locations.data?.find((l) => l.id === myLocationId) ?? null;

  const choices = useMemo(
    () => (locations.data ?? []).filter((l) => l.id !== myLocationId),
    [locations.data, myLocationId],
  );

  const visibleItems = useMemo(() => {
    const term = search.trim().toLowerCase();
    const pool = (items.data ?? []).filter(
      (item) =>
        item.location_id === null ||
        item.location_id === myLocationId ||
        item.location_id === counterpartyId,
    );
    if (!term) return pool;
    return pool.filter(
      (item) =>
        item.name.toLowerCase().includes(term) ||
        item.category.toLowerCase().includes(term) ||
        (item.sku ?? '').toLowerCase().includes(term),
    );
  }, [items.data, search, myLocationId, counterpartyId]);

  const grouped = useMemo(() => groupByCategory(visibleItems), [visibleItems]);

  if (!myLocationId) {
    return (
      <Screen title="Record a transfer" back="/">
        <Notice>
          Your account has no location yet, so there is nothing to record against. Ask an admin to
          assign you to a restaurant.
        </Notice>
      </Screen>
    );
  }

  const verb = transferKind === 'take' ? 'Take' : 'Give';
  const allLines: DraftLine[] = current ? [...cart, { item: current, quantity: draftQuantity }] : cart;

  async function record() {
    if (allLines.length === 0 || !counterpartyId) return;
    setBusy(true);
    setError(null);
    try {
      await api.createTransfer({
        kind: transferKind,
        counterpartyLocationId: counterpartyId,
        items: allLines.map((line) => ({ item_id: line.item.id, quantity: line.quantity })),
      });
      setStep('done');
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // ------------------------------------------------------------- step 1 --
  if (step === 'location') {
    return (
      <Screen title={`${verb} something`} back="/">
        <h2 className="page-title">{transferKind === 'take' ? 'From' : 'To'}</h2>
        <p className="page-subtitle">
          {transferKind === 'take'
            ? 'Which shop are you taking it from?'
            : 'Which shop are you giving it to?'}
        </p>
        {locations.loading && <Spinner />}
        {locations.error && <Notice>{locations.error}</Notice>}
        {locations.data && choices.length === 0 && (
          <Empty>There is only one location set up so far.</Empty>
        )}
        {choices.length > 0 && (
          <List>
            {choices.map((location) => (
              <Row
                key={location.id}
                title={location.name}
                onClick={() => {
                  setCounterpartyId(location.id);
                  setStep('item');
                }}
              />
            ))}
          </List>
        )}
      </Screen>
    );
  }

  // ------------------------------------------------------------- step 2 --
  if (step === 'item') {
    return (
      <Screen
        title={counterparty?.name ?? verb}
        back={() => {
          if (cart.length > 0) setStep('quantity');
          else setStep('location');
        }}
      >
        <h2 className="page-title">Item</h2>
        <p className="page-subtitle">
          {transferKind === 'take' ? 'From' : 'To'} {counterparty?.name}
        </p>
        <Search value={search} onChange={setSearch} placeholder="Search items" />
        {items.loading && <Spinner />}
        {items.error && <Notice>{items.error}</Notice>}
        {items.data && visibleItems.length === 0 && <Empty>Nothing matches “{search}”.</Empty>}
        {grouped.map(([category, list]) => (
          <div key={category}>
            <SectionLabel>{category}</SectionLabel>
            <List>
              {list.map((item) => (
                <Row
                  key={item.id}
                  title={item.name}
                  value={item.unit}
                  onClick={() => {
                    setCurrent(item);
                    setDraftQuantity(1);
                    setStep('quantity');
                  }}
                />
              ))}
            </List>
          </div>
        ))}
      </Screen>
    );
  }

  // ------------------------------------------------------------- step 3 --
  if (step === 'quantity') {
    return (
      <Screen
        title={counterparty?.name ?? verb}
        back={() => {
          setCurrent(null);
          setStep('item');
        }}
      >
        <h2 className="page-title">{current?.name}</h2>
        <p className="page-subtitle">
          {transferKind === 'take' ? (
            <>
              {counterparty?.name} &rarr; {myLocation?.name}
            </>
          ) : (
            <>
              {myLocation?.name} &rarr; {counterparty?.name}
            </>
          )}
        </p>

        {current && (
          <Stepper
            value={draftQuantity}
            unit={draftQuantity === 1 ? current.unit : `${current.unit}s`}
            onChange={setDraftQuantity}
          />
        )}

        {cart.length > 0 && (
          <>
            <SectionLabel>Also on this transfer</SectionLabel>
            <List>
              {cart.map((line) => (
                <Row
                  key={line.item.id}
                  title={line.item.name}
                  value={amount(line.quantity, line.item.unit)}
                  onClick={() => setCart((lines) => lines.filter((l) => l.item.id !== line.item.id))}
                  trailing={<span className="linkbtn linkbtn--danger">Remove</span>}
                />
              ))}
            </List>
          </>
        )}

        {error && (
          <div style={{ marginTop: 16 }}>
            <Notice>{error}</Notice>
          </div>
        )}

        <div className="sticky-actions stack">
          <button type="button" className="btn" onClick={record} disabled={busy || !current}>
            {busy ? 'Recording…' : 'Record transfer'}
          </button>
          <button
            type="button"
            className="btn btn--secondary"
            disabled={busy || !current}
            onClick={() => {
              if (!current) return;
              setCart((lines) => [...lines, { item: current, quantity: draftQuantity }]);
              setCurrent(null);
              setSearch('');
              setStep('item');
            }}
          >
            Add another item
          </button>
        </div>
      </Screen>
    );
  }

  // ------------------------------------------------------------- step 4 --
  const from = transferKind === 'take' ? counterparty?.name : myLocation?.name;
  const to = transferKind === 'take' ? myLocation?.name : counterparty?.name;

  return (
    <div className="screen">
      <div className="done">
        <div className="done__mark">
          <CheckMark />
        </div>
        <h1 className="done__title">Transfer recorded.</h1>
        <p className="done__detail">
          {from} &rarr; {to}
        </p>
        <p className="done__detail">
          {allLines.map((line) => `${amount(line.quantity, line.item.unit)} ${line.item.name}`).join(', ')}
        </p>
        <div className="stack" style={{ width: '100%', maxWidth: 320, marginTop: 16 }}>
          <button type="button" className="btn" onClick={() => navigate('/')}>
            Done
          </button>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={() => {
              setCart([]);
              setCurrent(null);
              setCounterpartyId(null);
              setSearch('');
              setError(null);
              setStep('location');
            }}
          >
            Record another
          </button>
        </div>
      </div>
    </div>
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
