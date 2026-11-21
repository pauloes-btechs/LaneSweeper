import { useState } from 'react';
import { RequireStaff } from '../components/RequireStaff';
import { api } from '../lib/api';

interface Answer { answer: string; tools: string[]; citations: string[]; provenance: { mode: string; model: string } }
interface Msg { role: 'user' | 'bot'; text: string; meta?: Answer }

const SUGGEST = ['Show the 10 unresolved glass incidents on First Avenue', 'Why did you rank ticket LK-101 as high priority?',
  'Which robot can handle the most urgent approved lane-cell cleanup?', 'What was actually verified after yesterday’s sweep?',
  'What conditions would force you to stop the robots?', 'Draft a rider update explaining why ticket LK-101 is delayed'];

function Body() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  async function ask(text: string) {
    if (!text.trim()) return;
    setMsgs((m) => [...m, { role: 'user', text }]);
    setQ(''); setBusy(true);
    try {
      const r = await api.post<Answer>('/v1/copilot', { question: text, session: 'console' });
      setMsgs((m) => [...m, { role: 'bot', text: r.answer, meta: r }]);
    } catch (e) { setMsgs((m) => [...m, { role: 'bot', text: `Refused: ${(e as Error).message}` }]); } finally { setBusy(false); }
  }
  return (
    <div className="stack" style={{ gap: 16 }}>
      <h1>Knowledge & copilot</h1>
      <div className="callout info small">The copilot answers from your city’s tickets, simulated fleet data and approved SOPs, and cites them. It cannot dispatch robots or assign crews; it can only draft and request approval.
        Retrieved documents are treated as untrusted text.</div>
      <div className="grid g-side">
        <div className="card stack">
          <div className="chat" aria-live="polite">{msgs.length === 0 && <p className="muted">Ask about tickets, priorities, robots or procedures.</p>}
            {msgs.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.text}
              {m.meta && <div className="small faint" style={{ marginTop: 6 }}>tools: {m.meta.tools?.join(', ') || 'none'} · {m.meta.provenance?.mode} ({m.meta.provenance?.model})</div>}</div>)}
            {busy && <div className="msg bot muted">Working…</div>}</div>
          <form className="row" onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
            <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask the operations copilot" aria-label="Question" maxLength={1000} />
            <button className="primary" disabled={busy}>Ask</button></form>
        </div>
        <div className="card"><h3>Try</h3><div className="stack">{SUGGEST.map((s) => <button key={s} onClick={() => void ask(s)} disabled={busy} style={{ textAlign: 'left' }}>{s}</button>)}</div></div>
      </div>
    </div>
  );
}

export default function Knowledge() { return <RequireStaff>{() => <Body />}</RequireStaff>; }
