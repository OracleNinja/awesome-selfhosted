import { useState, type FormEvent } from 'react';
import { Field, Notice } from '../ui';
import * as api from '../lib/api';

type Mode = 'signin' | 'request';

export default function SignIn() {
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fullName, setFullName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === 'signin') {
        await api.signIn(email, password);
      } else {
        const { needsEmailConfirmation } = await api.signUp({
          email,
          password,
          fullName,
          requestedLocationId: null,
        });
        if (needsEmailConfirmation) setSent(true);
      }
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <div className="center-page">
        <div className="auth">
          <h1 className="auth__brand">Check your email</h1>
          <p className="auth__tagline">
            Open the link we sent to {email} to finish setting up your account. A manager still
            has to approve you before you can record anything.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="center-page">
      <form className="auth" onSubmit={submit}>
        <div>
          <h1 className="auth__brand">Inventory Exchange</h1>
          <p className="auth__tagline">Keep track of what moves between the shops.</p>
        </div>

        <div className="segmented" role="group" aria-label="Sign in or request access">
          <button
            type="button"
            className="segmented__btn"
            aria-pressed={mode === 'signin'}
            onClick={() => setMode('signin')}
          >
            Sign in
          </button>
          <button
            type="button"
            className="segmented__btn"
            aria-pressed={mode === 'request'}
            onClick={() => setMode('request')}
          >
            Request access
          </button>
        </div>

        <div className="card">
          {mode === 'request' && (
            <Field
              label="Your name"
              value={fullName}
              autoComplete="name"
              required
              onChange={(e) => setFullName(e.target.value)}
            />
          )}
          <Field
            label="Email"
            type="email"
            inputMode="email"
            autoComplete="email"
            autoCapitalize="off"
            value={email}
            required
            onChange={(e) => setEmail(e.target.value)}
          />
          <Field
            label="Password"
            type="password"
            autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
            value={password}
            required
            minLength={8}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {error && <Notice>{error}</Notice>}

        <button type="submit" className="btn" disabled={busy}>
          {busy ? 'One moment…' : mode === 'signin' ? 'Sign in' : 'Request access'}
        </button>

        {mode === 'request' && (
          <p className="meta">
            Requesting access does not give you access. Someone at your restaurant approves it.
          </p>
        )}
      </form>
    </div>
  );
}
