# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [1.2.0] - 2026-08-06
### Added
- News aggregator with 15 RSS sources across 6 categories.
- Manga reader via MangaDex (search, genre filter, popular recommendations).
- Music search & streaming via YouTube Music (play, download per-track or all).
- Gallery downloads (Instagram / X / Facebook / TikTok slideshows) as ZIP.
- In-app "Lapor Bug / Feedback" page.

### Changed
- Layered YouTube anti-block strategy (14-client rotation, PO Token, Piped / Invidious fallback).
- Improved background downloads with progress polling.

## [1.1.0] - 2026-07-15
### Added
- `Dockerfile` for one-click container deployment (Render / Railway).
- Support for additional platforms (Threads, Snapchat, Reddit, Douyin, Rutube, etc.).
- Persistent data volume support via `DATA_DIR`.

### Changed
- Default resolution raised to 1080p across platforms.
- Added browser impersonation (`curl_cffi`) to bypass TLS / anti-bot blockers.

## [1.0.0] - 2026-06-01
### Added
- Initial public release of Nna (KINGS DOWNLOADER).
- Web UI built with Flask + yt-dlp.
- Core downloader supporting YouTube, TikTok, Instagram, Facebook, X, Pinterest, Spotify, and more.
- Resolution picker, MP3 / M4A / best-video modes, and a raw format list.
- Background downloads with a progress bar.

[1.2.0]: https://github.com/ahsansdr11-spec/Nna/releases/tag/v1.2.0
[1.1.0]: https://github.com/ahsansdr11-spec/Nna/releases/tag/v1.1.0
[1.0.0]: https://github.com/ahsansdr11-spec/Nna/releases/tag/v1.0.0
