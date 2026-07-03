# 📝 Changelog

All notable changes to Voice Summary will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Sarvam-only AI pipeline** (Saaras v3 diarized STT → forced tool-calling structured analysis,
  decomposed + map-reduce chunked → translation + memory), 3-key rotation; analysis on **sarvam-105b**
- **Auditable evidence** on every score dimension: supporting transcript quote + timestamp + speaker
  (`breakdown[].evidence` in `/score`), resolved from model-cited turn numbers
- **Durable pipeline** — `processing_jobs` table + startup **crash-recovery** (replaces fragile BackgroundTasks data-loss)
- **Governance guardrails** — `GOVERNANCE.md` (DPDP consent, no automated adverse decisions, Goodhart),
  + "coaching aid, not a verdict" + consent disclaimer in the Score tab
- **Validation gate** — `gold_set_eval.py` (per-dimension MAE/correlation vs human raters) +
  `config/score_dimensions.json` per-dimension status (validated/beta/hidden) gating `/score`
- **Transcript-quality flag** (`transcript_quality`: ok/low/failed) surfaced in `/score`

### Changed / Removed (production cleanup for `main`)
- **Sarvam-only**: removed all OpenAI / NVIDIA / Groq / Whisper code paths, config fields, and `.env` keys
- **Removed the agent-comparison feature** (router, ~14 util/integration modules, 3 DB models, schemas, frontend) — a separate product, out of scope for the telecaller pipeline
- **Removed legacy Bolna/extraction endpoints** from `calls.py` (create/process-full/process-audio/analyze-agent/data-pipeline/extracted-data) + their utils + `CallExtractedData` model/schemas
- Slimmed `requirements.txt` (dropped librosa/matplotlib/openai/pyyaml; added `sarvamai`)
- **Single integration guide**: `BACKEND_INTEGRATION.md` supersedes AI_HANDOVER/BACKEND_MODULES/HANDOVER_NOTE (removed); dropped open-source boilerplate docs
- Moved one-off scripts to `scripts/`; gitignored runtime data (`local_storage/`, `Audio/`); production-grade `README.md`

### Deferred (pre-scale / AWS phase)
- Indexed `contact_key` column + query refactor to replace full-scan grouping (premature at current volume; see GOVERNANCE.md)
- **Consolidated Score-tab endpoint** `GET /api/calls/{call_id}/score` — one payload
  for the Call Detail "Score" screen: hero composite Call Score, four rings
  (Overall / Telecaller / Lead Quality / Sentiment) each with a per-call trend arrow,
  the 5-dimension breakdown with one-line notes, and a sentiment timeline + caption
- Per-dimension coaching notes in `agent_debrief` (`opening_note` … `closing_note`)
- Deterministic Score helpers in `lead_intelligence.py`: `sentiment_score`,
  `sentiment_timeline`, `call_score` (composite, tunable via `CALL_SCORE_WEIGHTS`), `score_trend`
- AI-powered transcript analysis capabilities
- Intelligent data extraction pipeline
- Smart classification and labeling features
- Advanced audio processing with pause detection
- Real-time timeline visualization
- Comprehensive API documentation

### Changed
- Enhanced README with SEO optimization
- Improved project structure and organization
- Updated dependencies to latest versions

### Fixed
- Various bug fixes and improvements

## [0.1.0] - 2025-01-XX

### Added
- Initial release of Voice Summary
- FastAPI backend with PostgreSQL database
- React/Next.js frontend with TypeScript
- Audio processing and analysis capabilities
- S3 integration for file storage
- Basic transcript processing
- Call management API endpoints
- Real-time audio analysis
- Conversation health scoring
- Pause detection and speech segmentation
- Turn-by-turn conversation analysis
- Audio file upload and processing
- Database migrations with Alembic
- Environment configuration management
- Development and production setup scripts

### Features
- **Core Platform**: FastAPI + React + PostgreSQL architecture
- **Audio Processing**: Advanced audio analysis with librosa
- **Data Storage**: Robust PostgreSQL database with migrations
- **File Management**: AWS S3 integration for audio storage
- **API Documentation**: Automatic OpenAPI/Swagger documentation
- **Frontend Dashboard**: Modern React interface with real-time updates
- **Development Tools**: Comprehensive setup and development scripts

### Technical Stack
- **Backend**: Python 3.9+, FastAPI, SQLAlchemy, PostgreSQL
- **Frontend**: React 18+, Next.js 14+, TypeScript, Tailwind CSS
- **Audio Processing**: Librosa, SciPy, WebRTC VAD
- **Cloud**: AWS S3, OpenAI API integration
- **Development**: UV package manager, Alembic migrations

---

## Version History

- **0.1.0**: Initial release with core functionality
- **Future**: Planned AI enhancements and advanced features

## Contributing to Changelog

When contributing to Voice Summary, please update this changelog with:

1. **New Features**: Under "Added" section
2. **Bug Fixes**: Under "Fixed" section  
3. **Breaking Changes**: Under "Changed" section
4. **Deprecations**: Under "Deprecated" section

Use the same format as existing entries and include:
- Clear, concise descriptions
- Issue/PR references when applicable
- Breaking change notes when necessary

---

**For detailed release notes, see our [GitHub releases](https://github.com/yourusername/voicesummary/releases).**
