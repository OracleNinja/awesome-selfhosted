import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { render } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import type { InventoryItem, Location, Profile, Transfer } from '../../src/lib/types';

export const LOCATIONS: Location[] = [
  { id: 'loc-h1', name: 'Hibachio 1', active: true, created_at: '2026-01-01T00:00:00Z' },
  { id: 'loc-h2', name: 'Hibachio 2', active: true, created_at: '2026-01-01T00:00:00Z' },
  { id: 'loc-h3', name: 'Hibachio 3', active: true, created_at: '2026-01-01T00:00:00Z' },
  { id: 'loc-taco', name: '287 Taco Shop', active: true, created_at: '2026-01-01T00:00:00Z' },
];

export const ITEMS: InventoryItem[] = [
  item('item-cups32', '32 oz Cups', 'Cups', 'sleeve'),
  item('item-lids32', '32 oz Lids', 'Lids', 'sleeve'),
  item('item-cups16', '16 oz Cups', 'Cups', 'sleeve'),
  item('item-napkins', 'Napkins', 'Paper', 'case'),
  item('item-gloves', 'Gloves', 'Supplies', 'box'),
];

function item(id: string, name: string, category: string, unit: string): InventoryItem {
  return { id, name, category, unit, sku: null, active: true, notes: null, location_id: null };
}

export function profile(overrides: Partial<Profile> = {}): Profile {
  return {
    id: 'user-john',
    email: 'john@example.com',
    full_name: 'John Smith',
    role: 'employee',
    status: 'active',
    location_id: 'loc-h2',
    last_seen_at: null,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

export function transfer(overrides: Partial<Transfer> = {}): Transfer {
  return {
    id: 'transfer-1',
    kind: 'give',
    from_location_id: 'loc-taco',
    from_location_name: '287 Taco Shop',
    to_location_id: 'loc-h2',
    to_location_name: 'Hibachio 2',
    recorded_by: 'user-maria',
    recorded_by_name: 'Maria Lopez',
    recorded_at: new Date().toISOString(),
    note: null,
    confirming_location_id: 'loc-h2',
    confirmed: false,
    confirmed_at: null,
    confirmed_by_name: null,
    voided: false,
    lines: [
      {
        id: 'line-1',
        transfer_id: 'transfer-1',
        item_id: 'item-cups32',
        item_name: '32 oz Cups',
        item_category: 'Cups',
        unit: 'sleeve',
        original_quantity: 2,
        effective_quantity: 2,
        adjusted: false,
        voided: false,
      },
    ],
    ...overrides,
  };
}

/** Renders one screen at a route, with a router around it. */
export function renderAt(path: string, pattern: string, element: ReactElement) {
  const user = userEvent.setup();
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path={pattern} element={element} />
        <Route path="*" element={<div>elsewhere</div>} />
      </Routes>
    </MemoryRouter>,
  );
  return { ...view, user };
}
