# FinanceBook — Update System Redesign (Handoff Spec)

This document briefs a new Claude Code session on **how the app's update mechanism
should be rebuilt**. Read `HANDOFF.md` first for the full project picture; this
document only covers the update/distribution work and assumes that context.

The owner has reviewed and approved the design below. Your job is to implement it,
not to re-litigate the architecture. If you hit a genuine blocker, surface it — but
the shape described here is the intended one.

---

## 1. What's wrong with the current setup

Today (per `HANDOFF.md` → "OTA update check" and the manual release steps):

- `updater.py` fetches `version.json` from `adibmazloom.com/financebook/version.json`.
- A new version means the user clicks a **download link**, gets a `.exe`/zip, and
  manually re-downloads + extracts to update.
- Releasing is a 4-step manual chore: bump `CURRENT_VERSION`, run `build.bat`,
  upload to GitHub releases, update `version.json` on the website.

The owner dislikes two things specifically:
1. **The website-as-version-store workflow** — keeping `version.json` in sync by hand.
2. **Users having to repeatedly download and manually install a `.exe`** just to get
   a routine update.

---

## 2. What the owner wants

> **Goal:** Users should never have to manually re-download and reinstall the app for
> routine updates. They see "Update available," click **once**, the app restarts on
> the new version. Everything must still arrive in **one single download** with
> **no separate installs** — the user must never be asked to install Python, pip,
> a runtime, or any second piece of software for the app to work.

Concretely, the owner chose:
- **Update feel:** *One-click.* Not fully silent/background. A banner → one click →
  app restarts updated. (Silent background updates were explicitly **not** wanted —
  for a finance app, users should know when their money software changed.)
- **Packaging:** *Open to repackaging*, with the hard constraint that it stays
  **one download, zero installs.**

---

## 3. The approved architecture

### 3.1 Core idea: thin frozen launcher + replaceable code payload

The file-lock problem on Windows is the thing the whole design works around: a running
`.exe` **cannot overwrite itself**, so it can't cleanly auto-update if all the code is
baked into one frozen bundle. The fix is to split the shipped product into two parts:

```
FinanceBook/                  ← the single zip the user downloads & extracts
  FinanceBook.exe             ← THIN PyInstaller launcher (rarely changes)
                                 - bundles the Python runtime + all deps (Qt, pandas, etc.)
                                 - its only job: find ./app and run it
  app/                        ← the ACTUAL application code, as plain files
    main.py
    updater.py
    sheets/  parsers/  utils/  ui/  ...
    VERSION                   ← plain text file, e.g. "1.0.3"
```

