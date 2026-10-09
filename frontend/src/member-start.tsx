import { useEffect, useState } from "react";
import { ArrowUpRight, Check, Copy, Loader2 } from "lucide-react";
import { api } from "./api";
import { Brand, ProviderMark } from "./ui";

type Availability = {
  state:
    "github" | "workspace" | "approval" | "reviewer" | "unavailable" | "ready";
  owner: string;
  max_minutes: number;
  repositories: { name: string; reviewers: string[] }[];
};

export function MemberStart({
  user,
  account,
  githubLogin,
  navigate,
  onLogout,
}: {
  user: any;
  account: boolean;
  githubLogin: boolean;
  navigate: (path: string) => void;
  onLogout: () => void;
}) {
  const [data, setData] = useState<Availability | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [repoName, setRepoName] = useState("");
  const [reviewerName, setReviewerName] = useState("");
  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api("/start"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    if (account) return;
    let alive = true;
    const check = () => {
      if (alive && document.visibilityState === "visible") void refresh();
    };
    void refresh();
    window.addEventListener("focus", check);
    return () => {
      alive = false;
      window.removeEventListener("focus", check);
    };
  }, [account, user.github_login]);
  useEffect(() => {
    document.title =
      (account ? "Your account" : "Start a review") + " · LiteReview";
    document
      .querySelector<HTMLElement>(".member-start h1")
      ?.focus({ preventScroll: true });
  }, [account, data?.state]);
  const repo =
    data?.repositories.find((r) => r.name === repoName) ||
    data?.repositories[0];
  const reviewer = repo?.reviewers.includes(reviewerName)
    ? reviewerName
    : repo?.reviewers[0];
  const command = reviewer ? `@litereview ${reviewer}` : "";
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Copied to clipboard.");
    } catch {
      setNotice("Select and copy the text below.");
    }
  };
  const owner = data?.owner || "your workspace owner";
  return (
    <div className="member-start">
      <header className="member-header">
        <Brand />
        <div className="actions">
          {user.admin && (
            <button onClick={() => navigate("/repositories")}>Workspace</button>
          )}
          <button
            className="text-button"
            onClick={() => navigate(account ? "/start" : "/account")}
          >
            {account ? "Start a review" : "Account"}
          </button>
        </div>
      </header>
      <main className="member-content">
        {account ? (
          <>
            <h1 tabIndex={-1}>Your account</h1>
            <p className="member-intro">{user.email}</p>
            <div className="account-detail">
              <strong>Google account</strong>
              <span>
                <Check size={15} /> Connected
              </span>
            </div>
            <div className="account-detail">
              <strong>GitHub account</strong>
              <span>
                {user.github_login ? `@${user.github_login}` : "Not connected"}
              </span>
            </div>
            {githubLogin && (
              <a
                className="button primary full"
                href="/api/auth/github?next=/account"
              >
                <ProviderMark type="github" />
                {user.github_login ? "Change GitHub account" : "Connect GitHub"}
              </a>
            )}
            <p className="member-note">
              Sign in with Google again after{" "}
              {new Date(user.expires_at * 1000).toLocaleString()} to keep
              requesting reviews. Your GitHub link and access settings are
              saved.
            </p>
            <button onClick={onLogout}>Sign out</button>
          </>
        ) : (
          <>
            {loading && !data ? (
              <p role="status">
                <Loader2 size={16} className="spin" /> Checking your workspace…
              </p>
            ) : null}
            {error ? (
              <>
                <h1 tabIndex={-1}>Couldn’t check your workspace.</h1>
                <p className="error" role="alert">
                  {error}
                </p>
                <button className="primary" onClick={refresh}>
                  Try again
                </button>
              </>
            ) : (
              data && (
                <>
                  <div className="member-status">
                    <Check size={15} />
                    {user.github_login
                      ? `Connected as @${user.github_login}`
                      : "Google account connected"}
                  </div>
                  {data.state === "github" && (
                    <>
                      <h1 tabIndex={-1}>Connect your GitHub.</h1>
                      <p className="member-intro">
                        Link the account you use to comment on pull requests.
                        Then LiteReview can recognize your review requests.
                      </p>
                      <a
                        className="button primary full"
                        href="/api/auth/github?next=/start"
                      >
                        <ProviderMark type="github" />
                        Connect GitHub
                      </a>
                      <p className="member-note">
                        Your workspace owner manages repository access.
                      </p>
                    </>
                  )}
                  {data.state === "workspace" && (
                    <>
                      <h1 tabIndex={-1}>Your workspace is being connected.</h1>
                      <p className="member-intro">
                        {owner} needs to finish GitHub setup before you can
                        connect your account and request a review.
                      </p>
                    </>
                  )}
                  {data.state === "approval" && (
                    <>
                      <h1 tabIndex={-1}>You’re connected. Access is next.</h1>
                      <p className="member-intro">
                        Ask {owner} to add @{user.github_login} to People so you
                        can request reviews.
                      </p>
                      <button
                        className="primary"
                        onClick={() =>
                          copy(
                            `Please approve @${user.github_login} in LiteReview → People so I can request reviews.`,
                          )
                        }
                      >
                        <Copy size={15} />
                        Copy access request
                      </button>
                      <p className="member-note">
                        Nothing is sent automatically.
                      </p>
                    </>
                  )}
                  {data.state === "reviewer" && (
                    <>
                      <h1 tabIndex={-1}>Your reviewer isn’t ready yet.</h1>
                      <p className="member-intro">
                        {owner} needs to connect a repository and test a
                        reviewer you have access to. Your Google and GitHub
                        accounts are already connected.
                      </p>
                    </>
                  )}
                  {data.state === "unavailable" && (
                    <>
                      <h1 tabIndex={-1}>GitHub is temporarily unavailable.</h1>
                      <p className="member-intro">
                        We couldn’t verify your repository access. Your saved
                        setup is unchanged.
                      </p>
                    </>
                  )}
                  {["workspace", "reviewer", "unavailable"].includes(
                    data.state,
                  ) && (
                    <button
                      className="primary"
                      onClick={refresh}
                      disabled={loading}
                    >
                      {loading ? "Checking…" : "Check again"}
                    </button>
                  )}
                  {data.state === "ready" && repo && (
                    <>
                      <h1 tabIndex={-1}>Start your first review.</h1>
                      <p className="member-intro">
                        Add this comment to a pull request in a connected
                        repository. LiteReview will post its findings in the PR.
                      </p>
                      {data.repositories.length > 1 && (
                        <label className="start-select">
                          Repository
                          <select
                            value={repo.name}
                            onChange={(e) => {
                              setRepoName(e.target.value);
                              setReviewerName("");
                            }}
                          >
                            {data.repositories.map((r) => (
                              <option key={r.name}>{r.name}</option>
                            ))}
                          </select>
                        </label>
                      )}
                      {repo.reviewers.length > 1 && (
                        <label className="start-select">
                          Reviewer
                          <select
                            value={reviewer}
                            onChange={(e) => setReviewerName(e.target.value)}
                          >
                            {repo.reviewers.map((name) => (
                              <option key={name}>{name}</option>
                            ))}
                          </select>
                        </label>
                      )}
                      <div className="review-command">
                        <code>{command}</code>
                        <button
                          className="primary"
                          onClick={() => copy(command)}
                        >
                          <Copy size={15} />
                          Copy comment
                        </button>
                      </div>
                      <p className="member-note">
                        Minimum review time comes from each reviewer's saved
                        settings. Your comment only needs reviewer names.
                      </p>
                      <section className="member-repositories">
                        <h2>Your repositories</h2>
                        {data.repositories.map((r) => (
                          <a
                            key={r.name}
                            href={`https://github.com/${r.name}/pulls`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <span>{r.name}</span>
                            <ArrowUpRight size={15} />
                          </a>
                        ))}
                        <p className="member-note">
                          Showing only repositories and reviewers approved for
                          your account.
                        </p>
                      </section>
                    </>
                  )}
                </>
              )
            )}
            <p role="status" className="member-note">
              {notice}
            </p>
          </>
        )}
      </main>
    </div>
  );
}
