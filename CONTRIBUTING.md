# Contributing to Nna

Thanks for your interest in improving Nna! This guide explains how to run the
project locally, report bugs, submit pull requests, and the coding standards we
follow.

## Table of Contents

- [Running the Project](#running-the-project)
- [Reporting Bugs](#reporting-bugs)
- [Submitting Pull Requests](#submitting-pull-requests)
- [Coding Standards](#coding-standards)

## Running the Project

### Prerequisites

- Python 3.10 or newer
- `ffmpeg` installed and available on your `PATH`
  - Linux / macOS: `sudo apt install ffmpeg` / `brew install ffmpeg`
- (Optional) Git

### Local setup

```bash
# 1. Clone the repository
git clone https://github.com/ahsansdr11-spec/Nna.git
cd Nna

# 2. Install dependencies
#    (only needed the first time, or when requirements.txt changes)
pip install -r requirements.txt

# 3. Run the app
python app.py
# open http://localhost:5000
```

### Running with Docker

```bash
docker build -t nna .
docker run -p 5000:5000 -e PORT=5000 nna
```

### Deploying (Render / Railway)

The project ships a `Dockerfile`, so any container platform works:

- **Railway:** New Project → Deploy from GitHub repo (auto-detects the Dockerfile).
- **Render:** New → Web Service → select the repo (auto-detects the Dockerfile).

For persistent data (accounts, history, playlists) mount a volume at `/data`
and set the `DATA_DIR=/data` environment variable.

## Reporting Bugs

Before opening a bug report, please:

1. Search the [existing issues](https://github.com/ahsansdr11-spec/Nna/issues)
   to avoid duplicates.
2. Try the latest version and, if possible, a different network or browser.

When you file a bug, use the **Bug Report** template and include:

- A clear, descriptive title.
- Steps to reproduce (paste the URL you used, if relevant).
- What you expected to happen and what actually happened.
- Your environment: OS, Python version, browser, and whether you ran it
  locally, via Docker, or on Render / Railway.
- Logs or screenshots (redact any personal data).

You can also use the in-app "Lapor Bug / Feedback" page from the
**Cara Pakai** tab — reports there are stored in the database and reviewed by
the maintainer.

## Submitting Pull Requests

1. Fork the repository and create your branch from `main`.
2. Set up the project locally (see [Running the Project](#running-the-project)).
3. Make your changes, keeping them focused and atomic.
4. Run a quick sanity check:
   ```bash
   python -m py_compile app.py api/index.py yt_dlp_plugins/getpot.py
   for f in yt_dlp_plugins/extractor/*.py; do python -m py_compile "$f"; done
   ```
5. Make sure your commit messages are clear and descriptive.
6. Open a pull request using the PR template and fill in every section.
7. Describe **what** changed and **why**, and link any related issue
   (e.g. `Closes #12`).
8. Be responsive to review feedback.

## Coding Standards

- **Language:** Python 3.10+. Keep code readable and well-organized.
- **Style:** Follow [PEP 8](https://peps.python.org/pep-0008/). A line length
  of ~100 characters is acceptable.
- **Formatting:** Use consistent 4-space indentation. No tabs.
- **Imports:** Group imports (standard library, third-party, local) with a
  blank line between groups.
- **Typing:** Add type hints where reasonable.
- **Comments:** Write comments in English for shared code; the user-facing
  README and docs may stay in Indonesian.
- **Commits:** Use clear, imperative commit messages
  (e.g. `Add retry on YouTube client rotation`).
- **Tests:** If you add logic, add or update a test when practical.
- **No secrets:** Never commit tokens, cookies, or credentials.
- **Dependencies:** Pin versions in `requirements.txt` and explain why a pin
  is needed (see the existing `curl_cffi` note).

## Code of Conduct

Be respectful and constructive. We want Nna to be a welcoming project for
everyone.

---

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
