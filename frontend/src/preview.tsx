import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowUpRight, Loader2 } from "lucide-react";
import { api } from "./api";
import { ReviewMarkdown } from "./review-result";
import { LoadingState } from "./request-state";
import "./preview.css";

const finished = (status: string) => ["completed", "failed", "timed_out", "superseded"].includes(status);

export function ReviewMedia({ artifact }: { artifact: { id: string; name: string; mime?: string } }) {
  const src = `/api/artifacts/${encodeURIComponent(artifact.id)}`;
  return <figure className="preview-media">
    {artifact.mime === "video/mp4" ? <video controls preload="metadata" playsInline aria-label={artifact.name} src={src} />
      : <a href={src} target="_blank" rel="noreferrer"><img loading="lazy" src={src} alt={artifact.name} /></a>}
    <figcaption>{artifact.name} · <a href={src} target="_blank" rel="noreferrer">Open original</a></figcaption>
  </figure>;
}

export function PreviewPage({ sessionId }: { sessionId: string }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const job = new URLSearchParams(location.search).get("job");
  useEffect(() => {
    let alive = true, loaded = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setData(null);
    const load = async () => {
      if (document.visibilityState !== "visible") { timer = setTimeout(load, 4000); return; }
      let delay = 4000;
      try {
        const next = await api(`/previews/${encodeURIComponent(sessionId)}${job ? `?job=${encodeURIComponent(job)}` : ""}`,
          { signal: controller.signal, background: loaded });
        if (alive) {
          setData(next); setError(""); loaded = true;
          document.title = `${next.preview?.title || "PR preview"} · LiteReview`;
          if (finished(next.status)) delay = 30000;
        }
      } catch (e) { if (alive) setError((e as Error).message); }
      finally { if (alive) timer = setTimeout(load, delay); }
    };
    void load();
    return () => { alive = false; controller.abort(); clearTimeout(timer); };
  }, [sessionId, job, revision]);
  const retry = () => setRevision(n => n + 1);
  if (!data) return <main className="preview-page"><a href="/runs"><ArrowLeft size={14} /> Runs</a><LoadingState label="Loading preview…" error={error} retry={retry} /></main>;
  const preview = data.preview;
  return <main className="preview-page">
    <nav className="preview-top"><a href="/runs"><ArrowLeft size={14} /> LiteReview</a><a href={`https://github.com/${data.repo}/pull/${data.pr}`} target="_blank" rel="noreferrer">{data.repo} #{data.pr} <ArrowUpRight size={14} /></a></nav>
    {error && <p role="alert" className="error">{error} <button onClick={retry}>Retry</button></p>}
    {data.stale === true && <p className="preview-notice" role="status">New commits are available. This preview covers <code>{data.head.slice(0, 12)}</code>.</p>}
    {data.stale === null && <p className="preview-notice">Couldn’t check the latest commit. This preview covers <code>{data.head.slice(0, 12)}</code>.</p>}
    {!data.is_latest && <p className="preview-notice">You’re viewing an earlier preview. <a href={`/previews/${sessionId}`}>Open latest</a></p>}
    <header className="preview-heading"><p className="preview-label">PR preview · {data.agent}</p><h1>{preview?.title || `${data.repo} #${data.pr}`}</h1>
      {preview && <ReviewMarkdown>{preview.summary}</ReviewMarkdown>}
      <p className="preview-meta"><a href={`https://github.com/${data.repo}/commit/${data.head}`} target="_blank" rel="noreferrer"><code>{data.head.slice(0, 12)}</code></a> · <a href={`/runs/${data.run_id}`}>Run details</a></p>
    </header>
    {!finished(data.status) && <section className="preview-progress" aria-label="Preview progress" role="status"><p><Loader2 className="spin" size={16} /> {data.status === "queued" ? "Waiting to start…" : "Building your preview…"}</p>
      <ul>{data.branches.map((b: any, i: number) => <li key={i}><span>{b.name || `Branch ${i + 1}`}</span><span>{b.skipped ? "Skipped" : b.status}</span></li>)}</ul></section>}
    {finished(data.status) && (data.status !== "completed" || data.limitations.length > 0) && <section className="preview-notice" role="status"><h2>Couldn’t finish the preview</h2>
      <ul>{data.limitations.map((s: string, i: number) => <li key={i}><ReviewMarkdown>{s}</ReviewMarkdown></li>)}</ul>
      {!data.limitations.length && <ReviewMarkdown>{data.summary || "Open run details for the execution result."}</ReviewMarkdown>}
    </section>}
    {preview && <PreviewCanvas sessionId={sessionId} jobId={data.job_id} title={preview.title} />}
    {data.history.length > 1 && <details className="preview-history"><summary>Earlier previews</summary><ul>{data.history.map((h: any) => <li key={h.job_id}><a href={`/previews/${sessionId}?job=${h.job_id}`}>{h.head.slice(0, 12)} · {new Date(h.created_at * 1000).toLocaleString()}</a> · {h.status}</li>)}</ul></details>}
  </main>;
}

function PreviewCanvas({ sessionId, jobId, title }: { sessionId: string; jobId: string; title: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(720);
  const observer = useRef<ResizeObserver | null>(null);
  const resize = () => {
    observer.current?.disconnect();
    const body = frame.current?.contentDocument?.body;
    if (!body) return;
    const update = () => setHeight(Math.min(Math.max(body.scrollHeight, 240), 100000));
    observer.current = new ResizeObserver(update);
    observer.current.observe(body);
    update();
  };
  useEffect(() => () => observer.current?.disconnect(), [jobId]);
  return <iframe ref={frame} onLoad={resize} className="preview-canvas" title={title} style={{ height }} sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" src={`/api/previews/${encodeURIComponent(sessionId)}/document?job=${encodeURIComponent(jobId)}`} />;
}
