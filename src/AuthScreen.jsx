import React, { useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import './auth.css';

export default function AuthScreen({ onAuthenticate, checking, error }) {
  const [mode, setMode] = useState('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [accessCode, setAccessCode] = useState('');

  const submit = event => {
    event.preventDefault();
    onAuthenticate({ mode, name, email, password, accessCode });
  };

  return <main className="auth-page">
    <section className="auth-card">
      <div className="auth-brand-mark"><ShieldCheck size={25}/></div>
      <h1>Welcome to CiviCops</h1>
      <p className="auth-subtitle">{mode === 'login' ? 'Sign in to report issues and follow your city’s response.' : 'Create your CiviCops account.'}</p>
      <form className="auth-form" onSubmit={submit}>
        {mode === 'register' && <input autoComplete="name" maxLength={100} onChange={event => setName(event.target.value)} placeholder="Full name" required value={name}/>}
        <input autoComplete="username" maxLength={254} onChange={event => setEmail(event.target.value)} placeholder="Email address" required type="email" value={email}/>
        <input autoComplete={mode === 'login' ? 'current-password' : 'new-password'} maxLength={128} minLength={mode === 'register' ? 10 : undefined} onChange={event => setPassword(event.target.value)} placeholder={mode === 'register' ? 'Password (at least 10 characters)' : 'Password'} required type="password" value={password}/>
        {mode === 'register' && <input autoComplete="off" maxLength={256} onChange={event => setAccessCode(event.target.value)} placeholder="Invitation / first-admin code (if required)" value={accessCode}/>}
        {error && <p className="auth-error" role="alert">{error}</p>}
        <button className="auth-submit" disabled={checking} type="submit">{checking ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
      </form>
      <button className="auth-switch" disabled={checking} onClick={() => setMode(mode === 'login' ? 'register' : 'login')} type="button">
        {mode === 'login' ? 'New here? Create an account' : 'Already registered? Sign in'}
      </button>
      <p className="auth-footnote">Your role is assigned from your exact email domain. Staff accounts require an administrator invitation.</p>
    </section>
  </main>;
}
