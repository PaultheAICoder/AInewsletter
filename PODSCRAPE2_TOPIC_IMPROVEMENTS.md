# Topic Extraction and Deduplication Improvements

## Overview

This document describes improvements made to the topic extraction system in the AInewsletter project that should be replicated in podscrape2. The core problem being solved is **preventing duplicate topics** when semantically similar topics are extracted with slightly different names (e.g., "GPT-5 Release" vs "Upcoming GPT-5 Release" vs "GPT-5.2 Announcement").

## Problem Statement

The original topic extraction system had a fundamental flaw:

1. **Extraction** creates new topics based on GPT's output with only exact slug matching
2. **Novelty detection** only compares topics with identical slugs
3. Result: Semantically identical topics get created as duplicates because their names/slugs differ slightly

Example of the problem:
- Episode 1 extracts topic: "GPT-5 Release" (slug: `gpt-5-release`)
- Episode 2 extracts topic: "Upcoming GPT-5.2 Release" (slug: `upcoming-gpt-52-release`)
- Episode 3 extracts topic: "GPT-5.2 Announcement" (slug: `gpt-52-announcement`)

All three are essentially the same topic but get stored as separate entries because their slugs don't match.

## Solution Architecture

The solution has two components:

### 1. Prevention: Semantic Matching During Extraction

**Before creating a new topic**, check if a semantically similar topic already exists using embedding similarity.

Key changes to `TopicExtractor`:

```python
# 1. Initialize semantic matcher
self.semantic_matcher = SemanticTopicMatcher(similarity_threshold=0.85)

# 2. In extract_and_store_topics():
# a) Fetch existing topics (30 days lookback)
existing_topics = self.db.get_recent_episode_topics(digest_topic=digest_topic, days=30)

# b) Include existing topic names in GPT prompt
existing_topic_names = self.semantic_matcher.get_topic_names_for_prompt(existing_topics)

# c) Before creating each topic, check for semantic match
matched_topic = self.semantic_matcher.find_matching_topic(
    new_topic_name=topic_name,
    new_key_points=key_points,
    existing_topics=existing_topics,
    digest_topic=digest_topic
)

if matched_topic:
    # Use existing topic's slug and name
    # Only add NEW key points not already present
    # Set is_update=True and link parent_topic_id
else:
    # Create as new topic
```

**GPT Prompt Enhancement:**
```
IMPORTANT - Existing Topics to Reuse:
The following topics already exist in our database. If any topic in this transcript
matches one of these (even if phrased differently), USE THE EXISTING TOPIC NAME:
- GPT-5 Release
- OpenAI Leadership Crisis
- Claude 3.5 Sonnet Launch
...

When you find content about an existing topic:
- Use the EXACT existing topic name (don't create a variation)
- Mark is_update=true
- Add only NEW key points not covered before
```

### 2. Cleanup: Daily Deduplication Script

Even with prevention, some duplicates may slip through. A daily dedupe process consolidates them:

```bash
python scripts/dedupe_topics.py [--dry-run] [--digest-topic NAME]
```

The script:
1. Fetches all topics for each digest_topic (30 day window)
2. Uses embedding similarity to find groups of duplicate topics
3. For each group:
   - Keeps the canonical topic (oldest by first_mentioned_at)
   - Merges unique key points from duplicates into canonical
   - Deletes duplicate records
4. Logs all operations for audit

## New Files to Create

### `src/topic_tracking/semantic_matcher.py`

```python
class SemanticTopicMatcher:
    """
    Finds semantically similar topics using embedding similarity.
    Uses text-embedding-3-small for cost-effective comparisons.
    """

    def __init__(self, similarity_threshold: float = 0.85):
        # 0.85 = 85% similar means same topic

    def find_matching_topic(
        self,
        new_topic_name: str,
        new_key_points: List[str],
        existing_topics: List[Dict],
        digest_topic: str = None
    ) -> Optional[TopicMatch]:
        """
        Find best matching existing topic for a new topic.
        Returns TopicMatch if similarity >= threshold, None otherwise.
        """

    def find_duplicate_groups(
        self,
        topics: List[Dict],
        similarity_threshold: float = None
    ) -> List[List[Dict]]:
        """
        Find groups of duplicate topics using union-find.
        Returns list of groups, each sorted by age (oldest first).
        """

    def get_topic_names_for_prompt(
        self,
        existing_topics: List[Dict],
        max_topics: int = 50
    ) -> str:
        """
        Generate formatted list of existing topic names for GPT prompt.
        Dedupes by slug and returns most recent unique names.
        """
```

