# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Newsletter - An automated newsletter generation system using Supabase as the database backend. The project leverages patterns from the podscrape2 reference implementation for content processing and generation.

## Database

PostgreSQL database hosted on Supabase with the following tables:
- `feeds` - RSS feed sources
- `episodes` - Processed episodes with transcripts and scores
- `digests` - Generated digest content
- `topics` - Topic configuration with voice settings and instructions
- `tasks` - Task management
- `pipeline_runs` - Pipeline execution tracking
- `web_settings` - Application configuration

### Database Access
```bash
# Using psql (installed via Homebrew)
PGPASSWORD='<password>' psql "postgresql://postgres.dylqxfgdozwjvbiklnfn@aws-1-us-west-1.pooler.supabase.com:6543/postgres"

# Using Supabase REST API with service role key
curl -s "https://dylqxfgdozwjvbiklnfn.supabase.co/rest/v1/<table>" \
  -H "apikey: $SUPABASE_SERVICE_ROLE" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE"
```

## Environment Configuration

Required environment variables in `.env`:
- `OPENAI_API_KEY` - OpenAI API access
- `ELEVENLABS_API_KEY` - ElevenLabs TTS
- `GITHUB_API_TOKEN` / `GH_API_TOKEN` - GitHub access (renamed from GITHUB_TOKEN/GH_TOKEN to avoid gh CLI conflicts)
- `GITHUB_REPOSITORY` - Target repo (format: owner/repo)
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_SERVICE_ROLE` - Service role key for backend operations
- `DATABASE_URL` - PostgreSQL connection string

**Note**: GitHub tokens are named `GITHUB_API_TOKEN` and `GH_API_TOKEN` (not `GITHUB_TOKEN`/`GH_TOKEN`) to prevent conflicts with the gh CLI authentication.

## Development Commands

```bash
# Node.js dependencies
npm install

# Database queries via psql
psql "$DATABASE_URL" -c "SELECT * FROM topics;"

# GitHub CLI (uses stored credentials, not env tokens)
gh repo view
gh release list
```

## Reference Implementation

The `podscrape2-reference/` directory contains a complete RSS podcast digest system that serves as a reference for patterns including:
- 6-phase pipeline architecture (Discovery, Audio, Digest, TTS, Publishing, Retention)
- SQLAlchemy models for PostgreSQL
- Alembic migrations
- Next.js Web UI
- ElevenLabs TTS integration (single-voice and multi-voice dialogue modes)
- GitHub Releases publishing

Key reference files:
- `podscrape2-reference/CLAUDE.md` - Detailed development guidelines
- `podscrape2-reference/src/database/sqlalchemy_models.py` - Database models
- `podscrape2-reference/alembic/` - Migration patterns
