"""
TopicExtractor: Extracts high-level topics from episode transcripts using GPT.
Used for topic tracking and story arc evolution in digest generation.

Key feature: Story Arc Recognition - Topics are grouped into evolving stories
rather than treated as isolated events. New information extends existing stories
rather than creating duplicate topics.
"""

import os
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from openai import OpenAI
from dotenv import load_dotenv

from src.topic_tracking.novelty_detector import NoveltyDetector
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

load_dotenv()

logger = logging.getLogger(__name__)

# Story arc patterns - keywords that indicate topics belong to the same story
STORY_ARC_PATTERNS = {
    "gemini-3": ["gemini 3", "gemini-3", "gemini3"],
    "gpt-5-release": ["gpt-5", "gpt 5", "gpt5", "gpt-5.2", "gpt 5.2"],
    "openai-strategy": ["openai code red", "openai's focus", "openai's shift", "openai financial"],
    "ai-trust": ["ai trust", "trust in ai", "ai perception", "edelman", "ai sentiment"],
    "google-glasses": ["google glass", "smart glasses", "project aura", "android xr"],
    "cybersecurity-ciso": ["ciso", "cybersecurity", "zero trust", "cyber risk"],
    "ai-benchmarks": ["benchmark", "gdpval", "ai grading", "model performance"],
    "ai-education": ["ai education", "workforce development", "micro-credential", "educational"],
    "social-media-regulation": ["tiktok", "social media ban", "first amendment", "fcc", "media regulation"],
    "streaming-wars": ["netflix", "streaming", "warner bros", "disney acquisition"],
    "square-block": ["square", "block inc"],
}


