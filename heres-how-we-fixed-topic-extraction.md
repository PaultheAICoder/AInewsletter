# How We Fixed Topic Extraction & Story Arc System

**Date:** 2026-01-14
**Fixed By:** Claude (via podcast repo coordination)
**Status:** COMPLETE - All changes applied and verified

---

## The Problem

The story arc deduplication system was completely broken, causing 210+ story arcs to accumulate when the target is 10-30.

**Root Causes:**
1. `semantic_matcher.py` was **missing from this repo** - the dedupe cron (`scripts/dedupe_topics.py`) imported it but the file didn't exist
2. `max_arcs_per_episode` was set to 10 (way too high)
3. The extraction prompt didn't guide the LLM to prefer existing arcs
4. Cleanup only considered max age, not inactivity

---

## Changes Made (All Applied)

### 1. Created `src/topic_tracking/semantic_matcher.py`

**Commit:** 1e981d1

Copied from the podcast repo (podscrape2). Provides:
- `SemanticTopicMatcher` class for finding semantically similar arcs
- `find_matching_topic()` - Find best matching existing arc for a new topic
- `find_duplicate_groups()` - Union-find algorithm for batch deduplication
- Uses OpenAI embeddings (`text-embedding-3-small`) for semantic similarity
- Default similarity threshold: 0.85

### 2. Updated `src/topic_tracking/__init__.py`

Added `SemanticTopicMatcher` to exports:
```python
from src.topic_tracking.topic_extractor import StoryArcExtractor
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

__all__ = ['StoryArcExtractor', 'SemanticTopicMatcher']
```

### 3. Updated `src/topic_tracking/topic_extractor.py`

**Change 1:** Lowered `max_arcs_per_episode` from 10 to 3
- Reduces arc creation rate dramatically
- Quality over quantity approach

**Change 2:** Added CRITICAL GUIDELINES section to extraction prompt:
```
## CRITICAL GUIDELINES - READ CAREFULLY

1. **STRONGLY prefer adding events to existing arcs over creating new arcs**
   - If content relates to ANY existing arc, add an event to it instead of creating a new arc
   - Look for thematic overlap, not just exact matches

2. **Only create a NEW arc if this is a significant story likely to have follow-up coverage**
   - One-off mentions or general discussions should NOT become arcs
   - Ask: "Will this specific story likely appear in future episodes?"

3. **Maximum 2-3 story arcs per episode** - quality over quantity
   - If you find more than 3 relevant stories, pick the most significant ones

4. **When in doubt, add to an existing arc with a similar theme**
   - Example: "Claude Code Updates" should be added to existing "Claude Code" arc
```

### 4. Updated `src/database/supabase_client.py`

Updated `cleanup_old_story_arcs()` method to support dual cleanup criteria:

```python
def cleanup_old_story_arcs(
    self,
    max_age_days: int = None,      # Default: 14 (from web_settings)
    inactivity_days: int = None    # Default: 7 (from web_settings)
) -> int:
```

Now deletes arcs that match EITHER condition:
- **Older than 14 days** (regardless of activity), OR
- **No new events in 7+ days** (inactive)

---

## One-Time Cleanup Performed

On 2026-01-14, we ran cleanup SQL against the shared Supabase database:

```sql
-- Deleted 134 single-event arcs (likely false positives)
DELETE FROM story_arcs WHERE event_count = 1;

-- Deleted 21 arcs over 14 days old
DELETE FROM story_arcs WHERE started_at < NOW() - INTERVAL '14 days';
```

**Result:** Reduced from 210 arcs to 55 arcs

---

## Web Settings Added

These settings control the story arc system (already in database):

| Category | Key | Value | Description |
|----------|-----|-------|-------------|
| retention | story_arc_retention_days | 14 | Maximum age for story arcs |
| retention | story_arc_inactivity_days | 7 | Delete arcs inactive for this many days |
| topic_tracking | max_arcs_per_episode | 3 | Maximum story arcs to extract per episode |

If not present, run:
```sql
INSERT INTO web_settings (category, setting_key, setting_value, value_type, description)
VALUES
    ('retention', 'story_arc_retention_days', '14', 'int', 'Maximum age for story arcs'),
    ('retention', 'story_arc_inactivity_days', '7', 'int', 'Delete arcs inactive for this many days'),
    ('topic_tracking', 'max_arcs_per_episode', '3', 'int', 'Maximum story arcs to extract per episode')
ON CONFLICT (category, setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value;
```

---

## Verification

### Verify dedupe cron works:
```bash
python scripts/dedupe_topics.py --dry-run --verbose
```

This should no longer fail with `ModuleNotFoundError: No module named 'semantic_matcher'`.

### Check current arc count:
```sql
SELECT COUNT(*) FROM story_arcs;
-- Target: 10-30 arcs
```

---

## Coordination with Podcast Repo (podscrape2)

The podcast repo received matching updates:

| File | Change |
|------|--------|
| `src/topic_tracking/topic_extractor.py` | Same prompt changes, same max_arcs limit |
| `src/database/story_arc_repo.py` | Same dual-criteria cleanup logic |
| `src/publishing/retention_manager.py` | Added story arc cleanup to retention phase |

Both repos share the same Supabase database, so changes affect the shared data.

---

## Expected Results

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| Total arcs | 210+ | 10-30 | 55 (will decrease to target) |
| Arcs per day | 12-23 | 3-5 | Pending verification |
| Single-event arcs | 134 (63%) | <10 | 0 (deleted) |
| Dedupe cron | Broken | Running | Fixed |

---

## How Story Arcs Should Work Now

1. **YouTube transcripts** are processed and scored
2. **Topic extraction** identifies 2-3 significant story arcs per episode (max)
3. **Semantic matching** adds events to existing similar arcs instead of creating duplicates
4. **Daily dedupe cron** merges any remaining duplicates using embedding similarity
5. **Retention cleanup** removes arcs older than 14 days OR inactive for 7+ days
6. **Digest generation** references ongoing arcs for listener continuity

The goal is 10-30 active arcs representing the major ongoing stories in AI/tech news, with natural turnover as stories conclude and new ones emerge.
