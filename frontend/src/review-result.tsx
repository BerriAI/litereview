import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowUpRight, Check, FileCode2, Loader2 } from "lucide-react";
import { Badge } from "./ui";
import "./review-result.css";

export function ReviewMarkdown({ children }: { children: string }) {
  return (
    <div className="review-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // Model output can link to images; only saved review artifacts load inline.
          img: ({ alt, src }) => (
            <a href={src} target="_blank" rel="noopener noreferrer">
              {alt || "View image"}
            </a>
          ),
          table: ({ children }) => (
            <div className="review-table">
              <table>{children}</table>
            </div>
          ),
          h1: ({ children }) => <h4>{children}</h4>,
          h2: ({ children }) => <h4>{children}</h4>,
          h3: ({ children }) => <h4>{children}</h4>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

export function ReviewResult({
  job,
  repo,
  head,
}: {
  job: any;
  repo: string;
  head: string;
}) {
  const result = job.result || {};
  const findings = result.findings || [];
  const limitations = result.limitations || [];
  const checks = result.checks || [];
  const finished = ["completed", "failed", "timed_out"].includes(job.status);
  const incomplete =
    finished && (job.status !== "completed" || limitations.length > 0);
  return (
    <>
      {!finished && (
        <div className="review-progress" role="status">
          <Loader2 size={16} className="spin" />
          <span>
            {job.status === "queued"
              ? "Waiting to start…"
              : job.status === "starting"
                ? "Preparing the review…"
                : "Reviewing the changes…"}
          </span>
        </div>
      )}
      {finished && result.summary && (
        <div className="review-response">
          <ReviewMarkdown>{result.summary}</ReviewMarkdown>
        </div>
      )}
      {finished && !incomplete && !result.summary && findings.length === 0 && (
        <div className="review-clean">
          <Check size={17} />
          <span>No issues found.</span>
        </div>
      )}
      {findings.map((finding: any, index: number) => (
        <section className="review-finding" key={index}>
          <h4>
            <Badge
              tone={
                ["high", "critical"].includes(finding.severity)
                  ? "red"
                  : finding.severity === "medium"
                    ? "amber"
                    : ""
              }
            >
              {finding.severity}
            </Badge>
            <span>{finding.title}</span>
          </h4>
          {finding.file && (
            <a
              className="source-link"
              href={`https://github.com/${repo}/blob/${head}/${finding.file.replace(/^\/+/, "").split("/").map(encodeURIComponent).join("/")}${finding.line ? "#L" + finding.line : ""}`}
              target="_blank"
              rel="noreferrer"
            >
              <FileCode2 size={13} />
              <code>
                {finding.file}
                {finding.line ? ":" + finding.line : ""}
              </code>
              <ArrowUpRight size={12} />
            </a>
          )}
          <ReviewMarkdown>{finding.explanation}</ReviewMarkdown>
          {finding.evidence?.length > 0 && (
            <details className="review-evidence">
              <summary>Evidence</summary>
              <ul>
                {finding.evidence.map((evidence: string, i: number) => (
                  <li key={i}>
                    <ReviewMarkdown>{evidence}</ReviewMarkdown>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>
      ))}
      {checks.length > 0 && (
        <details className="review-checks" open={checks.some((check: any) => check.status === "not_run")}>
          <summary>Checks</summary>
          <ul>
            {checks.map((check: any, index: number) => (
              <li key={index}>
                <div className="review-check-heading">
                  <strong>{check.name}</strong>
                  <Badge tone={check.status === "passed" ? "green" : check.status === "failed" ? "red" : ""}>
                    {check.status === "not_run" ? "Not run" : check.status === "failed" ? "Failed" : "Passed"}
                  </Badge>
                  <span>{check.required ? "Required" : "Optional"}</span>
                </div>
                <ReviewMarkdown>{check.details}</ReviewMarkdown>
              </li>
            ))}
          </ul>
        </details>
      )}
      {incomplete && (limitations.length > 0 || !result.summary) && (
        <section className="review-incomplete">
          <h4>Couldn’t complete</h4>
          {limitations.length > 0 ? (
            <ul>
              {limitations.map((limitation: string, i: number) => (
                <li key={i}>
                  <ReviewMarkdown>{limitation}</ReviewMarkdown>
                </li>
              ))}
            </ul>
          ) : (
            <ReviewMarkdown>
              The task ended before completion. Open activity for execution details.
            </ReviewMarkdown>
          )}
        </section>
      )}
    </>
  );
}