Key implementation details:
- Uses `text-embedding-3-small` model (fast, cheap)
- Creates text representation: `topic_name + " " + " ".join(key_points)`
- Cosine similarity for comparison
- Caches embeddings to reduce API calls
- Union-find algorithm for grouping duplicates

### `scripts/dedupe_topics.py`

Daily cleanup script that:
1. Gets topics with `enable_topic_tracking=true`
2. For each digest_topic:
   - Fetches recent topics (configurable, default 30 days)
   - Finds duplicate groups using embedding similarity
   - Merges duplicates into canonical (oldest)
   - Deletes duplicate records
3. Logs comprehensive statistics

## Database Methods to Add

```python
def delete_episode_topic(self, topic_id: int) -> bool:
    """Delete an episode topic by ID."""

def update_episode_topic_key_points(self, topic_id: int, key_points: List[str]) -> None:
    """Update key points for an episode topic."""
```

## Configuration

The semantic matching uses these thresholds:

| Setting | Value | Purpose |
|---------|-------|---------|
| `similarity_threshold` | 0.85 | Consider topics as duplicates if 85%+ similar |
| `lookback_days` | 30 | Check against topics from last 30 days |
| `max_key_points` | 6 | Maximum key points after merging |

These can be made configurable via web_settings if needed.

## Key Behavior Changes

### Before
- GPT extracts topics with whatever names it chooses
- New topics created unless exact slug match exists
- Duplicate topics accumulate over time

### After
- GPT receives list of existing topic names and is instructed to reuse them
- New topics checked against existing via embedding similarity
- Matching topics add new key points instead of creating duplicates
- Daily dedupe catches any that slip through
- Topics consolidate information over time instead of fragmenting

## Implementation Checklist for podscrape2

1. [ ] Create `src/topic_tracking/semantic_matcher.py` with `SemanticTopicMatcher` class
2. [ ] Update `TopicExtractor.__init__()` to initialize `SemanticTopicMatcher`
3. [ ] Update `TopicExtractor.extract_and_store_topics()`:
   - [ ] Fetch existing topics (30 days)
   - [ ] Generate existing topic names for prompt
   - [ ] Check semantic match before creating each topic
   - [ ] If match found: use existing name/slug, merge key points, set is_update=True
   - [ ] If no match: create as new topic (existing behavior)
4. [ ] Update `TopicExtractor._create_extraction_prompt()`:
   - [ ] Accept `existing_topic_names` parameter
   - [ ] Include existing topics section in prompt
   - [ ] Emphasize reusing existing topic names
5. [ ] Add database methods:
   - [ ] `delete_episode_topic(topic_id)`
   - [ ] `update_episode_topic_key_points(topic_id, key_points)`
6. [ ] Create `scripts/dedupe_topics.py`
7. [ ] Add to daily cron (after main pipeline)

## Testing

To verify the implementation:

```bash
# Dry run to see what would be deduplicated
python scripts/dedupe_topics.py --dry-run --verbose

# Test on single digest topic
python scripts/dedupe_topics.py --dry-run --digest-topic "AI and Technology"

# Run actual deduplication
python scripts/dedupe_topics.py --verbose
```

## Expected Outcomes

1. **Fewer duplicate topics**: Topics like "GPT-5 Release" and "GPT-5.2 Announcement" get consolidated
2. **Richer key points**: Information from multiple episodes gets merged into canonical topics
3. **Better tracking**: Evolution of topics over time becomes clearer
4. **Cleaner digests**: Newsletter generation has less redundancy to filter

## Questions to Resolve

1. Should similarity_threshold be configurable in web_settings?
2. Should we add a "merged_from" audit trail when consolidating?
3. Do we need to update any digest generation logic to handle the new structure?

---

*Generated from AInewsletter implementation on 2024-12-13*
