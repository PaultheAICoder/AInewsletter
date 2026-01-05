# Fix Story Arc Integration in Digest Generation

## Problem Summary

Story arcs are being extracted and stored in the database correctly, but **they're never used in digest generation**. The digest script generator still uses the old `episode_topics` system via `topic_tracking_repo`, which stopped being populated on Dec 20, 2025.

### Current State (Broken):
```
run_audio.py → StoryArcExtractor → story_arcs table ✅ (working)
run_digest.py → script_generator.py → topic_tracking_repo → episode_topics table ❌ (stale data)
```

### Target State (Fixed):
```
run_audio.py → StoryArcExtractor → story_arcs table ✅
run_digest.py → script_generator.py → story_arc_repo → story_arcs table ✅
```

---

## Required Changes

### 1. Update `src/generation/script_generator.py`

#### A. Replace the repository import and initialization

**Current code (around line 30-40):**
```python
from src.database.topic_tracking_repo import get_topic_tracking_repo
# ...
self.topic_tracking_repo = get_topic_tracking_repo()
```

**Replace with:**
```python
from src.database.story_arc_repo import get_story_arc_repo
# ...
self.story_arc_repo = get_story_arc_repo()
```

#### B. Replace `_get_recent_topic_history()` method (around line 230-282)

**Current code:**
```python
def _get_recent_topic_history(self, digest_topic: str, days_back: int = None) -> str:
    if not self.topic_tracking_repo:
        return ""
    # ... uses topic_tracking_repo.get_topics_last_n_days()
```

**Replace with this new implementation:**
```python
def _get_recent_story_arc_context(self, digest_topic: str, days_back: int = None) -> str:
    """
    Retrieve active story arcs for deduplication and context.

    Args:
        digest_topic: Name of the digest topic (e.g., "AI and Technology")
        days_back: Number of days to look back (default from config)

    Returns:
        Formatted string of active story arcs for GPT prompt
    """
    if not self.story_arc_repo:
        logger.debug("Story arc repository unavailable, skipping context")
        return ""

    # Get lookback window from config
    if days_back is None:
        if self.web_config:
            days_back = self.web_config.get_setting(
                SettingsKeys.TopicTracking.CATEGORY,
                SettingsKeys.TopicTracking.RETENTION_DAYS,
                14
            )
        else:
            days_back = 14

    try:
        # Get story arcs that haven't been included yet
        arcs = self.story_arc_repo.get_story_arcs_for_digest(
            digest_topic=digest_topic,
            min_events=2,           # Only arcs with multiple events
            exclude_included=False  # Include all for context (mark after generation)
        )

        if not arcs:
            logger.debug(f"No active story arcs found for {digest_topic}")
            return ""

        # Format arcs for GPT prompt
        context = f"\n\n## ACTIVE STORY ARCS (Last {days_back} days)\n"
        context += "The following story arcs are being tracked. Consider including significant developments:\n\n"

        for arc in arcs[:15]:  # Limit to top 15 arcs
            arc_name = arc.get('arc_name', '')
            category = arc.get('functional_category', 'other')
            event_count = arc.get('event_count', 0)
            source_count = arc.get('source_count', 0)

            context += f"### {arc_name}\n"
            context += f"Category: {category} | Events: {event_count} | Sources: {source_count}\n"

            # Include recent events for context
            events = arc.get('events', [])
            if events:
                context += "Recent developments:\n"
                for event in events[-3:]:  # Last 3 events
                    summary = event.get('event_summary', '')
                    perspective = event.get('perspective', 'neutral')
                    source = event.get('source_name', 'Unknown')
                    context += f"- [{perspective}] {summary} (via {source})\n"
            context += "\n"

        # Add instruction about avoiding already-included arcs
        included_arcs = [a for a in arcs if a.get('included_in_digest_id')]
        if included_arcs:
            context += f"\n**Note:** {len(included_arcs)} arcs were already included in recent digests. "
            context += "Focus on NEW developments in these stories or arcs not yet covered.\n"

        logger.info(f"Retrieved {len(arcs)} story arcs for {digest_topic} context")
        return context

    except Exception as e:
        logger.warning(f"Failed to retrieve story arc context for {digest_topic}: {e}")
        return ""
```

#### C. Update prompt building to use story arcs

In `generate_dialogue_script()` and `generate_narrative_script()` methods, replace:
```python
topic_history = self._get_recent_topic_history(topic)
```

With:
```python
story_arc_context = self._get_recent_story_arc_context(topic)
```

And update the prompt template to use `{story_arc_context}` instead of `{topic_history}`.

---

### 2. Mark Story Arcs as Included After Generation

