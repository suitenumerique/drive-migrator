import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ResanaConnectSection } from '@/components/ResanaConnect/ResanaConnectSection';

const mockFetch = jest.fn();
global.fetch = mockFetch;

beforeEach(() => {
  mockFetch.mockReset();
});

const renderComponent = (onConnected = jest.fn()) =>
  render(<ResanaConnectSection onConnected={onConnected} />);

describe('ResanaConnectSection', () => {
  it('renders email and password fields', () => {
    renderComponent();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/mot de passe/i)).toBeInTheDocument();
  });

  it('renders a submit button', () => {
    renderComponent();
    expect(
      screen.getByRole('button', { name: /connecter/i }),
    ).toBeInTheDocument();
  });

  it('calls /resana/auth/connect on submit and invokes onConnected', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: () => Promise.resolve({}),
    });

    const onConnected = jest.fn();
    renderComponent(onConnected);

    await userEvent.type(screen.getByLabelText(/email/i), 'u@example.com');
    await userEvent.type(screen.getByLabelText(/mot de passe/i), 's3cr3t');
    await userEvent.click(screen.getByRole('button', { name: /connecter/i }));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('resana/auth/connect'),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ email: 'u@example.com', password: 's3cr3t' }),
        }),
      );
    });
    expect(onConnected).toHaveBeenCalled();
  });

  it('shows an error message on failed connect', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ detail: 'error' }),
    });

    renderComponent();

    await userEvent.type(screen.getByLabelText(/email/i), 'u@example.com');
    await userEvent.type(screen.getByLabelText(/mot de passe/i), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /connecter/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('does not invoke onConnected on failure', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: () => Promise.resolve({}),
    });

    const onConnected = jest.fn();
    renderComponent(onConnected);

    await userEvent.type(screen.getByLabelText(/email/i), 'u@example.com');
    await userEvent.type(screen.getByLabelText(/mot de passe/i), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: /connecter/i }));

    await waitFor(() => {
      expect(onConnected).not.toHaveBeenCalled();
    });
  });
});
