import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { LOCATIONS, profile, renderAt } from './harness';
import type { Balance, Profile, Role, UserStatus } from '../../src/lib/types';

type UpdateUserInput = {
  userId: string;
  role?: Role;
  locationId?: string | null;
  status?: UserStatus;
};

const fetchUsers = vi.fn<() => Promise<Profile[]>>();
const updateUser = vi.fn<(input: UpdateUserInput) => Promise<void>>();
const fetchBalances = vi.fn<(locationId?: string) => Promise<Balance[]>>();

vi.mock('../../src/lib/api', () => ({
  fetchLocations: vi.fn(async () => LOCATIONS),
  fetchUsers: () => fetchUsers(),
  updateUser: (...args: Parameters<typeof updateUser>) => updateUser(...args),
  fetchInvitations: vi.fn(async () => []),
  createInvitation: vi.fn(async () => 'invite-id'),
  revokeInvitation: vi.fn(async () => undefined),
  fetchBalances: (...args: Parameters<typeof fetchBalances>) => fetchBalances(...args),
}));

const admin = profile({ id: 'user-ada', full_name: 'Ada Owner', role: 'admin', location_id: 'loc-h1' });

vi.mock('../../src/state/session', () => ({
  useSession: () => ({
    session: { user: { id: 'user-ada' } },
    profile: admin,
    loading: false,
    error: null,
    refresh: async () => {},
  }),
  useProfile: () => admin,
}));

const { default: Users } = await import('../../src/routes/admin/Users');
const { default: Balances } = await import('../../src/routes/admin/Balances');

const pendingUser = profile({
  id: 'user-john',
  full_name: 'John Smith',
  email: 'john@example.com',
  status: 'pending',
  location_id: 'loc-h2',
});

beforeEach(() => {
  fetchUsers.mockReset();
  fetchUsers.mockResolvedValue([]);
  updateUser.mockReset();
  updateUser.mockResolvedValue(undefined);
  fetchBalances.mockReset();
  fetchBalances.mockResolvedValue([]);
});

describe('pending access requests', () => {
  it('approves a request with the chosen role and location', async () => {
    fetchUsers.mockResolvedValue([pendingUser, admin]);
    const { user } = renderAt('/admin/users', '/admin/users', <Users />);

    expect(await screen.findByText('John Smith')).toBeInTheDocument();
    expect(screen.getByText('john@example.com')).toBeInTheDocument();

    // Scope to the request card so the invite form's own Role field is not
    // what gets changed.
    const card = screen.getByRole('button', { name: 'Approve' }).closest('.card')!;
    await user.selectOptions(within(card as HTMLElement).getByLabelText('Role'), 'manager');
    await user.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({
        userId: 'user-john',
        role: 'manager',
        locationId: 'loc-h2',
        status: 'active',
      }),
    );
  });

  it('rejects a request by disabling the account rather than deleting it', async () => {
    fetchUsers.mockResolvedValue([pendingUser, admin]);
    const { user } = renderAt('/admin/users', '/admin/users', <Users />);
    await user.click(await screen.findByRole('button', { name: 'Reject' }));

    await waitFor(() =>
      expect(updateUser).toHaveBeenCalledWith({ userId: 'user-john', status: 'disabled' }),
    );
  });

  it('says when nobody is waiting', async () => {
    fetchUsers.mockResolvedValue([admin]);
    renderAt('/admin/users', '/admin/users', <Users />);
    expect(await screen.findByText('Nobody is waiting.')).toBeInTheDocument();
  });

  it('marks who is using the app right now', async () => {
    fetchUsers.mockResolvedValue([
      admin,
      profile({
        id: 'user-kim',
        full_name: 'Kim Park',
        last_seen_at: new Date(Date.now() - 60_000).toISOString(),
      }),
    ]);
    renderAt('/admin/users', '/admin/users', <Users />);
    expect(await screen.findByText('Here now')).toBeInTheDocument();
  });
});

describe('balances', () => {
  it('states each pair once, in the direction that is owed', async () => {
    fetchBalances.mockResolvedValue([
      balance('loc-taco', '287 Taco Shop', 'loc-h2', 'Hibachio 2', 'item-cups32', '32 oz Cups', 'sleeve', 2),
      balance('loc-h2', 'Hibachio 2', 'loc-taco', '287 Taco Shop', 'item-cups32', '32 oz Cups', 'sleeve', -2),
      balance('loc-h2', 'Hibachio 2', 'loc-taco', '287 Taco Shop', 'item-napkins', 'Napkins', 'case', 1),
      balance('loc-taco', '287 Taco Shop', 'loc-h2', 'Hibachio 2', 'item-napkins', 'Napkins', 'case', -1),
    ]);
    renderAt('/admin/balances', '/admin/balances', <Balances />);

    expect(await screen.findByText('287 Taco Shop is up on Hibachio 2')).toBeInTheDocument();
    expect(screen.getByText('Hibachio 2 is up on 287 Taco Shop')).toBeInTheDocument();
    expect(screen.getByText('2 sleeves')).toBeInTheDocument();
    expect(screen.getByText('1 case')).toBeInTheDocument();
  });

  it('says everything is even when it is', async () => {
    renderAt('/admin/balances', '/admin/balances', <Balances />);
    expect(await screen.findByText('Everything is even.')).toBeInTheDocument();
  });
});

function balance(
  a: string,
  aName: string,
  b: string,
  bName: string,
  itemId: string,
  itemName: string,
  unit: string,
  net: number,
): Balance {
  return {
    location_a: a,
    location_a_name: aName,
    location_b: b,
    location_b_name: bName,
    item_id: itemId,
    item_name: itemName,
    unit,
    net_quantity: net,
  };
}
