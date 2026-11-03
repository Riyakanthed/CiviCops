import React, { useEffect, useState } from 'react';
import { Check, Copy, UserPlus, Users } from 'lucide-react';

const departments = ['Public Works', 'Sanitation', 'Water Management', 'Electrical Services', 'Drainage'];

function StaffRow({ account, onSaveDepartment }) {
  const [department, setDepartment] = useState(account.departments?.[0] || departments[0]);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDepartment(account.departments?.[0] || departments[0]), [account]);

  const save = async () => {
    setSaving(true);
    try {
      await onSaveDepartment(account.user_id, department);
    } finally {
      setSaving(false);
    }
  };
  const changed = account.role === 'Worker' && department !== (account.departments?.[0] || '');

  return <tr>
    <td><b>{account.name}</b><small>{account.email}</small></td>
    <td>{account.role}</td>
    <td>{account.role === 'Worker' ? <select aria-label={`Department for ${account.email}`} value={department} onChange={event => setDepartment(event.target.value)}>
      {departments.map(item => <option key={item}>{item}</option>)}
    </select> : <span>—</span>}</td>
    <td>{account.role === 'Worker' ? <button className="secondary-btn" disabled={!changed || saving} onClick={save}><Check size={14}/>{saving ? 'Saving…' : 'Save department'}</button> : <span>Domain assigned</span>}</td>
  </tr>;
}

export default function StaffView({ users, onSaveDepartment, onCreateInvite }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('Worker');
  const [department, setDepartment] = useState(departments[0]);
  const [inviteCode, setInviteCode] = useState('');
  const [inviteError, setInviteError] = useState('');
  const [creating, setCreating] = useState(false);

  const createInvite = async event => {
    event.preventDefault();
    setInviteError('');
    setInviteCode('');
    setCreating(true);
    try {
      const invitation = await onCreateInvite({
        email,
        role,
        department: role === 'Worker' ? department : null,
      });
      setInviteCode(invitation.code);
    } catch (error) {
      setInviteError(error.message);
    } finally {
      setCreating(false);
    }
  };

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(inviteCode);
    } catch (error) {
      setInviteError(`Could not copy invitation code: ${error.message}`);
    }
  };

  return <>
    <section className="panel full-panel staff-panel">
      <div className="panel-head"><div><h3><Users size={18}/> Staff and account access</h3><p>Roles come from exact email-domain matches. Worker departments can be managed here.</p></div></div>
      {users.length ? <div className="table-wrap"><table className="staff-table"><thead><tr><th>ACCOUNT</th><th>DOMAIN ROLE</th><th>DEPARTMENT</th><th>ACCESS</th></tr></thead><tbody>
        {users.map(account => <StaffRow key={account.user_id} account={account} onSaveDepartment={onSaveDepartment}/>)}
      </tbody></table></div> : <div className="empty-state"><Users size={22}/><b>No accounts yet</b><span>Users appear here after registering.</span></div>}
    </section>
    <section className="panel full-panel staff-panel">
      <div className="panel-head"><div><h3><UserPlus size={18}/> Create account invitation</h3><p>Invitations are restricted to the matching role domain and expire after 72 hours. Share the code only with the intended person.</p></div></div>
      <form className="invite-form" onSubmit={createInvite}>
        <label>Email address<input autoComplete="off" onChange={event => setEmail(event.target.value)} required type="email" value={email}/></label>
        <label>Domain role<select value={role} onChange={event => setRole(event.target.value)}><option>Worker</option><option>Admin</option><option>Citizen</option></select></label>
        {role === 'Worker' && <label>Department<select value={department} onChange={event => setDepartment(event.target.value)}>{departments.map(item => <option key={item}>{item}</option>)}</select></label>}
        <button className="primary-btn" disabled={creating} type="submit"><UserPlus size={15}/>{creating ? 'Creating…' : 'Create one-time invitation'}</button>
      </form>
      {inviteError && <p className="auth-error" role="alert">{inviteError}</p>}
      {inviteCode && <div className="invite-result"><div><b>Copy this code now</b><code>{inviteCode}</code><span>It is shown only once. Give it to the invited user.</span></div><button className="secondary-btn" onClick={copyInvite} type="button"><Copy size={14}/>Copy code</button></div>}
    </section>
  </>;
}
