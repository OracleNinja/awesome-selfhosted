import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { LOCATIONS, profile, renderAt, transfer } from './harness';
import type { Transfer } from '../../src/lib/types';

const fetchAwaitingConfirmation = vi.fn<(locationId: string) => Promise<Transfer[]>>();
const confirmTransfer = vi.fn<(id: string, note?: string | null) => Promise<void>>();

vi.mock('../../src/lib/api', () => ({
  fetchLocations: vi.fn(async () => LOCATIONS),
  fetchAwaitingConfirmation: (...args: Parameters<typeof fetchAwaitingConfirmation>) =>
    fetchAwaitingConfirmation(...args),
  confirmTransfer: (...args: Parameters<typeof confirmTransfer>) => confirmTransfer(...args),
  signOut: vi.fn(),
}));

let currentProfile = profile();

vi.mock('../../src/state/session', () => ({
  useSession: () => ({
    session: { user: { id: currentProfile.id } },
    profile: currentProfile,
    loading: false,
    error: null,
    refresh: async () => {},
  }),
  useProfile: () => currentProfile,
}));

const { default: Home } = await import('../../src/routes/Home');
const { default: Confirm } = await import('../../src/routes/Confirm');

beforeEach(() => {
  currentProfile = profile();
  fetchAwaitingConfirmation.mockReset();
  fetchAwaitingConfirmation.mockResolvedValue([]);
  confirmTransfer.mockReset();
  confirmTransfer.mockResolvedValue(undefined);
});

describe('home', () => {
  it('greets by first name and names the location being worked at', async () => {
    renderAt('/', '/', <Home />);
    expect(await screen.findByText('Hi John')).toBeInTheDocument();
    expect(await screen.findByText('Hibachio 2')).toBeInTheDocument();
  });

  it('offers the four actions in order', async () => {
    renderAt('/', '/', <Home />);
    const actions = await screen.findAllByRole('button');
    const labels = actions.map((b) => b.textContent ?? '');
    expect(labels[0]).toMatch(/Take something/);
    expect(labels[1]).toMatch(/Give something/);
    expect(labels[2]).toMatch(/Receive \/ confirm/);
    expect(labels[3]).toMatch(/History/);
  });

  it('badges how many transfers are waiting', async () => {
    fetchAwaitingConfirmation.mockResolvedValue([transfer(), transfer({ id: 'transfer-2' })]);
    renderAt('/', '/', <Home />);
    expect(await screen.findByText('2 waiting')).toBeInTheDocument();
  });

  it('shows the admin entry only to admins', async () => {
    renderAt('/', '/', <Home />);
    await screen.findByText('Hi John');
    expect(screen.queryByText('Users, locations, catalog, activity')).not.toBeInTheDocument();

    currentProfile = profile({ role: 'admin', full_name: 'Ada Owner' });
    renderAt('/', '/', <Home />);
    expect(await screen.findByText('Users, locations, catalog, activity')).toBeInTheDocument();
  });
});

describe('receive and confirm', () => {
  it('separates what is coming in from what was taken off our shelves', async () => {
    fetchAwaitingConfirmation.mockResolvedValue([
      transfer(),
      transfer({
        id: 'transfer-2',
        kind: 'take',
        from_location_id: 'loc-h2',
        from_location_name: 'Hibachio 2',
        to_location_id: 'loc-h3',
        to_location_name: 'Hibachio 3',
        confirming_location_id: 'loc-h2',
        recorded_by_name: 'Sam Outside',
      }),
    ]);
    renderAt('/confirm', '/confirm', <Confirm />);

    expect(await screen.findByText('Incoming')).toBeInTheDocument();
    expect(screen.getByText('Taken from us')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /confirm received/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /yes, that left here/i })).toBeInTheDocument();
    expect(screen.getByText(/Sent by Maria Lopez/)).toBeInTheDocument();
  });

  it('confirms a transfer and reloads the queue', async () => {
    fetchAwaitingConfirmation.mockResolvedValue([transfer()]);
    const { user } = renderAt('/confirm', '/confirm', <Confirm />);
    const button = await screen.findByRole('button', { name: /confirm received/i });

    fetchAwaitingConfirmation.mockResolvedValue([]);
    await user.click(button);

    await waitFor(() => expect(confirmTransfer).toHaveBeenCalledWith('transfer-1'));
    expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument();
  });

  it('surfaces a refusal from the database', async () => {
    fetchAwaitingConfirmation.mockResolvedValue([transfer()]);
    confirmTransfer.mockRejectedValueOnce(new Error('That transfer is already confirmed'));
    const { user } = renderAt('/confirm', '/confirm', <Confirm />);
    await user.click(await screen.findByRole('button', { name: /confirm received/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('already confirmed');
  });

  it('says when nothing is waiting', async () => {
    renderAt('/confirm', '/confirm', <Confirm />);
    expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument();
  });
});
