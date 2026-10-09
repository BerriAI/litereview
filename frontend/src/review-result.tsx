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
      {finished && !incomplete && findings.length === 0 && (
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
      {incomplete && (
        <section className="review-incomplete">
          <h4>Couldn’t finish these checks</h4>
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
              {result.summary ||
                "The review ended before all checks finished. Open activity for execution details."}
            </ReviewMarkdown>
          )}
        </section>
      )}
      {result.summary && (!incomplete || limitations.length > 0) && (
        <details className="review-notes">
          <summary>Review notes</summary>
          <ReviewMarkdown>{result.summary}</ReviewMarkdown>
        </details>
      )}
    </>
  );
}
