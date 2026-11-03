import React from 'react';
import { Activity, ArrowRight, CheckCircle2, ClipboardList, Clock3, MapPin, TrendingUp } from 'lucide-react';

function Stat({ label, value, icon: Icon }) {
  return <div className="metric-card"><div className="metric-top"><span>{label}</span><div className="metric-icon blue"><Icon size={17}/></div></div><div className="metric-value">{value}</div></div>;
}

function ReportRows({ reports, open }) {
  return <div className="table-wrap"><table><thead><tr><th>REPORT</th><th>LOCATION</th><th>COMMUNITY SUPPORT</th><th>ASSIGNED WORKER</th><th>STATUS</th></tr></thead><tbody>
    {reports.map(report => <tr key={report.id} onClick={() => open(report)}>
      <td><b>{report.title}</b><small>{report.id} · {report.date}</small></td>
      <td><MapPin size={13}/>{report.area}</td>
      <td><TrendingUp size={13}/> {report.upvotes || 0}</td>
      <td>{report.assignedStaffName || 'Unassigned'}</td>
      <td><span className={`status ${report.status.toLowerCase().replaceAll(' ', '-')}`}>{report.status}</span></td>
    </tr>)}
  </tbody></table></div>;
}

export function AdminDashboard({ reports, open, go }) {
  const active = reports.filter(report => !['Resolved', 'Closed'].includes(report.status));
  const unassigned = reports.filter(report => !report.assignedStaff && !['Resolved', 'Closed'].includes(report.status));
  const inProgress = reports.filter(report => report.status === 'In progress');
  const resolved = reports.filter(report => ['Resolved', 'Closed'].includes(report.status));
  return <>
    <div className="metrics-grid admin-metrics">
      <Stat label="All reports" value={reports.length} icon={ClipboardList}/>
      <Stat label="Awaiting assignment" value={unassigned.length} icon={Clock3}/>
      <Stat label="In progress" value={inProgress.length} icon={Activity}/>
      <Stat label="Resolved" value={resolved.length} icon={CheckCircle2}/>
    </div>
    <section className="panel table-panel">
      <div className="panel-head"><div><h3>Highest-priority active reports</h3><p>{active.length} active reports · Priority reflects issue severity and community upvotes.</p></div>
        <button className="text-link" onClick={() => go('All reports')}>View all reports <ArrowRight size={14}/></button>
      </div>
      {reports.length ? <ReportRows reports={reports.slice(0, 8)} open={open}/> : <div className="empty-state"><b>No reports in MongoDB yet</b><span>New submissions will appear here.</span></div>}
    </section>
  </>;
}

export function WorkerDashboard({ reports, open }) {
  const inProgress = reports.filter(report => report.status === 'In progress').length;
  const completed = reports.filter(report => report.status === 'Resolved').length;
  return <>
    <div className="metrics-grid admin-metrics">
      <Stat label="Assigned to me" value={reports.length} icon={ClipboardList}/>
      <Stat label="In progress" value={inProgress} icon={Activity}/>
      <Stat label="Completed" value={completed} icon={CheckCircle2}/>
      <Stat label="Needs action" value={reports.filter(report => report.status === 'Assigned').length} icon={Clock3}/>
    </div>
    <section className="panel table-panel">
      <div className="panel-head"><div><h3>My assigned work</h3><p>Only reports assigned to your account are shown.</p></div></div>
      {reports.length ? <ReportRows reports={reports} open={open}/> : <div className="empty-state"><b>No reports assigned</b><span>Your administrator will assign work to your department.</span></div>}
    </section>
  </>;
}
