# Story Arc Implementation Guide for Podscrape2

## Overview

This document describes how to update podscrape2 to use the new **Story Arc** system instead of the previous topic extraction approach. Story arcs track evolving news narratives over time, capturing multiple perspectives from different sources.

**Key Changes:**
- Topics → Story Arcs (evolving narratives, not isolated topics)
- Hardcoded keyword patterns → LLM-driven story recognition
- Per-episode topics → Timeline events with perspectives
- `episode_topics` table → `story_arcs` + `story_arc_events` tables
- Web UI: Recurring Topics page → Story Arcs page

## Why This Change?

The previous system had fundamental limitations:

1. **Hardcoded keywords** (like "openai", "gpt-5") couldn't recognize new stories
2. **Semantic matching** couldn't connect conceptually related but differently-phrased updates
3. **No timeline tracking** - couldn't see how a story evolved over time
4. **No perspective tracking** - couldn't see different viewpoints on the same story

**Example of the problem:**
- Week 1: "OpenAI Code Red" (treated as new topic)
- Week 2: "GPT-5 Development Accelerated" (treated as new topic)
- Week 3: "GPT-5.2 Released" (treated as new topic)
- Week 4: "GPT-5.2 Benchmark Results" (treated as new topic)

These are all **one story** - "OpenAI's GPT-5 Development" - but the old system couldn't connect them.

**With story arcs:**
```
Story Arc: "OpenAI's GPT-5 Development"
Category: model_release
Timeline:
  - [Dec 5] OpenAI declares "Code Red" in response to Gemini
  - [Dec 8] Reports emerge of accelerated GPT-5 development
  - [Dec 12] GPT-5.2 released ahead of schedule
  - [Dec 15] Benchmarks show 15% improvement over GPT-5
```

## Database Schema (Already Created)

The database already has two new tables. Podscrape2 shares the same Supabase database, so no migration needed.

### `story_arcs` Table
```sql
CREATE TABLE story_arcs (
    id SERIAL PRIMARY KEY,
    arc_name VARCHAR(512) NOT NULL,           -- "OpenAI's GPT-5 Development"
    arc_slug VARCHAR(255) NOT NULL,           -- "openais-gpt-5-development"
    functional_category VARCHAR(50) NOT NULL, -- model_release, company_strategy, etc.
    digest_topic VARCHAR(256) NOT NULL,       -- "AI and Technology"
    summary TEXT,                             -- AI-generated summary
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    event_count INTEGER NOT NULL DEFAULT 1,
    source_count INTEGER NOT NULL DEFAULT 1,  -- Number of unique feeds
    included_in_digest_id INTEGER,
    included_at TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);
```

### `story_arc_events` Table
```sql
CREATE TABLE story_arc_events (
    id SERIAL PRIMARY KEY,
    story_arc_id INTEGER NOT NULL REFERENCES story_arcs(id) ON DELETE CASCADE,
    event_date TIMESTAMP WITH TIME ZONE NOT NULL,
    event_summary TEXT NOT NULL,              -- "GPT-5.2 released with multimodal capabilities"
    key_points TEXT[] NOT NULL DEFAULT '{}',  -- Supporting details
    source_feed_id INTEGER REFERENCES feeds(id),
    source_episode_id INTEGER REFERENCES episodes(id),
    source_episode_guid VARCHAR(512),
    source_name VARCHAR(256),                 -- "Hard Fork Podcast"
    perspective VARCHAR(50),                  -- positive, negative, neutral, analytical
    relevance_score DOUBLE PRECISION,
    extracted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);
```

### Web Settings (Already Added)
```sql
-- story_arcs.retention_days = 14
-- story_arcs.max_events_per_arc = 20
```

## Functional Categories

Use these categories when classifying story arcs:

| Category | Description | Examples |
|----------|-------------|----------|
| `model_release` | New model announcements, updates, versions | GPT-5.2 launch, Claude 4 release |
| `company_strategy` | Business moves, pivots, leadership changes | OpenAI restructuring, Apple AI strategy |
| `research` | Papers, studies, breakthroughs | New alignment techniques, benchmark results |
| `regulation` | Policy, legal, governance | EU AI Act, California AI laws |
| `product_launch` | New products, features, services | ChatGPT Enterprise, Copilot features |
| `partnership` | Collaborations, acquisitions, investments | Microsoft-OpenAI, Google-Samsung |
| `controversy` | Disputes, criticisms, debates | AI safety concerns, copyright issues |
| `industry_trend` | Broader patterns, market shifts | AI spending trends, enterprise adoption |
| `technique` | New methods, approaches, architectures | RAG improvements, fine-tuning methods |
| `use_case` | Applications, implementations | AI in healthcare, coding assistants |
| `other` | Miscellaneous | |

## Perspective Values

Track how each source views the story:

| Perspective | Description |
|-------------|-------------|
| `positive` | Enthusiastic, supportive, optimistic about the development |
| `negative` | Critical, concerned, skeptical about the development |
| `neutral` | Factual coverage without strong stance |
| `analytical` | In-depth analysis, comparison, balanced examination |

