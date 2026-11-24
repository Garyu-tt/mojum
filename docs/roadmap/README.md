# Mojum Live Roadmap

A read-only public engineering observatory. No dependencies, no external JavaScript
libraries, no credentials in the browser.

## View

- Modules: group by existing milestone / area label, or explicitly marked title heuristic.
- All matching items: searchable, paginated issues, PRs and imported project-only drafts.
- PR flow: recorded open / close / merge dates and exact head → base refs. Opening a PR
  is not proof of the branch's creation date.
- Commit graph: the latest 100 commits reachable from main, with exact parent SHAs.
  Dashed edges leave the current page; squash/rebase merges are recorded in PR flow.
- Releases: real published releases only.
- Inspector: confirmed issue relationships from GitHub (parent, sub-issues, blockers);
  textual #references are separate and are not treated as dependencies.

## Preview / deploy

Open the HTML locally (browser must permit GitHub API access), or serve the
directory via a static HTTP server. The live REST fallback is subject to GitHub
unauthenticated rate limits. The Actions snapshot is generated with a read-only
repository token, contains minimal public fields, and is published hourly.

Before enabling publication, confirm this repository is not already hosting a
different Pages site. In Settings → Pages → Build and deployment choose GitHub
Actions. Merge this PR, then run Mojum Live Roadmap manually if necessary.
Expected URL: https://garyu-tt.github.io/mojum/roadmap/ (not active until deployment).
The PR workflow uploads a preview artifact without publishing it.

Local snapshot:

    python tools/build_live_roadmap.py
    python -m http.server 8000 --directory docs/roadmap

## Personal Project metadata (optional)

Repository APIs do not enumerate personal Project-only drafts or custom date fields.
An authenticated local GitHub CLI with read:project access can export Project #1:

    gh project item-list 1 --owner Garyu-tt --limit 10000 --format json > project.json

Check the returned count against the Project UI. Import project.json via the
Inspector; it stays in memory in the current browser, never uploaded or persisted.
Imported URL matches are deduplicated against repository items. Other project items
are retained separately. No dates, dependencies or issue hierarchy are guessed.

For native Project Roadmap, configure its Start date and Target date fields and
milestone markers. Creation/merge dates in this observatory are observed history,
not a substitute for planned dates. No native project fields are modified by this
tool. Only write confirmed dates and explicitly recorded dependencies to Projects.

## Tests

    node tools/test_live_roadmap.cjs

The GitHub Actions PR workflow checks browser JavaScript, Python syntax, the
fixture tests and builds a real paginated repository snapshot.
