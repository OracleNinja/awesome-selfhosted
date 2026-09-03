import { beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { ITEMS, LOCATIONS, profile, renderAt } from './harness';
import type { TransferKind } from '../../src/lib/types';

type CreateInput = {
  kind: TransferKind;
  counterpartyLocationId: string;
  items: Array<{ item_id: string; quantity: number }>;
  note?: string | null;
  actingLocationId?: string | null;
};

const createTransfer = vi.fn<(input: CreateInput) => Promise<string>>();

vi.mock('../../src/lib/api', () => ({
  fetchLocations: vi.fn(async () => LOCATIONS),
  fetchItems: vi.fn(async () => ITEMS),
  createTransfer: (...args: Parameters<typeof createTransfer>) => createTransfer(...args),
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

const { default: RecordTransfer } = await import('../../src/routes/RecordTransfer');

beforeEach(() => {
  createTransfer.mockReset();
  createTransfer.mockResolvedValue('new-transfer-id');
  currentProfile = profile();
});

describe('the take flow, end to end', () => {
  it('records 2 sleeves of 32 oz cups from 287 Taco Shop in four taps', async () => {
    const { user } = renderAt('/record/take', '/record/:kind', <RecordTransfer />);

    // Step one: which shop are we taking from? Our own is not on the list.
    expect(await screen.findByRole('button', { name: /287 Taco Shop/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Hibachio 2/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /287 Taco Shop/ }));

    // Step two: which item?
    await user.click(await screen.findByRole('button', { name: /32 oz Cups/ }));

    // Step three: how many? Starts at one.
    expect(screen.getByText('1')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /increase quantity/i }));
    expect(screen.getByText('2')).toBeInTheDocument();

    // Step four: record it.
    await user.click(screen.getByRole('button', { name: /record transfer/i }));

    await waitFor(() => expect(createTransfer).toHaveBeenCalledTimes(1));
    expect(createTransfer).toHaveBeenCalledWith({
      kind: 'take',
      counterpartyLocationId: 'loc-taco',
      items: [{ item_id: 'item-cups32', quantity: 2 }],
    });

    expect(await screen.findByText('Transfer recorded.')).toBeInTheDocument();
    expect(screen.getByText('287 Taco Shop → Hibachio 2')).toBeInTheDocument();
  });

  it('will not go below one', async () => {
    const { user } = renderAt('/record/take', '/record/:kind', <RecordTransfer />);
    await user.click(await screen.findByRole('button', { name: /287 Taco Shop/ }));
    await user.click(await screen.findByRole('button', { name: /Napkins/ }));
    expect(screen.getByRole('button', { name: /decrease quantity/i })).toBeDisabled();
  });

  it('searches the catalog', async () => {
    const { user } = renderAt('/record/take', '/record/:kind', <RecordTransfer />);
    await user.click(await screen.findByRole('button', { name: /287 Taco Shop/ }));
    await screen.findByRole('button', { name: /32 oz Cups/ });

    await user.type(screen.getByRole('searchbox', { name: /search items/i }), 'napk');
    expect(screen.getByRole('button', { name: /Napkins/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /32 oz Cups/ })).not.toBeInTheDocument();
  });

  it('puts several items on one transfer', async () => {
    const { user } = renderAt('/record/take', '/record/:kind', <RecordTransfer />);
    await user.click(await screen.findByRole('button', { name: /287 Taco Shop/ }));
    await user.click(await screen.findByRole('button', { name: /32 oz Cups/ }));
    await user.click(screen.getByRole('button', { name: /increase quantity/i }));
    await user.click(screen.getByRole('button', { name: /add another item/i }));

    await user.click(await screen.findByRole('button', { name: /Gloves/ }));
    await user.click(screen.getByRole('button', { name: /record transfer/i }));

    await waitFor(() => expect(createTransfer).toHaveBeenCalledTimes(1));
    expect(createTransfer.mock.calls[0]![0]).toMatchObject({
      items: [
        { item_id: 'item-cups32', quantity: 2 },
        { item_id: 'item-gloves', quantity: 1 },
      ],
    });
  });

  it('sends a give in the other direction', async () => {
    const { user } = renderAt('/record/give', '/record/:kind', <RecordTransfer />);
    expect(await screen.findByText('To')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Hibachio 3/ }));
    await user.click(await screen.findByRole('button', { name: /Gloves/ }));
    await user.click(screen.getByRole('button', { name: /record transfer/i }));

    await waitFor(() => expect(createTransfer).toHaveBeenCalledTimes(1));
    expect(createTransfer).toHaveBeenCalledWith({
      kind: 'give',
      counterpartyLocationId: 'loc-h3',
      items: [{ item_id: 'item-gloves', quantity: 1 }],
    });
    expect(await screen.findByText('Hibachio 2 → Hibachio 3')).toBeInTheDocument();
  });

  it('shows the reason when the database refuses', async () => {
    createTransfer.mockRejectedValueOnce(new Error('Unknown or inactive item'));
    const { user } = renderAt('/record/take', '/record/:kind', <RecordTransfer />);
    await user.click(await screen.findByRole('button', { name: /287 Taco Shop/ }));
    await user.click(await screen.findByRole('button', { name: /32 oz Cups/ }));
    await user.click(screen.getByRole('button', { name: /record transfer/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Unknown or inactive item');
    expect(screen.queryByText('Transfer recorded.')).not.toBeInTheDocument();
  });

  it('says so when the account has no location', async () => {
    currentProfile = profile({ location_id: null });
    renderAt('/record/take', '/record/:kind', <RecordTransfer />);
    expect(await screen.findByRole('alert')).toHaveTextContent(/no location yet/i);
  });
});
