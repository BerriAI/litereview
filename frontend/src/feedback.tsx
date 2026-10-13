import { useEffect, useState } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { api, send } from "./api";
import { LoadingState } from "./request-state";
import "./feedback.css";

type Rating = "up" | "down";
type Feedback = { rating: Rating; note: string; updated_at: number };
type Entry = Feedback & { id: string; job_id: string; run_id: string; reporter: string; repo: string;
  pr: number; agent_name: string; model: string };

export function ReviewFeedback({ jobId, initial }: { jobId: string; initial?: Feedback | null }) {
  const [saved, setSaved] = useState(initial);
  const [note, setNote] = useState(initial?.note || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const save = async (rating: Rating) => {
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await api(`/jobs/${jobId}/feedback`, send("PUT", { rating, note }));
      setSaved(result); setNote(result.note); setMessage("Feedback saved. Thank you.");
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <section className="review-feedback" aria-label="Review feedback" id={`feedback-${jobId}`}>
    <div className="feedback-prompt">
      <strong>Was this review helpful?</strong>
      <div className="feedback-votes">
        <button type="button" aria-pressed={saved?.rating === "up"} disabled={busy} onClick={() => save("up")}>
          <ThumbsUp size={16} /> Helpful
        </button>
        <button type="button" aria-pressed={saved?.rating === "down"} disabled={busy} onClick={() => save("down")}>
          <ThumbsDown size={16} /> Needs work
        </button>
      </div>
    </div>
    <p className="meta">Your rating, name, and optional note are visible to workspace admins.</p>
    {saved && <form onSubmit={e => { e.preventDefault(); void save(saved.rating); }}>
      <label htmlFor={`feedback-note-${jobId}`}>{saved.rating === "down" ? "What should improve?" : "What worked well?"} <span className="meta">Optional</span></label>
      <textarea id={`feedback-note-${jobId}`} value={note} disabled={busy} onChange={e => setNote(e.target.value)} maxLength={4000}
        placeholder="For example: it missed a bug, flagged working code, or was hard to understand." rows={3} />
      <button disabled={busy || note === saved.note}>{busy ? "Saving…" : "Save note"}</button>
    </form>}
    {busy && <p role="status" className="meta">Saving feedback…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {message && !busy && <p role="status" className="meta">{message}</p>}
  </section>;
}

export function FeedbackPage({ agents }: { agents: { id: string; name: string }[] }) {
  const [rating, setRating] = useState("");
  const [agentId, setAgentId] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{ items: Entry[]; total: number; has_more: boolean } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError("");
    const params = new URLSearchParams({ agent_id: agentId, offset: String(offset), limit: "30" });
    if (rating) params.set("rating", rating);
    api(`/feedback?${params}`, { signal: controller.signal, label: "Loading feedback…" })
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [rating, agentId, offset, revision]);
  return <section aria-label="Reviewer feedback">
    <div className="feedback-filters">
      <label>Rating<select value={rating} onChange={e => { setRating(e.target.value); setOffset(0); }}>
        <option value="">All feedback</option><option value="down">Needs work</option><option value="up">Helpful</option>
      </select></label>
      <label>Reviewer<select value={agentId} onChange={e => { setAgentId(e.target.value); setOffset(0); }}>
        <option value="">All reviewers</option>{agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select></label>
      <button onClick={() => setRevision(v => v + 1)}>Refresh</button>
    </div>
    {!data ? <LoadingState label="Loading feedback…" error={error} retry={() => setRevision(v => v + 1)} /> : <>
      <p className="meta">{data.total} {data.total === 1 ? "rating" : "ratings"}</p>
      {!data.items.length ? <div className="empty"><h3>{rating || agentId ? "No matching feedback" : "No feedback yet"}</h3>
        <p>People can rate each finished reviewer on its run page.</p></div> : <div className="feedback-list">
        {data.items.map(item => <article className="feedback-entry" key={item.id}>
          <div className="feedback-entry-heading"><strong>{item.agent_name}</strong>
            <span className={`feedback-rating ${item.rating}`}>{item.rating === "up" ? <ThumbsUp size={15} /> : <ThumbsDown size={15} />}
              {item.rating === "up" ? "Helpful" : "Needs work"}</span>
          </div>
          <a href={`/runs/${item.run_id}`}>{item.repo} #{item.pr} · View review</a>
          {item.note ? <p className="feedback-note">{item.note}</p> : <p className="meta">No note added.</p>}
          <p className="meta">{item.reporter} · {new Date(item.updated_at * 1000).toLocaleString()} · {item.model}</p>
        </article>)}
      </div>}
      {(offset > 0 || data.has_more) && <div className="feedback-pagination">
        <button disabled={offset === 0} onClick={() => setOffset(v => Math.max(0, v - 30))}>Previous</button>
        <button disabled={!data.has_more} onClick={() => setOffset(v => v + 30)}>Next</button>
      </div>}
    </>}
  </section>;
}
