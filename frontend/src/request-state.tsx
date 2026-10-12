import { useEffect, useState, useSyncExternalStore } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { requestState } from "./api";

export function LoadingState({ label, error = "", retry }: { label: string; error?: string; retry?: () => void }) {
  const [slow, setSlow] = useState(false);
  useEffect(() => { setSlow(false); const timer = setTimeout(() => setSlow(true), 8000); return () => clearTimeout(timer); }, [label, error]);
  return <div className="loading-state" aria-busy={!error}>
    <div className="loading-message" role={error ? "alert" : "status"}>
      {!error && <Loader2 size={17} className="spin" aria-hidden="true" />}
      <div><strong>{error ? "Couldn’t load this page" : label}</strong>
        <p>{error || (slow ? "This is taking longer than usual. You can retry or open another page." : "You can keep navigating while this loads.")}</p></div>
      {retry && (error || slow) && <button onClick={retry}><RefreshCw size={14} aria-hidden="true" />Retry</button>}
    </div>
    {!error && <div className="loading-placeholder" aria-hidden="true"><span /><span /><span /></div>}
  </div>;
}

export function RequestActivity() {
  const pending = useSyncExternalStore(requestState.subscribe, requestState.getSnapshot);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!pending.length) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pending.length > 0]);
  const request = pending[0];
  return <div className="request-activity" role="status" aria-live="polite">
    {request && <><Loader2 size={14} className="spin" aria-hidden="true" /><span>{request.label}{now - request.started > 8000 && " Still waiting for the server."}</span></>}
  </div>;
}
