import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Check, Loader2, RefreshCw } from "lucide-react";
import { api, send } from "./api";
import { Badge, Field, ProviderMark } from "./ui";

type Repository = { name: string; installation_id: number };
type Status = {
  state: "unconfigured" | "needs_installation" | "available";
  app_name?: string;
  app_url?: string;
  owner?: string;
  owner_type?: string;
  repositories: Repository[];
  installations: { account: string; suspended: boolean }[];
};

export function GitHubSetup({
  setup,
  connected,
  revision,
  onConnected,
  onCreate,
  onExisting,
  onReset,
}: {
  setup: { github?: boolean; github_app_url?: string; github_reset?: boolean };
  connected: Repository[];
  revision: number;
  onConnected: () => Promise<void>;
  onCreate: () => void;
  onExisting: () => void;
  onReset: (undo: boolean) => Promise<void>;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [connectError, setConnectError] = useState("");
  const [query, setQuery] = useState("");
  const [connecting, setConnecting] = useState("");
  const [notice, setNotice] = useState("");
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState("");
  const refresh = useRef<() => void>(() => {});

  useEffect(() => {
    let alive = true;
    let pending = false;
    const controller = new AbortController();
    const check = async () => {
      if (pending) return;
      pending = true;
      setChecking(true);
      setError("");
      try {
        const result = await api("/github/status", {
          signal: controller.signal,
        });
        if (alive) setStatus(result);
      } catch (err) {
        if (alive) setError((err as Error).message);
      } finally {
        pending = false;
        if (alive) setChecking(false);
      }
    };
    refresh.current = check;
    check();
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      controller.abort();
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [revision]);

  const appUrl = status?.app_url || setup.github_app_url;
  const configured = status ? status.state !== "unconfigured" : setup.github;
  const repositories = status?.repositories || [];
  const connectedNames = new Set(
    connected
      .filter((r) =>
        repositories.some(
          (repo) =>
            repo.name.toLowerCase() === r.name.toLowerCase() &&
            repo.installation_id === r.installation_id,
        ),
      )
      .map((r) => r.name.toLowerCase()),
  );
  const verifiedCount = repositories.filter((r) =>
    connectedNames.has(r.name.toLowerCase()),
  ).length;
  const unavailable = connected.filter(
    (r) =>
      !repositories.some(
        (repo) => repo.name.toLowerCase() === r.name.toLowerCase(),
      ),
  );
  const matches = repositories.filter((r) =>
    r.name.toLowerCase().includes(query.toLowerCase()),
  );
  const connect = async (repo: Repository) => {
    setConnecting(repo.name);
    setConnectError("");
    setNotice("");
    try {
      await api("/github/connect", send("POST", { name: repo.name }));
      await onConnected();
      setNotice(`${repo.name} connected. Continue with your PR agent below.`);
    } catch (err) {
      setConnectError((err as Error).message);
    } finally {
      setConnecting("");
    }
  };
  const reset = async () => {
    setResetting(true);
    setResetError("");
    try {
      await onReset(!!setup.github_reset);
    } catch (err) {
      setResetError((err as Error).message);
      setResetting(false);
    }
  };

  return (
    <section className="setup-panel github-setup" aria-label="GitHub setup">
      <div className="setup-title">
        <ProviderMark type="github" />
        <div>
          <h3>
            {checking
              ? "Checking GitHub access…"
              : error
                ? "GitHub access could not be checked"
                : !configured
                  ? "Connect GitHub"
                  : repositories.length
                    ? "Choose repositories to review"
                    : "Finish GitHub installation"}
          </h3>
          <p>
            {status?.owner && (
              <>
                App owned by <strong>{status.owner}</strong>.{" "}
              </>
            )}
            {checking
              ? "Checking the App and the repositories it can access."
              : error
                ? "Your saved configuration is unchanged. Retry the check or update the App settings below."
                : !configured
                  ? "Create an App in the account that owns your repositories. GitHub sends its credentials back automatically."
                  : !repositories.length
                    ? "The App is saved, but GitHub has not made any repositories available to it."
                    : "Connect a repository below. Installation details are filled in automatically."}
          </p>
        </div>
        <Badge tone={!checking && !error && verifiedCount ? "green" : ""}>
          {checking
            ? "Checking"
            : error
              ? "Check failed"
              : verifiedCount
                ? `${verifiedCount} connected`
                : "Setup needed"}
        </Badge>
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      <div className="actions">
        {configured && appUrl ? (
          <a
            className={"button " + (!repositories.length ? "primary" : "")}
            href={appUrl + "/installations/new"}
            target="_blank"
            rel="noreferrer"
          >
            {repositories.length
              ? "Manage GitHub access"
              : "Continue installation on GitHub"}{" "}
            <ArrowUpRight size={14} />
          </a>
        ) : !checking && !error && !configured ? (
          <button className="primary" onClick={onCreate} disabled={resetting}>
            Connect GitHub organization <ArrowUpRight size={14} />
          </button>
        ) : null}
        <button
          disabled={checking || !!connecting || resetting}
          onClick={() => refresh.current()}
        >
          {checking ? (
            <Loader2 size={14} className="spin" />
          ) : (
            <RefreshCw size={14} />
          )}{" "}
          {checking ? "Checking…" : "Check again"}
        </button>
        {!configured && !checking && !error && (
          <button onClick={onExisting} disabled={resetting}>
            Use existing App
          </button>
        )}
        <button
          aria-label={
            setup.github_reset
              ? "Undo reset GitHub setup"
              : "Reset GitHub setup"
          }
          disabled={resetting || !!connecting}
          onClick={reset}
          title={
            setup.github_reset
              ? "Restore the previous GitHub App connection."
              : "Restart GitHub setup. Your workspace settings are kept. You can undo this."
          }
        >
          {resetting
            ? "Updating…"
            : setup.github_reset
              ? "Undo reset"
              : "Reset"}
        </button>
      </div>
      {setup.github_reset && (
        <p className="notice" role="status">
          GitHub setup reset. Your agents and workspace settings are saved. The
          App remains on GitHub. Connect again or choose Undo reset.
        </p>
      )}
      {resetError && (
        <p className="error" role="alert">
          {resetError}
        </p>
      )}
      {configured && (
        <p className="setup-help">
          Select only the repositories you want to review on GitHub. This page
          checks again when you return. If your organization is missing or
          approval is pending, an organization owner needs to finish
          installation. A private App can only be installed on the account that
          owns it.
        </p>
      )}
      {!checking &&
        !error &&
        !repositories.length &&
        status?.owner_type === "User" && (
          <div className="account-recovery">
            <p className="setup-help">
              This App belongs to the personal account{" "}
              <strong>{status.owner}</strong>. If your organization is not
              offered during installation, create an App owned by that
              organization. An organization owner may need to complete this
              step.
            </p>
            <button onClick={onCreate} disabled={resetting || !!connecting}>
              Connect an organization instead
            </button>
          </div>
        )}
      {!checking &&
        !error &&
        status?.installations.some((i) => i.suspended) && (
          <p role="alert" className="error">
            Installation suspended for{" "}
            {status.installations
              .filter((i) => i.suspended)
              .map((i) => i.account)
              .join(", ")}
            . An organization owner needs to restore it on GitHub.
          </p>
        )}
      {!setup.github_reset && !checking && !error && unavailable.length > 0 && (
        <p role="alert" className="error">
          GitHub access is missing for{" "}
          {unavailable.map((r) => r.name).join(", ")}. Their review settings are
          saved. Restore repository access on GitHub before starting reviews.
        </p>
      )}
      {!checking && !error && repositories.length > 0 && (
        <div className="repository-picker">
          <Field label="Find a repository">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search owner/repository"
            />
          </Field>
          <p className="setup-help">
            New repositories allow your existing People entries to call their
            permitted agents there. You can narrow repository access in Access.
          </p>
          <ul className="repository-options">
            {matches.map((repo) => (
              <li key={repo.name}>
                <span>{repo.name}</span>
                {connectedNames.has(repo.name.toLowerCase()) ? (
                  <span className="connected-label">
                    <Check size={14} /> Connected
                  </span>
                ) : (
                  <button
                    disabled={!!connecting || resetting}
                    onClick={() => connect(repo)}
                    aria-label={`Connect ${repo.name}`}
                  >
                    {connecting === repo.name
                      ? "Connecting…"
                      : connected.some(
                            (r) =>
                              r.name.toLowerCase() === repo.name.toLowerCase(),
                          )
                        ? "Reconnect"
                        : "Connect"}
                  </button>
                )}
              </li>
            ))}
          </ul>
          {!matches.length && (
            <p>
              No repositories match “{query}”. Update GitHub access if the
              repository is missing.
            </p>
          )}
        </div>
      )}
      {connectError && (
        <p className="error" role="alert">
          {connectError} You can retry Connect without creating a duplicate.
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {(configured || error) && (
        <details className="github-settings">
          <summary>App settings and recovery</summary>
          <p className="setup-help">
            If this App belongs to the wrong account, have an organization owner
            create an App in the organization that owns your repositories.
            Replacing the App keeps model connections, agents, and access
            settings; reconnect repositories to the new installation.
          </p>
          <div className="actions">
            <button onClick={onCreate} disabled={resetting || !!connecting}>
              Create App for another account <ArrowUpRight size={14} />
            </button>
            <button onClick={onExisting} disabled={resetting || !!connecting}>
              Update App credentials
            </button>
          </div>
        </details>
      )}
    </section>
  );
}