Why this satisfies everything:
- **One download, zero installs:** the Python interpreter and every dependency stay
  bundled *inside* `FinanceBook.exe` (PyInstaller's frozen runtime). The user never
  installs Python or pip. They extract one zip and double-click, exactly like today.
- **One-click updates that don't re-download the `.exe`:** because the real code lives
  in the plain `app/` folder *outside* the frozen bundle, an update is just
  "replace the `app/` folder." That's plain file copying — no installer, no manual
  download. The locked `.exe` is not touched, so the lock problem disappears.
- The downloaded update payload is small — it's just Python source files, not the
  whole ~100MB+ runtime.

### 3.2 The two kinds of release (important)

| Release type | What changed | How the user gets it |
|---|---|---|
| **Code update** (the common case) | Any `.py`, bug fixes, new features, UI tweaks | **One-click in-app update.** Swaps `app/`. No re-download of the zip. |
| **Runtime update** (rare) | Bumped PyQt/pandas, changed bundled deps, edited the launcher itself | Full zip re-download (fallback). Can't be code-swapped because the `.exe` changed. |

Most releases are code updates. Keep a "major update — download here" fallback path
for the rare runtime change, but the one-click path is the default experience.

### 3.3 Version source: GitHub API, not the website

Stop hand-maintaining `version.json`. Use GitHub Releases as the single source of truth:

- Each release is a **GitHub Release** with the `app/` payload (and, for runtime
  updates, the full zip) attached as release assets.
- `updater.py` reads
  `https://api.github.com/repos/Adeen924/FinanceBook/releases/latest`
  and compares the release tag to the local `app/VERSION`.
- No more `version.json` to keep in sync — the release *is* the version source.
- GitHub's unauthenticated API allows 60 req/hr per IP; a once-per-startup daemon
  check is far under that, so it's a non-issue.

**Website's new role:** demoted to a thin human-facing landing/download page that just
links to the latest GitHub release. *Optional indirection:* if the owner ever wants a
self-controlled endpoint (to move off GitHub later, or avoid the rate limit entirely),
keep a tiny `version.json` on `adibmazloom.com` that merely **points to** the GitHub
release rather than storing the binary. Not required for v1.

---

## 4. The one-click update flow (what to build)

1. **Check (startup, daemon thread — keep the existing non-blocking pattern):**
   `updater.py` calls the GitHub `releases/latest` API, parses the tag, compares to
   `app/VERSION`. Never blocks the GUI.
2. **Notify:** if remote > local, show an in-app banner/dialog:
   "Update available (vX.Y.Z) — click to install." (Replaces the current
   "open download_url in browser" behavior.)
3. **Download:** on click, download the new `app/` payload (a zip asset from the
   release) into a temp folder in the background. Show progress.
4. **Hand off + exit:** the app writes out a tiny **helper** (a `.bat` or a small
   bundled side script), tells the user "Restarting to finish update," and quits so
   the files unlock.
5. **Swap + relaunch:** the helper waits for `FinanceBook.exe` to fully exit, replaces
   the `app/` folder with the downloaded one (back up the old one first for rollback),
   then relaunches `FinanceBook.exe`.
6. **Done:** app reopens on the new version. Total user effort: **one click.**

**Implementation notes / gotchas to respect:**
- Verify the download (size/checksum) before swapping; never swap a partial download.
- Keep a backup of the previous `app/` so a failed update can roll back.
- The helper must wait for the parent PID to exit before touching files (lock).
- Preserve the user's database — per `HANDOFF.md`, in a packaged build the DB lives at
  `%APPDATA%\FinanceBook\finances.db`, **outside** the app folder. Confirm the new
  layout keeps the DB there so updates never risk user data. This is critical: it's a
  finance app.
- **Code-signing / SmartScreen:** the `.exe` is unsigned, so the *first* download may
  show "Windows protected your PC." Subsequent one-click updates are quieter because
  they only touch `.py` files, not the signed-status `.exe`. Flag this to the owner as
  a known limitation; a code-signing cert is the paid fix but is out of scope for v1.

---

## 5. GitHub repo setup (how the owner wants this hosted)

Owner's GitHub: `github.com/Adeen924`. Target repo name: `FinanceBook` (confirm).

### 5.1 What goes in the repo
- All app source (`main.py`, `sheets/`, `parsers/`, `utils/`, `ui/`, `updater.py`).
- Build tooling: `run.bat`, `build.bat`, `FinanceBook.spec`, `requirements.txt`.
- `app/VERSION` (or wherever the version marker ends up living).
- A `.github/workflows/release.yml` (see below).
- **Do not commit** the user database, `.venv`, `dist/`, build temp, or any
  `finances.db`. Add a `.gitignore` covering these.

### 5.2 Release automation — use GitHub Actions (Option B)

`HANDOFF.md` left the release-automation choice open (Options A/B/C). With code in a
GitHub repo and releases serving the payload, **Option B (GitHub Actions) is the
chosen fit** — the build and the version-check read from the same place.

The workflow (`.github/workflows/release.yml`) should:
1. Trigger on a pushed tag (e.g. `v1.0.3`) **or** manual "Run workflow" with a version
   + release-notes input.
2. Run on a **Windows runner** (PyInstaller builds are OS-specific; the app is Windows).
3. Install pinned deps from `requirements.txt`, run the PyInstaller build.
4. Produce two assets: the **`app/` payload zip** (for one-click updates) and the
   **full `FinanceBook.zip`** (for first installs + runtime-change fallback).
5. Create/publish a **GitHub Release** for the tag with those assets and the notes.

Net effect: **one tag-push = a shipped update.** The owner's 4-step manual chore
collapses to tagging a release.

### 5.3 Open setup questions to confirm with the owner before building
1. Repo **public or private?** (Public is simplest for the unauthenticated update
   check. A private repo means the updater needs a token — more friction.)
2. Confirm the repo name and that `Adeen924/FinanceBook` is the API path to hardcode.
3. Should the Actions workflow **auto-bump** the `VERSION` file from the tag, or should
   the owner set it manually before tagging? (This is the same open question from
   `HANDOFF.md` — now scoped to the `VERSION` file instead of `updater.py`.)

---

## 6. Things from HANDOFF.md to honor while doing this work

- **Don't break the `FinanceBook.spec` `hiddenimports` debt:** the handoff notes that
  `LoansPage`/`LoanDialog` were **not** yet added to `hiddenimports`. If you touch the
  build, fix this. Any new module the launcher loads must be statically importable.
- **Keep `updater.py`'s daemon-thread, never-block-GUI pattern.** Only the *source* of
  truth (GitHub API) and the *action* (one-click swap vs. browser link) change.
- Follow existing style conventions (PyQt6, QSS constants in `ui/styles.py`,
  table-button padding overrides, comments only where the WHY is non-obvious).

---

## 7. Summary for the next session

Build a **one-click in-app updater** for a **repackaged** FinanceBook:
**thin frozen `.exe` launcher + a replaceable plain-code `app/` folder**, shipped as
**one zip with zero separate installs**. Version checks and distribution move from the
website's hand-maintained `version.json` to **GitHub Releases via the GitHub API**.
Release automation is **GitHub Actions (Option B)** so one tag-push ships an update.
The website becomes a thin download-link page. Protect the `%APPDATA%` database across
updates, keep backups for rollback, and confirm the four open questions in §5.3 with
the owner before writing the workflow.

---

*Prepared for handoff to a Claude Code session. Owner: Adib Mazloom —
amazloom@fiyrpod.com · adibmazloom.com · github.com/Adeen924*