## Code Changes Required

### 1. Update `src/topic_tracking/topic_extractor.py`

Replace the entire file with the new `StoryArcExtractor` class. Key changes:

```python
# OLD: TopicExtractor with hardcoded STORY_ARC_PATTERNS
class TopicExtractor:
    def extract_and_store_topics(self, episode_id, episode_guid, ...):
        # Old approach...

# NEW: StoryArcExtractor with LLM-driven recognition
class StoryArcExtractor:
    def extract_and_store_story_arcs(
        self,
        episode_id: int,
        episode_guid: str,
        feed_id: int,
        digest_topic: str,
        transcript: str,
        episode_title: str,
        episode_published_date: datetime,
        relevance_score: float = 0.0,
    ) -> List[Dict]:
        # 1. Get active story arcs for context
        # 2. Create prompt with arc context
        # 3. LLM returns: continuing_arcs[] and new_arcs[]
        # 4. Add events to existing arcs or create new ones
```

**Reference implementation:** See `/home/pbrown/AInewsletter/src/topic_tracking/topic_extractor.py`

### 2. Update Database Repository Methods

Add these methods to your database module (SQLAlchemy or repository pattern):

```python
# Create story arc
def create_story_arc(
    arc_name: str,
    digest_topic: str,
    functional_category: str = 'other',
    initial_event: Dict = None
) -> Dict

# Get or create (find by slug, create if not exists)
def get_or_create_story_arc(
    arc_name: str,
    digest_topic: str,
    functional_category: str = 'other'
) -> Dict

# Add event to arc timeline
def add_story_arc_event(
    story_arc_id: int,
    event_date: datetime,
    event_summary: str,
    key_points: List[str],
    source_feed_id: int,
    source_episode_id: int,
    source_episode_guid: str,
    source_name: str,
    perspective: str,
    relevance_score: float
) -> Dict

# Get active arcs (within retention window)
def get_active_story_arcs(
    digest_topic: str,
    days: int = 14
) -> List[Dict]

# Get arcs formatted for prompt
def get_story_arcs_for_prompt(
    digest_topic: str,
    max_arcs: int = 15,
    max_events_per_arc: int = 5
) -> str

# Get arcs for digest generation
def get_story_arcs_for_digest(
    digest_topic: str,
    min_events: int = 2,
    exclude_included: bool = True
) -> List[Dict]

# Mark arc as included in digest
def mark_story_arc_included(
    story_arc_id: int,
    digest_id: int
) -> None

# Cleanup old arcs
def cleanup_old_story_arcs(days: int = 14) -> int
```

**Reference implementation:** See `/home/pbrown/AInewsletter/src/database/supabase_client.py`

### 3. Update Pipeline Integration

In your audio processing pipeline (after scoring), call story arc extraction:

```python
# In run_audio.py or equivalent
from src.topic_tracking.topic_extractor import StoryArcExtractor

# After scoring...
if score >= threshold:
    extractor = StoryArcExtractor(db_client=db)
    results = extractor.extract_and_store_story_arcs(
        episode_id=episode.id,
        episode_guid=episode.episode_guid,
        feed_id=episode.feed_id,
        digest_topic=topic_name,
        transcript=transcript,
        episode_title=episode.title,
        episode_published_date=episode.published_date,
        relevance_score=score
    )
    logger.info(f"Extracted {len(results)} story arcs from episode")
```

### 4. Update Digest Generation

When generating digests/newsletters, use story arcs instead of topics:

```python
# OLD: Get topics for digest
topics = db.get_episode_topics_for_digest(digest_topic)

# NEW: Get story arcs for digest
arcs = db.get_story_arcs_for_digest(
    digest_topic=digest_topic,
    min_events=2,  # At least 2 events = actual story
    exclude_included=True
)

# Pass arcs to script generation with timeline
for arc in arcs:
    print(f"Story: {arc['arc_name']}")
    print(f"Category: {arc['functional_category']}")
    for event in arc['events']:
        print(f"  - [{event['event_date']}] {event['event_summary']}")
        print(f"    Source: {event['source_name']}")
        print(f"    Perspective: {event['perspective']}")
```

## Web UI Changes

### Rename "Recurring Topics" to "Story Arcs"

1. **File renames:**
   - `app/recurring-topics/page.tsx` → `app/story-arcs/page.tsx`
   - `app/api/recurring-topics/` → `app/api/story-arcs/`

2. **Navigation update:**
   ```tsx
   // In Navigation.tsx
   { name: 'Story Arcs', href: '/story-arcs' }
   ```

3. **API endpoints:**
   - `GET /api/story-arcs/topics` → List all active arcs
   - `GET /api/story-arcs/topics/[id]` → Get arc with events
   - `POST /api/story-arcs/merge` → Merge similar arcs
   - `DELETE /api/story-arcs/topics/[id]` → Delete arc

### Story Arcs Page UI

The new page should show:

