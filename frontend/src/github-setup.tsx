import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  Loader2,
  LockKeyhole,
  MoreHorizontal,
  Plus,
  RefreshCw,
} from "lucide-react";
import { api, send } from "./api";
import { ProviderMark } from "./ui";

type Row = { id: string; name: string; [key: string]: any };
type Status = {
  state: "unconfigured" | "needs_installation" | "available";
  owner?: string;
  owner_type?: string;
  public?: boolean;
  app_url?: string;
  review_events_enabled?: boolean;
  repositories: { name: string; installation_id: number }[];
  installations: { account: string; suspended: boolean }[];
  excluded?: string[];
};
type Readiness = {
  runtime: boolean;
  has_review: boolean;
  agents: {
    id: string;
    name: string;
    checked: boolean;
    check_id?: string;
    check_status?: string;
  }[];
};
type Props = {
  setup: any;
  githubAccount: string;
  ssoRequired: boolean;
  canLinkGithub: boolean;
  onAccount: () => void;
  connected: Row[];
  agents: Row[];
  groups: Row[];
  connections: Row[];
  access: Row[];
  revision: number;
  onConnected: () => Promise<void>;
  onCreate: () => void;
  onExisting: () => void;
  onReset: (undo: boolean) => Promise<void>;
  onSettings: () => void;
  onModel: () => void;
  onAgent: (agent?: Row) => void;
  onEdit: (repo: Row) => void;
  onAccess: (repo: Row) => void;
  onRemove: (repo: Row) => void;
  onPeople: () => void;
  onStart: () => void;
};
export function GitHubSetup(p: Props) {
  const [status, setStatus] = useState<Status | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    document
      .querySelector<HTMLElement>(".repositories-page h1")
      ?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    let alive = true,
      pending = false;
    const controller = new AbortController();
    const check = async () => {
      if (pending) return;
      pending = true;
      setChecking(true);
      setError("");
      try {
        const result = await api("/github/sync", {
          ...send("POST"),
          signal: controller.signal,
        });
        if (!alive) return;
        await p.onConnected();
        const ready = await api("/workspace/readiness", {
          signal: controller.signal,
        });
        if (alive) {
          setStatus(result);
          setReadiness(ready);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        pending = false;
        if (alive) setChecking(false);
      }
    };
    void check();
    const visible = () => {
      if (document.visibilityState === "visible") void check();
    };
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false;
      controller.abort();
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [p.revision, refresh]);
  const pending = readiness?.agents.find((a) =>
    ["queued", "starting", "running"].includes(a.check_status || ""),
  );
  useEffect(() => {
    if (!pending) return;
    let alive = true;
    const timer = setInterval(() => {
      api("/workspace/readiness")
        .then((r) => {
          if (alive) setReadiness(r);
        })
        .catch((e) => {
          if (alive) setActionError(e.message);
        });
    }, 4000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [pending?.check_id]);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setActionError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setActionError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const installed = status?.repositories || [];
  const available = (r: Row) =>
    installed.some(
      (i) =>
        i.name.toLowerCase() === r.name.toLowerCase() &&
        i.installation_id === r.installation_id,
    );
  const repos = p.connected.filter((r) =>
    r.name.toLowerCase().includes(query.toLowerCase()),
  );
  const hasRepos = p.connected.some(available);
  const checked = readiness?.agents.filter((a) => a.checked) || [];
  const organization = p.setup.github_organization || "";
  const mismatch =
    status?.owner_type === "User" &&
    organization &&
    status.owner?.toLowerCase() !== organization.toLowerCase() &&
    status.public !== true &&
    !installed.length;
  const hasModels = p.connections.some((c) => c.type !== "mcp");
  const hasPeople =
    p.ssoRequired ||
    p.access.some(
      (g) => g.subject === "user" && (g.scope === "all" || g.target_ids.length),
    );
  const appUrl = status?.app_url || p.setup.github_app_url;
  const installUrl = appUrl ? appUrl + "/installations/new" : "";
  const needs = !hasModels
    ? "model"
    : !p.agents.length
      ? "agent"
      : !readiness?.runtime
        ? "runtime"
        : pending
          ? "pending"
          : !checked.length
            ? "check"
            : !hasPeople
              ? "people"
              : "review";
  const copyRequest = () =>
    run(async () => {
      const account = p.githubAccount
        ? `@${p.githubAccount}`
        : "my GitHub account";
      const text = `Please help connect LiteReview to ${organization || "our organization"} on GitHub.

To let me create the GitHub App, an organization owner can open organization Settings → Organization roles → Role assignments → New role assignment, select ${account} and App Manager, then choose Add new assignment. This role manages all organization-owned Apps and is available on supported GitHub plans. If unavailable, an organization owner can create the App instead.

To let me install it myself, open organization Settings → Member privileges → GitHub Apps, enable “Allow repository admins to install GitHub Apps for their repositories,” and save. I also need Admin access on each target repository. This policy applies to all repository admins. If you prefer to keep installations restricted, please install the App on the repositories we choose.

Continue at ${location.origin}/repositories. No extra LiteReview access is granted by this link.
GitHub instructions: https://docs.github.com/en/organizations/managing-peoples-access-to-your-organization-with-roles/using-organization-roles`;
      await navigator.clipboard.writeText(text);
      setNotice("Setup request copied. Nothing was sent.");
    });
  return (
    <section
      className="repositories-page"
      aria-label="Repository setup"
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        const menu = (e.target as HTMLElement).closest<HTMLDetailsElement>(
          "details.connection-menu",
        );
        if (menu?.open) {
          menu.open = false;
          menu.querySelector("summary")?.focus();
        }
      }}
    >
      <header className="page-heading">
        <div>
          <h1 tabIndex={-1}>Repositories</h1>
          <p>Connect GitHub to choose where LiteReview can run.</p>
        </div>
        {hasRepos && !error && (
          <a
            className={
              "button " +
              (needs === "review" && readiness?.has_review ? "primary" : "")
            }
            href={installUrl}
            target="_blank"
            rel="noreferrer"
          >
            <Plus size={16} />
            Add repositories
          </a>
        )}
      </header>
      {p.ssoRequired && (
        <div className="github-account-summary">
          <div>
            <strong>Your GitHub account</strong>
            <span>
              {p.githubAccount ? `@${p.githubAccount} · Linked` : "Not linked"}
            </span>
          </div>
          {p.githubAccount ? (
            <button className="text-button" onClick={p.onAccount}>
              Account settings
            </button>
          ) : p.canLinkGithub ? (
            <a className="button" href="/api/auth/github?next=/repositories">
              Connect your account
            </a>
          ) : (
            <small>Connect repositories below to enable account linking.</small>
          )}
        </div>
      )}
      {p.setup.github_reset && (
        <div className="setup-recovery">
          <div>
            <strong>GitHub setup was reset.</strong>
            <p>Your agents and access settings are saved.</p>
          </div>
          <button disabled={busy} onClick={() => run(() => p.onReset(true))}>
            Undo reset
          </button>
        </div>
      )}
      {actionError && (
        <p className="error" role="alert">
          {actionError}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {error ? (
        <div className="setup-recovery" role="alert">
          <div>
            <strong>Couldn’t verify GitHub access.</strong>
            <p>{error}</p>
            <p>Your saved setup is unchanged.</p>
          </div>
          <button className="primary" onClick={() => setRefresh((v) => v + 1)}>
            Try again
          </button>
        </div>
      ) : checking && !status ? (
        <p className="onboarding-loading" role="status">
          <Loader2 size={16} className="spin" />
          Checking GitHub access…
        </p>
      ) : null}
      {!error &&
        status &&
        (!hasRepos ? (
          <div className="repo-empty">
            <ProviderMark type="github" />
            <h2>
              {mismatch
                ? `${organization} repositories aren’t connected.`
                : status.state === "unconfigured"
                  ? "GitHub repositories aren’t connected."
                  : "No repositories are connected yet."}
            </h2>
            <p>
              {mismatch
                ? `The saved private GitHub App belongs to @${status.owner} and can only be installed on that personal account. A ${organization} owner or organization-wide App Manager can create the organization’s App. Repository installation may need an owner’s approval.`
                : status.state === "unconfigured"
                  ? `Connect the LiteReview App${organization ? ` to ${organization}` : ""}, then choose repositories on GitHub. Your selections will appear here automatically.`
                  : "The App is saved. Continue on GitHub to install it and select repositories. You won’t need to enter credentials again."}
            </p>
            {status.state === "unconfigured" || mismatch ? (
              <button className="primary" onClick={p.onCreate}>
                {mismatch
                  ? `Connect ${organization} repositories`
                  : organization
                    ? `Connect ${organization}`
                    : "Connect GitHub"}
                <ArrowUpRight size={15} />
              </button>
            ) : (
              <a
                className="button primary"
                href={installUrl}
                target="_blank"
                rel="noreferrer"
              >
                Continue on GitHub
                <ArrowUpRight size={15} />
              </a>
            )}
            <p className="repo-help">
              A GitHub organization owner may need to approve access.{" "}
              <button className="text-button" onClick={copyRequest}>
                Copy setup request
              </button>
            </p>
            <div className="actions">
              <button className="text-button" onClick={p.onExisting}>
                Use an existing App
              </button>
              <button
                className="text-button"
                onClick={() => setRefresh((v) => v + 1)}
                disabled={checking}
              >
                {checking ? "Checking…" : "Check installation"}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="organization-row">
              <ProviderMark type="github" />
              <div className="organization-name">
                <strong>{organization || status.owner || "GitHub"}</strong>
                <small>GitHub repositories</small>
              </div>
              <span className="connected-label">
                <Check size={14} />
                Connected
              </span>
              <details className="connection-menu">
                <summary aria-label="GitHub connection options">
                  <MoreHorizontal size={18} />
                </summary>
                <div>
                  <a
                    className="button"
                    href={installUrl}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Manage GitHub access
                  </a>
                  <button onClick={p.onSettings}>App settings</button>
                  <button
                    disabled={checking}
                    onClick={() => setRefresh((v) => v + 1)}
                  >
                    <RefreshCw size={14} />
                    Sync repositories
                  </button>
                </div>
              </details>
            </div>
            {!readiness?.has_review && readiness && (
              <section className="setup-focus">
                <div>
                  <h2>
                    {
                      {
                        model: "Connect a model.",
                        agent: "Choose your first reviewer.",
                        runtime: "Finish the review runtime setup.",
                        pending: "Checking your reviewer…",
                        check: "Test your reviewer.",
                        people: "Approve your first caller.",
                        review: "Try it on a pull request.",
                      }[needs]
                    }
                  </h2>
                  <p>
                    {
                      {
                        model:
                          "Connect a provider or gateway for your reviewers to use.",
                        agent:
                          "Your model connection is already saved. Choose a model and check that your reviewer can run.",
                        runtime:
                          "Review execution needs Modal credentials, a public HTTPS URL, and a running dispatcher. Your setup is saved.",
                        pending:
                          "The private check is running. You can leave this page and return; progress is saved.",
                        check:
                          "Run a private harness check before your first review. This uses Modal and model credits.",
                        people:
                          "Add the GitHub usernames that can request reviews and choose their access.",
                        review:
                          "Connect your personal GitHub identity and get a comment for a repository you can use.",
                      }[needs]
                    }
                  </p>
                </div>
                {needs === "model" && (
                  <button className="primary" onClick={p.onModel}>
                    Connect a model
                  </button>
                )}
                {needs === "agent" && (
                  <button className="primary" onClick={() => p.onAgent()}>
                    Configure reviewer
                  </button>
                )}
                {needs === "runtime" && (
                  <a className="button primary" href="/docs#runtime">
                    View setup guide
                  </a>
                )}
                {needs === "pending" && (
                  <button
                    onClick={() =>
                      run(async () => {
                        await api(
                          `/agents/check/${pending!.check_id}/cancel`,
                          send("POST"),
                        );
                        setReadiness(await api("/workspace/readiness"));
                      })
                    }
                  >
                    Stop check
                  </button>
                )}
                {needs === "check" && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api("/agents/check", send("POST", p.agents[0]));
                        setReadiness(await api("/workspace/readiness"));
                      })
                    }
                  >
                    {busy ? "Starting…" : "Check reviewer"}
                  </button>
                )}
                {needs === "people" && (
                  <button className="primary" onClick={p.onPeople}>
                    Manage people
                  </button>
                )}
                {needs === "review" && (
                  <button className="primary" onClick={p.onStart}>
                    Start a review
                  </button>
                )}
              </section>
            )}
          </>
        ))}
      {status?.installations
        .filter((i) => i.suspended)
        .map((i) => (
          <p className="error" role="alert" key={i.account}>
            The GitHub installation for {i.account} is suspended. An
            organization owner must restore it on GitHub.
          </p>
        ))}
      {p.connected.length > 0 && (
        <>
          {p.connected.length > 5 && (
            <label className="repo-search">
              Find a repository
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search owner/repository"
              />
            </label>
          )}
          <div className="repo-list-heading" aria-hidden="true">
            <span>Repository</span>
            <span>Reviewer</span>
            <span>Status</span>
            <span />
          </div>
          <ul className="repo-list">
            {repos.map((repo) => {
              const grant = p.access.find(
                (g) =>
                  g.subject === "repo" &&
                  g.name.toLowerCase() === repo.name.toLowerCase(),
              );
              const allowed = [...p.agents, ...p.groups].filter(
                (a) =>
                  grant &&
                  (grant.scope === "all" || grant.target_ids.includes(a.id)),
              );
              const ready =
                !error &&
                available(repo) &&
                readiness?.runtime &&
                allowed.some((r) =>
                  r.members
                    ? r.members.length > 0 &&
                      r.members.every((m: { agent_id: string }) =>
                        checked.some((a) => a.id === m.agent_id),
                      )
                    : checked.some((a) => a.id === r.id),
                );
              const automaticRuleCount = repo.review_rules?.filter((r: { enabled: boolean }) => r.enabled).length || 0;
              return (
                <li key={repo.id}>
                  <button className="row-name" onClick={() => p.onEdit({ ...repo, review_events_enabled: status?.review_events_enabled })}>
                    {repo.name}
                  </button>
                  <span className="repo-reviewers">
                    {allowed.map((a) => a.name).join(", ") || "None permitted"}
                    {automaticRuleCount > 0 && <small>
                      {automaticRuleCount} automatic review {automaticRuleCount === 1 ? "rule" : "rules"}
                    </small>}
                  </span>
                  <span className={ready ? "connected-label" : "muted"}>
                    {ready && <Check size={14} />}
                    {error
                      ? "Unverified"
                      : !status
                        ? "Checking access…"
                        : !available(repo)
                          ? "Access missing"
                          : ready
                            ? "Ready to review"
                            : "Needs reviewer"}
                  </span>
                  <details className="connection-menu">
                    <summary aria-label={`Settings for ${repo.name}`}>
                      <ChevronRight size={16} />
                    </summary>
                    <div>
                      <button onClick={() => p.onEdit({ ...repo, review_events_enabled: status?.review_events_enabled })}>
                        Automatic reviews
                      </button>
                      <button onClick={() => p.onEdit({ ...repo, review_events_enabled: status?.review_events_enabled })}>
                        Review environment
                      </button>
                      <button onClick={() => p.onAccess(repo)}>
                        Review permissions
                      </button>
                      <button onClick={() => p.onRemove(repo)}>
                        Remove repository
                      </button>
                    </div>
                  </details>
                </li>
              );
            })}
          </ul>
          {!repos.length && (
            <p className="repo-help">No repositories match “{query}”.</p>
          )}
        </>
      )}
      {!!status?.excluded?.length && (
        <details className="duration-help">
          <summary>Previously removed repositories</summary>
          <p>
            These stay excluded when GitHub syncs. Restore only the repositories
            you want to review.
          </p>
          {installed
            .filter((r) => status.excluded!.includes(r.name.toLowerCase()))
            .map((r) => (
              <div className="excluded-repo" key={r.name}>
                <span>{r.name}</span>
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(
                        "/github/connect",
                        send("POST", { name: r.name }),
                      );
                      setRefresh((v) => v + 1);
                    })
                  }
                >
                  Restore
                </button>
              </div>
            ))}
        </details>
      )}
      <p className="repository-policy">
        <LockKeyhole size={14} />
        <span>
          {p.ssoRequired
            ? "Public and private repositories are supported. Sign in with Google and link GitHub once to request reviews with your permitted reviewers."
            : "Public and private repositories are supported. New selections allow approved People to use their permitted reviewers."}{" "}
          <button className="text-button" onClick={p.onPeople}>
            Manage people
          </button>
        </span>
      </p>
    </section>
  );
}

export function GitHubSettings({
  setup,
  onCreate,
  onExisting,
  onReset,
  onRepositories,
}: {
  setup: any;
  onCreate: () => void;
  onExisting: () => void;
  onReset: (undo: boolean) => Promise<void>;
  onRepositories: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <section className="integration-settings">
      <h2>GitHub App</h2>
      <p>
        Manage App credentials and setup recovery. Choose repositories on the
        Repositories page.
      </p>
      <div className="actions">
        <button onClick={onRepositories}>Open repositories</button>
        <button onClick={onExisting}>App credentials</button>
        <button onClick={onCreate}>Create App for another account</button>
      </div>
      <div className="settings-recovery">
        <h3>
          {setup.github_reset ? "Restore GitHub setup" : "Reset GitHub setup"}
        </h3>
        <p>
          Your agents, connections, and access settings are kept. The App
          remains on GitHub. You can undo a reset.
        </p>
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              await onReset(!!setup.github_reset);
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy
            ? "Updating…"
            : setup.github_reset
              ? "Undo reset"
              : "Reset setup"}
        </button>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </div>
    </section>
  );
}