class TopicExtractor:
    """
    Extracts high-level topics and key points from episode transcripts.
    Groups topics into story arcs for tracking evolution over time.
    """

    def __init__(
        self,
        db_client,
        max_topics: int = 15,
        novelty_threshold: float = 0.30,
        enable_novelty_detection: bool = True,
        semantic_similarity_threshold: float = 0.80
    ):
        """
        Initialize TopicExtractor.

        Args:
            db_client: Database client with topic tracking methods
            max_topics: Maximum topics to extract per episode
            novelty_threshold: Minimum novelty score (0.0-1.0)
            enable_novelty_detection: Whether to calculate novelty scores
            semantic_similarity_threshold: Threshold for matching existing topics
        """
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key, timeout=60.0)
        self.db = db_client
        self.max_topics = max_topics

        # Get model from web_settings, fallback to default
        try:
            self.model = db_client.get_setting('topic_tracking', 'extraction_model', 'gpt-5.2-chat-latest')
            logger.info(f"Using extraction model from web_settings: {self.model}")
        except Exception as e:
            self.model = "gpt-5.2-chat-latest"
            logger.warning(f"Failed to get model from web_settings, using default: {self.model}")

        # Initialize semantic matcher for existing topic detection
        try:
            self.semantic_matcher = SemanticTopicMatcher(
                similarity_threshold=semantic_similarity_threshold,
                db_client=db_client  # Load embedding model from web_settings
            )
            logger.info(f"Semantic matching enabled with threshold: {semantic_similarity_threshold}")
        except Exception as e:
            logger.warning(f"Failed to initialize semantic matcher: {e}")
            self.semantic_matcher = None

        # Initialize novelty detector
        self.novelty_detection_enabled = enable_novelty_detection
        if enable_novelty_detection:
            try:
                self.novelty_detector = NoveltyDetector(
                    novelty_threshold=novelty_threshold,
                    db_client=db_client  # Load embedding model from web_settings
                )
                logger.info(f"Novelty detection enabled with threshold: {novelty_threshold}")
            except Exception as e:
                logger.warning(f"Failed to initialize novelty detector: {e}")
                self.novelty_detector = None
                self.novelty_detection_enabled = False
        else:
            self.novelty_detector = None

    def _identify_story_arc(self, topic_name: str, key_points: List[str] = None) -> Optional[str]:
        """
        Identify which story arc a topic belongs to based on keywords.

        Args:
            topic_name: The topic name to check
            key_points: Optional key points for additional context

        Returns:
            Story arc identifier or None if no match
        """
        text_to_check = topic_name.lower()
        if key_points:
            text_to_check += " " + " ".join(key_points).lower()

        for arc_id, patterns in STORY_ARC_PATTERNS.items():
            for pattern in patterns:
                if pattern in text_to_check:
                    return arc_id

        return None

    def _group_topics_by_story_arc(self, topics: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Group existing topics by their story arc.

        Args:
            topics: List of topic dictionaries

        Returns:
            Dictionary mapping story arc IDs to lists of topics
        """
        arcs = {}
        for topic in topics:
            arc_id = self._identify_story_arc(
                topic.get('topic_name', ''),
                topic.get('key_points', [])
            )
            if arc_id:
                if arc_id not in arcs:
                    arcs[arc_id] = []
                arcs[arc_id].append(topic)

        return arcs

    def _get_story_arcs_for_prompt(self, existing_topics: List[Dict]) -> str:
        """
        Generate story arc descriptions for the GPT prompt.

        Args:
            existing_topics: List of existing topics

        Returns:
            Formatted string describing active story arcs
        """
        arcs = self._group_topics_by_story_arc(existing_topics)

        if not arcs:
            return ""

        lines = ["\n\nACTIVE STORY ARCS (extend these rather than creating new topics):"]

        for arc_id, arc_topics in arcs.items():
            # Get the canonical name (most common or first)
            names = [t.get('topic_name') for t in arc_topics]
            canonical_name = max(set(names), key=names.count) if names else arc_id

            # Collect unique key points
            all_points = []
            for t in arc_topics:
                all_points.extend(t.get('key_points', []))
            unique_points = list(dict.fromkeys(all_points))[:4]

            lines.append(f"\n📰 **{canonical_name}**")
            lines.append(f"   Story ID: {arc_id}")
            if unique_points:
                lines.append(f"   Recent developments: {'; '.join(unique_points[:2])}")

        lines.append("\nWhen content relates to an active story arc, use the story's canonical name and add NEW developments only.")

        return "\n".join(lines)

    def extract_and_store_topics(
        self,
        episode_id: int,
        episode_guid: str,
        digest_topic: str,
        transcript: str,
        relevance_score: float,
    ) -> List[Dict]:
        """
        Extract high-level topics from transcript and store in database.

        Key behavior: Recognizes story arcs and extends existing stories rather
        than creating duplicate topics. New information is added to evolving
        narratives.

        Args:
            episode_id: Episode database ID
            episode_guid: Episode GUID (for logging)
            digest_topic: Parent topic (e.g., "AI and Technology")
            transcript: Full episode transcript
            relevance_score: Episode's score for digest_topic

        Returns:
            List of extracted topic dictionaries
        """
        logger.info(
            f"Extracting topics from episode {episode_guid} for {digest_topic}"
        )

        # Get existing topics for this digest_topic
        existing_topics = []
        story_arcs_prompt = ""
        existing_topic_names = ""

        try:
            existing_topics = self.db.get_recent_episode_topics(
                digest_topic=digest_topic,
                days=30
            )
            logger.info(f"Retrieved {len(existing_topics)} existing topics")

            # Generate story arcs section for prompt
            story_arcs_prompt = self._get_story_arcs_for_prompt(existing_topics)

            # Also generate simple topic names list
            if self.semantic_matcher and existing_topics:
                existing_topic_names = self.semantic_matcher.get_topic_names_for_prompt(
                    existing_topics, max_topics=50
                )
        except Exception as e:
            logger.warning(f"Failed to retrieve existing topics: {e}")

        # Create prompt for GPT
        prompt = self._create_extraction_prompt(
            transcript, digest_topic, existing_topic_names, story_arcs_prompt
        )
        schema = self._create_extraction_schema()

        try:
            # Call GPT with structured output
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "topic_extraction",
                        "schema": schema,
                        "strict": True,
                    },
                },
                max_tokens=2000,
            )

            # Parse response
            topics_data = json.loads(response.choices[0].message.content)
            extracted_topics = topics_data.get("topics", [])

            logger.info(
                f"Extracted {len(extracted_topics)} topics from episode {episode_guid}"
            )

            # Store each topic in database
            stored_topics = []
            topics_matched = 0
            topics_new = 0
            topics_arc_extended = 0

            for topic_data in extracted_topics[: self.max_topics]:
                try:
                    topic_name = topic_data["name"]
                    key_points = topic_data["key_points"]

                    # Check if this belongs to a story arc
                    story_arc = self._identify_story_arc(topic_name, key_points)

                    # Try semantic matching against existing topics
                    matched_topic = None
                    if self.semantic_matcher and existing_topics:
                        try:
                            matched_topic = self.semantic_matcher.find_matching_topic(
                                new_topic_name=topic_name,
                                new_key_points=key_points,
                                existing_topics=existing_topics,
                                digest_topic=digest_topic
                            )
                        except Exception as e:
                            logger.warning(f"Semantic matching failed for '{topic_name}': {e}")

                    # If no semantic match but belongs to story arc, find arc's canonical topic
                    if not matched_topic and story_arc:
                        arc_topics = [
                            t for t in existing_topics
                            if self._identify_story_arc(t.get('topic_name', ''), t.get('key_points', [])) == story_arc
                        ]
                        if arc_topics:
                            # Use the oldest topic as canonical
                            arc_topics.sort(key=lambda t: t.get('first_mentioned_at') or t.get('created_at'))
                            canonical = arc_topics[0]
                            matched_topic = type('TopicMatch', (), {
                                'topic_id': canonical.get('id'),
                                'topic_slug': canonical.get('topic_slug'),
                                'topic_name': canonical.get('topic_name'),
                                'similarity_score': 0.0,  # Story arc match, not semantic
                                'key_points': canonical.get('key_points', [])
                            })()
                            topics_arc_extended += 1
                            logger.info(
                                f"Topic '{topic_name}' matched to story arc '{story_arc}' "
                                f"-> '{canonical.get('topic_name')}'"
                            )

                    if matched_topic:
                        # Found existing topic - add new info to it
                        topics_matched += 1
                        topic_slug = matched_topic.topic_slug
                        parent_topic_id = matched_topic.topic_id

                        # Merge key points (new points only)
                        existing_points_lower = {p.lower() for p in (matched_topic.key_points or [])}
                        new_key_points = [
                            kp for kp in key_points
                            if kp.lower() not in existing_points_lower
                        ]

                        if not new_key_points:
                            logger.info(
                                f"Topic '{topic_name}' matched '{matched_topic.topic_name}' "
                                f"but no new key points to add, skipping"
                            )
                            continue

                        # Use matched topic name for consistency
                        final_topic_name = matched_topic.topic_name
                        # Combine existing and new key points (limit to 6 for story arcs)
                        final_key_points = (list(matched_topic.key_points or []) + new_key_points)[:6]

                        # Calculate novelty based on new key points
                        novelty_score = len(new_key_points) / max(len(key_points), 1)

                        logger.info(
                            f"Matched '{topic_name}' to existing '{matched_topic.topic_name}', "
                            f"adding {len(new_key_points)} new key points"
                        )
                    else:
                        # New topic
                        topics_new += 1
                        topic_slug = self._normalize_topic_name(topic_name)
                        final_topic_name = topic_name
                        final_key_points = key_points
                        novelty_score = 1.0
                        parent_topic_id = None

                        # Fall back to novelty detector for exact slug matches
                        if self.novelty_detection_enabled and self.novelty_detector and existing_topics:
                            try:
                                novelty_score, parent_topic_id = self.novelty_detector.calculate_novelty_score(
                                    current_topic={
                                        'topic_slug': topic_slug,
                                        'topic_name': topic_name,
                                        'key_points': key_points
                                    },
                                    recent_topics=existing_topics
                                )
                            except Exception as e:
                                logger.warning(f"Novelty detection failed for '{topic_name}': {e}")
                                novelty_score = 1.0

                    # Store topic with all fields
                    stored_topic = self.db.store_episode_topic(
                        episode_id=episode_id,
                        topic_name=final_topic_name,
                        topic_slug=topic_slug,
                        key_points=final_key_points,
                        digest_topic=digest_topic,
                        relevance_score=relevance_score,
                        topic_type=topic_data.get("type", "other"),
                        novelty_score=novelty_score,
                        is_update=matched_topic is not None or topic_data.get("is_update", False),
                        parent_topic_id=parent_topic_id,
                        evolution_summary=topic_data.get("evolution_summary"),
                    )
                    stored_topics.append(
                        {
                            "name": final_topic_name,
                            "type": topic_data.get("type", "other"),
                            "key_points": final_key_points,
                            "novelty_score": novelty_score,
                            "matched_existing": matched_topic is not None,
                            "story_arc": story_arc,
                        }
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to store topic '{topic_data['name']}': {e}"
                    )

            logger.info(
                f"Episode {episode_guid}: {topics_new} new topics, "
                f"{topics_matched} matched ({topics_arc_extended} via story arc)"
            )

            return stored_topics

        except Exception as e:
            logger.error(f"Topic extraction failed for {episode_guid}: {e}")
            raise

    def _create_extraction_prompt(
        self,
        transcript: str,
        digest_topic: str,
        existing_topic_names: str = "",
        story_arcs_prompt: str = ""
    ) -> str:
        """
        Create GPT prompt for topic extraction with story arc awareness.

        Args:
            transcript: Full episode transcript
            digest_topic: Parent topic name
            existing_topic_names: Formatted list of existing topic names
            story_arcs_prompt: Story arcs section for the prompt

        Returns:
            Formatted prompt string
        """
        truncated_transcript = transcript[:4000]

        existing_topics_section = ""
        if existing_topic_names:
            existing_topics_section = f"""

EXISTING TOPICS (reuse these names when content matches):
{existing_topic_names}
"""

        return f"""Analyze this transcript and extract significant topics relevant to "{digest_topic}".

IMPORTANT: We track STORY ARCS - ongoing narratives that evolve over time. When you find content
about an existing story, EXTEND that story rather than creating a new topic.
{story_arcs_prompt}
{existing_topics_section}

For each topic, provide:
1. **Name**: Use existing topic name if this extends a story, otherwise create a clear, specific name
2. **Type**: Classification (model_release, use_case, personality, research, company_news, regulation, technique, other)
3. **Key Points**: 2-4 bullet points of NEW information only (what's new in this episode?)
4. **Is Update**: true if this extends an existing story arc
5. **Related To**: The story/topic this updates (if is_update=true)
6. **Evolution Summary**: What's new/changed since the last update (if is_update=true)

Guidelines:
- PRIORITIZE extending existing stories over creating new topics
- Key points should capture NEW developments, not repeat known information
- Focus on what CHANGED or what's NEW in this episode
- Generic observations are not useful - be specific
- If content relates to an active story arc, use that story's name

Transcript (first 4000 chars):
{truncated_transcript}

Extract topics, prioritizing story arc continuity."""

    def _create_extraction_schema(self) -> dict:
        """
        JSON schema for structured topic extraction.

        Returns:
            JSON schema dictionary
        """
        return {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Topic name (use existing name if extending a story)",
                            },
                            "type": {
                                "type": "string",
                                "description": "Topic classification",
                                "enum": [
                                    "model_release",
                                    "use_case",
                                    "personality",
                                    "research",
                                    "company_news",
                                    "regulation",
                                    "technique",
                                    "other",
                                ],
                            },
                            "key_points": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 4,
                                "description": "NEW information from this episode",
                            },
                            "is_update": {
                                "type": "boolean",
                                "description": "True if this extends an existing story arc",
                            },
                            "related_to": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "null"}
                                ],
                                "description": "Story/topic this updates (if is_update=true)",
                            },
                            "evolution_summary": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "null"}
                                ],
                                "description": "What's new/changed (if is_update=true)",
                            },
                        },
                        "required": ["name", "type", "key_points", "is_update", "related_to", "evolution_summary"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 15,
                }
            },
            "required": ["topics"],
            "additionalProperties": False,
        }

    def _normalize_topic_name(self, topic_name: str) -> str:
        """
        Normalize topic name to slug for deduplication.

        Args:
            topic_name: Original topic name

        Returns:
            Normalized slug
        """
        slug = topic_name.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[-\s]+", "-", slug)
        slug = slug.strip("-")
        return slug
