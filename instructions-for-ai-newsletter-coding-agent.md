# Story Arc System Fixes Applied

**Date:** 2026-01-14
**Applied By:** Claude (via podcast repo)

## Summary

The story arc deduplication cron (`dedupe_topics.py`) was completely broken because `semantic_matcher.py` was missing from the repo. This caused 210+ story arcs to accumulate when the target is 10-30.

## Changes Made

### 1. Created `src/topic_tracking/semantic_matcher.py`

- Copied from the podcast repo (podscrape2)
- Provides `SemanticTopicMatcher` class for:
  - `find_matching_topic()` - Find semantically similar arcs
  - `find_duplicate_groups()` - Union-find for deduplication
- Uses OpenAI embeddings (text-embedding-3-small)

### 2. Updated `src/topic_tracking/__init__.py`

- Added `SemanticTopicMatcher` to exports
- Now exports both `StoryArcExtractor` and `SemanticTopicMatcher`

### 3. Updated `src/topic_tracking/topic_extractor.py`

**Change 1:** Lowered `max_arcs_per_episode` from 10 to 3
- Reduces arc creation rate
- Quality over quantity

**Change 2:** Added CRITICAL GUIDELINES to extraction prompt:
1. STRONGLY prefer adding events to existing arcs over creating new ones
2. Only create NEW arc if story will likely have follow-up coverage
3. Maximum 2-3 story arcs per episode
4. When in doubt, add to existing arc with similar theme

### 4. Updated `src/database/supabase_client.py`

Updated `cleanup_old_story_arcs()` method:
- Now accepts `max_age_days` (default: 14) and `inactivity_days` (default: 7)
- Deletes arcs that are:
  - Older than 14 days (regardless of activity), OR
  - Haven't had events in 7+ days

## Actions Still Required

### 1. Add web_settings (run once)

```sql
INSERT INTO web_settings (category, setting_key, setting_value, value_type, description)
VALUES
    ('retention', 'story_arc_retention_days', '14', 'int', 'Maximum age for story arcs'),
    ('retention', 'story_arc_inactivity_days', '7', 'int', 'Delete arcs inactive for this many days'),
    ('topic_tracking', 'max_arcs_per_episode', '3', 'int', 'Maximum story arcs to extract per episode')
ON CONFLICT (category, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value;
```

### 2. One-time cleanup (run after deployment)

```sql
-- Delete single-event arcs (likely false positives)
DELETE FROM story_arcs WHERE event_count = 1;

-- Delete arcs over 14 days old
DELETE FROM story_arcs WHERE started_at < NOW() - INTERVAL '14 days';
```

### 3. Verify cron works

```bash
python scripts/dedupe_topics.py --dry-run --verbose
```

## Coordination with Podcast Repo

The podcast repo (podscrape2) also needs updates:
- Add story arc cleanup to `retention_manager.py`
- Same changes to `topic_extractor.py` (lower max_arcs, better prompts)
- Fix `mark_covered_story_arcs()` in `script_generator.py` (use embeddings instead of substring match)
- Add continuity framing to digest generation prompts

See `fix-story-arc.md` in the podcast repo for the complete cross-repo plan.

## Expected Results

| Metric | Before | After |
|--------|--------|-------|
| Total arcs | 210+ | 10-30 |
| Arcs per day | 12-23 | 3-5 |
| Single-event arcs | 134 (63%) | <10 |
| Dedupe cron | Broken | Running daily |