1. **Arc list with timeline preview:**
   ```
   ┌─────────────────────────────────────────────────────────┐
   │ OpenAI's GPT-5 Development              [model_release] │
   │ 4 events from 3 sources | Last update: Dec 15          │
   │ ─────────────────────────────────────────────────────── │
   │ • Dec 15: GPT-5.2 benchmarks released                   │
   │ • Dec 12: GPT-5.2 launched with multimodal support      │
   │ • Dec 8: Development accelerated                        │
   │ • Dec 5: Code Red declared                              │
   └─────────────────────────────────────────────────────────┘
   ```

2. **Filters:**
   - By digest topic
   - By functional category
   - By date range

3. **Actions:**
   - View full timeline
   - Merge similar arcs
   - Delete arc

### Arc Detail View

Show full timeline with source attribution:

```
Story Arc: OpenAI's GPT-5 Development
Category: model_release
Started: December 5, 2024
Sources: Hard Fork, AI Daily, Lex Fridman

Timeline:
─────────
Dec 5, 2024 - Hard Fork Podcast
"OpenAI declares 'Code Red' following Gemini 2 announcement"
Perspective: analytical
Key points:
  • Internal competition acknowledged
  • Shift in development priorities

Dec 8, 2024 - AI Daily
"Reports of accelerated GPT-5 development timeline"
Perspective: neutral
Key points:
  • Original late Q1 2025 target moved up
  • Additional resources allocated

Dec 12, 2024 - The Vergecast
"GPT-5.2 released with multimodal capabilities"
Perspective: positive
Key points:
  • Video understanding added
  • Performance improvements on reasoning benchmarks

Dec 15, 2024 - Hard Fork Podcast
"GPT-5.2 dominates benchmarks, 15% improvement over GPT-5"
Perspective: analytical
Key points:
  • MMLU score improvements
  • Comparison with Claude 3.5 and Gemini
```

## Daily Consolidation Script

The AInewsletter project runs a daily consolidation script that:
1. Finds semantically similar arcs (in case LLM creates duplicates)
2. Merges events from duplicate arcs into canonical arc
3. Cleans up arcs older than retention period

**This runs on the shared database, so podscrape2 doesn't need its own consolidation script.**

However, if you want to run it manually:
```bash
python scripts/dedupe_topics.py --dry-run --verbose
python scripts/dedupe_topics.py  # Actually merge
```

## Migration from episode_topics

The old `episode_topics` table remains for reference but is no longer used.
No data migration is needed - new arcs will be created as episodes are processed.

To optionally convert existing topics to arcs (one-time):
```python
# This is optional - only if you want to preserve existing topic data
for topic in db.get_all_episode_topics():
    arc = db.get_or_create_story_arc(
        arc_name=topic['topic_name'],
        digest_topic=topic['digest_topic'],
        functional_category=topic.get('topic_type', 'other')
    )
    # Note: Key points don't have dates, so we can't create proper events
    # Just create a single event per topic
    db.add_story_arc_event(
        story_arc_id=arc['id'],
        event_date=topic['first_mentioned_at'],
        event_summary=f"Initial topic: {topic['topic_name']}",
        key_points=topic['key_points'],
        source_episode_id=topic['episode_id']
    )
```

## Testing

1. **Test arc creation:**
   ```bash
   # Process a single episode and check arc creation
   python scripts/run_audio.py --episode-guid "test-guid" --verbose
   ```

2. **Test arc consolidation:**
   ```bash
   python scripts/dedupe_topics.py --dry-run --digest-topic "AI and Technology"
   ```

3. **Verify in database:**
   ```sql
   SELECT arc_name, event_count, source_count, last_updated_at
   FROM story_arcs
   WHERE digest_topic = 'AI and Technology'
   ORDER BY last_updated_at DESC;

   SELECT sa.arc_name, sae.event_date, sae.event_summary, sae.source_name
   FROM story_arcs sa
   JOIN story_arc_events sae ON sae.story_arc_id = sa.id
   ORDER BY sa.arc_name, sae.event_date;
   ```

## Summary of Files to Change

| File | Change |
|------|--------|
| `src/topic_tracking/topic_extractor.py` | Replace with StoryArcExtractor |
| `src/database/topic_tracking_repo.py` | Add story arc methods |
| `scripts/run_audio.py` | Call StoryArcExtractor after scoring |
| `src/generation/script_generator.py` | Use story arcs instead of topics |
| `web_ui_hosted/app/recurring-topics/` | Rename to `story-arcs/` |
| `web_ui_hosted/app/api/recurring-topics/` | Rename to `story-arcs/` |
| `web_ui_hosted/components/Navigation.tsx` | Update nav link |

## Questions?

If you have questions about the implementation, refer to:
- `/home/pbrown/AInewsletter/src/topic_tracking/topic_extractor.py` - StoryArcExtractor implementation
- `/home/pbrown/AInewsletter/src/database/supabase_client.py` - Database methods
- `/home/pbrown/AInewsletter/scripts/dedupe_topics.py` - Consolidation script

---

*Document created: December 19, 2024*
*For: podscrape2 Story Arc Integration*