After a digest is successfully generated and saved, mark the story arcs that were covered.

#### Add this method to `ScriptGenerator` class:

```python
def mark_covered_story_arcs(self, digest_id: int, digest_topic: str, script_content: str):
    """
    Mark story arcs as included in the digest based on content analysis.

    Args:
        digest_id: The database ID of the generated digest
        digest_topic: The topic name
        script_content: The generated script content
    """
    if not self.story_arc_repo:
        return

    try:
        # Get all active arcs
        arcs = self.story_arc_repo.get_story_arcs_for_digest(
            digest_topic=digest_topic,
            min_events=1,
            exclude_included=True  # Only unincluded arcs
        )

        # Check which arcs are mentioned in the script
        script_lower = script_content.lower()
        arcs_marked = 0

        for arc in arcs:
            arc_name = arc.get('arc_name', '')
            # Check if arc name or key terms appear in script
            if arc_name.lower() in script_lower:
                self.story_arc_repo.mark_arc_included(
                    arc_id=arc['id'],
                    digest_id=digest_id
                )
                arcs_marked += 1
                logger.debug(f"Marked arc '{arc_name}' as included in digest {digest_id}")

        if arcs_marked > 0:
            logger.info(f"Marked {arcs_marked} story arcs as included in digest {digest_id}")

    except Exception as e:
        logger.warning(f"Failed to mark story arcs as included: {e}")
```

#### Call this method after saving the digest:

In `run_digest.py` or wherever the digest is saved, add:
```python
# After saving digest to database
self.script_generator.mark_covered_story_arcs(
    digest_id=digest.id,
    digest_topic=topic_name,
    script_content=script_content
)
```

---

### 3. Update `story_arc_repo.py` (if method doesn't exist)

Ensure the repository has this method:

```python
def mark_arc_included(self, arc_id: int, digest_id: int) -> None:
    """
    Mark a story arc as included in a digest.

    Args:
        arc_id: Story arc database ID
        digest_id: Digest database ID
    """
    now = datetime.now(timezone.utc)

    with self.db_manager.get_session() as session:
        session.execute(
            text("""
                UPDATE story_arcs
                SET included_in_digest_id = :digest_id,
                    included_at = :now,
                    updated_at = :now
                WHERE id = :arc_id
            """),
            {"digest_id": digest_id, "now": now, "arc_id": arc_id}
        )
        session.commit()
```

---

### 4. Remove Old `topic_tracking_repo` Usage

After implementing the above, search for and remove any remaining references to:
- `topic_tracking_repo`
- `get_topics_last_n_days()`
- `episode_topics` table access in digest generation

---

## Testing Checklist

1. **Verify story arcs appear in prompt context:**
   ```bash
   python3 scripts/run_digest.py --dry-run --verbose
   ```
   Look for log message: `Retrieved X story arcs for AI and Technology context`

2. **Check that story arcs are marked after generation:**
   ```sql
   SELECT arc_name, included_in_digest_id, included_at
   FROM story_arcs
   WHERE included_in_digest_id IS NOT NULL
   ORDER BY included_at DESC LIMIT 10;
   ```

3. **Verify no more `episode_topics` references:**
   ```bash
   grep -r "episode_topics" src/generation/
   grep -r "topic_tracking_repo" src/generation/
   ```

---

## Database Schema Reference

### `story_arcs` table
| Column | Type | Description |
|--------|------|-------------|
| id | int | Primary key |
| arc_name | text | Human-readable story name |
| arc_slug | text | Normalized slug for matching |
| functional_category | text | model_release, company_strategy, etc. |
| digest_topic | text | Parent topic (e.g., "AI and Technology") |
| event_count | int | Number of events in timeline |
| source_count | int | Number of unique source feeds |
| included_in_digest_id | int | FK to digests table (NULL if not included) |
| included_at | timestamp | When arc was included in digest |
| last_updated_at | timestamp | Last event added |

### `story_arc_events` table
| Column | Type | Description |
|--------|------|-------------|
| id | int | Primary key |
| story_arc_id | int | FK to story_arcs |
| event_date | timestamp | When event occurred |
| event_summary | text | 1-2 sentence summary |
| key_points | text[] | Array of specific details |
| source_name | text | Episode/feed title |
| perspective | text | positive, negative, neutral, analytical |

---

## Summary

The key changes are:
1. **Import `story_arc_repo` instead of `topic_tracking_repo`**
2. **Use `get_story_arcs_for_digest()` to get arc context**
3. **Call `mark_arc_included()` after digest generation**
4. **Remove all `episode_topics` references from digest generation**

This will fix the disconnect between story arc extraction (working) and digest generation (currently using stale data).
