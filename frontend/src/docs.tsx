import { ArrowLeft } from "lucide-react";
import { Brand } from "./ui";

export function Documentation() {
  return (
    <div className="docs">
      <header className="docs-header">
        <a className="brand" href="/" aria-label="LiteReview home">
          <Brand />
        </a>
        <a className="button" href="/">
          <ArrowLeft size={14} /> Open workspace
        </a>
      </header>
      <main className="docs-content">
        <h1>Documentation</h1>
        <p className="docs-intro">
          LiteReview runs your PR review agents from a GitHub comment. Each
          agent has its own model, instructions, and tools. Agents run in
          parallel in separate Modal sandboxes and report back on the PR.
        </p>

        <section>
          <h2>Start a review</h2>
          <ol className="docs-steps">
            <li>
              <h3>Connect GitHub and a model</h3>
              <p>
                In <a href="/connections">Connections</a>, create or connect a
                GitHub App, install it on the repositories you want to review,
                then add those repositories. Your organization may require an
                owner to approve the App.
              </p>
              <p>
                Under Models, add your gateway URL and key, or connect OpenAI or
                Anthropic directly. Test the connection to discover available
                models.
              </p>
            </li>
            <li>
              <h3>Create a PR agent</h3>
              <p>
                In <a href="/agents">PR agents</a>, choose a model, describe
                what it should check, and enable the tools it needs. Attach
                skills or MCP tools if useful. Use Test model to check tool and
                image support.
              </p>
            </li>
            <li>
              <h3>Optionally, bundle agents into a group</h3>
              <p>
                In <a href="/groups">Agent groups</a>, name a group, add agents,
                and choose how many copies of each should run. You can reuse the
                same agent in several groups.
              </p>
            </li>
            <li>
              <h3>Set access</h3>
              <p>
                In <a href="/access">Access</a>, add GitHub usernames under
                People and manage connected repositories under Repositories.
                Give each one All agents and groups (including future additions)
                or select specific ones. New repositories start with All access;
                you can narrow it here.
              </p>
              <p>
                Both the person and the repository must have access to each name
                in a command. The person must also have GitHub access to the
                repository. Access to a group includes its members when called
                through that group.
              </p>
            </li>
            <li>
              <h3>Comment on an open PR</h3>
              <p>
                Use agent names, group names, or both in a new comment, with
                nothing else in it:
              </p>
              <pre>
                <code>@litereview frontend security</code>
              </pre>
              <p>
                If frontend is an agent and security is a group of five, this
                starts six independent reviewers. Use just @litereview frontend
                to run that agent alone. Each checks the PR commit at the time
                you called it.
              </p>
              <p>
                Agent and group names must be unique, ignoring capitalization.
                Repeating a name in one comment runs it once. An agent selected
                directly and through a group runs separately for each selection.
              </p>
            </li>
            <li>
              <h3>Read the results</h3>
              <p>
                LiteReview updates a PR comment with each agent’s findings or
                “No findings.” Open <a href="/runs">Runs</a> for details,
                activity, and screenshots. An incomplete check means the agent
                could not finish its configured review. Results are summary
                comments; agents do not push fixes.
              </p>
            </li>
          </ol>
        </section>

        <section>
          <h2>Choose what an agent can do</h2>
          <dl className="docs-capabilities">
            <div>
              <dt>Code only</dt>
              <dd>Read the PR diff and repository files.</dd>
            </div>
            <div>
              <dt>Terminal</dt>
              <dd>Run commands, install dependencies, and execute tests.</dd>
            </div>
            <div>
              <dt>Browser</dt>
              <dd>
                Interact with the running app using Playwright and inspect
                screenshots.
              </dd>
            </div>
            <div>
              <dt>Computer</dt>
              <dd>See the desktop and use its mouse and keyboard.</dd>
            </div>
          </dl>
          <p>
            For agents that run your app, edit its repository in Connections.
            Set the setup command, start command, and a localhost readiness URL.
            Add any test credentials the app needs. Each sandbox starts fresh.
          </p>
        </section>

        <section>
          <h2>Reuse skills and tools</h2>
          <p>
            <a href="/skills">Skills</a> hold reusable instructions and optional
            supporting files. Create one or upload a ZIP with SKILL.md, then
            attach it to agents.
          </p>
          <p>
            Add shared MCP servers under Connections → MCP servers using a
            Streamable HTTP URL. Test a connection to discover its tools, then
            select which tools each agent can use. Connecting a server does not
            enable it for every agent.
          </p>
        </section>
      </main>
    </div>
  );
}
