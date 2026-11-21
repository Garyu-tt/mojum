# Gemini Flash PR worker: activation and controls

This workflow is staged as a pull request. Scheduled execution starts only after review and merge into the repository's default branch.

## One-time configuration

1. Add repository Actions secret named `GEMINI_API_KEY` containing a Gemini API key. Never commit or paste the key into an issue or prompt.
2. In repository Settings → Actions → General → Workflow permissions, enable “Allow GitHub Actions to create and approve pull requests”. The worker never approves or merges its own PRs.
3. Optional: add Actions variable `GEMINI_MODEL` with the allowed model ID. Default: `gemini-3.5-flash-lite`.
4. Merge the workflow PR. GitHub schedules it hourly at minute 37 UTC; it can also be started manually via workflow_dispatch.

## Separation of duties

- The model step gets the model API key only, and no GitHub write credentials.
- Gemini CLI v0.68.0 is pinned and restricts tools to read and edit files. No shell or web-fetch tools. Workspace `.env` loading is disabled and CLI environment-variable redaction is enabled with `GEMINI_API_KEY` explicitly blocked.
- Before uploading the untrusted patch, the worker checks that it does not contain the literal API key. This is an additional safeguard, **not proof** that encoded secrets or prompt-injection exfiltration are impossible; the workflow remains a draft pending credential-boundary review.
- Agent patches are bounded by path allowlist, size, symlink checks and `git diff --check`.
- Tests run in a **different job on a fresh runner**, not merely in another step of the Gemini-key job. A credential-free runner verifies the candidate patch SHA-256 and runs the full suite. After success, a third, write-scoped job downloads the original immutable candidate artifact (never the test runner's mutable copy), verifies its SHA-256 against the trusted proposal output, and opens a **draft PR**. Generated tests cannot swap the publication payload after validation.
- Issue/PR text and repository content are untrusted. No private Noosphere data or credentials are supplied.

## Known limitations

- The Gemini web GitHub connector is unrelated to headless Gemini CLI credentials.
- Flash-Lite and Gemini API free tiers have rate limits; do not assume unlimited API requests.
- The workflow requires the API secret and the repository Actions permission for PR creation.
- No API key or permissions means no autonomous PRs.
- Failed tests or no useful changes mean no new PR.