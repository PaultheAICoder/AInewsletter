"""
TopicExtractor: Extracts high-level topics from episode transcripts using GPT.
Used for topic tracking and deduplication in digest generation.

Key feature: Semantic matching against existing topics to prevent duplicates
and add new information to existing topic threads rather than creating redundant entries.
"""

import os
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional

from openai import OpenAI
from dotenv import load_dotenv

from src.topic_tracking.novelty_detector import NoveltyDetector
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

load_dotenv()

logger = logging.getLogger(__name__)


class TopicExtractor:
    """
    Extracts high-level topics and key points from episode transcripts.
    Uses GPT-4o-mini for cost-effective topic analysis.
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
            semantic_similarity_threshold: Threshold for matching existing topics (0.85 = 85% similar)
        """
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key, timeout=60.0)
        self.db = db_client
        self.max_topics = max_topics
        self.model = "gpt-4o-mini"  # Cost-effective for extraction

        # Initialize semantic matcher for existing topic detection
        try:
            self.semantic_matcher = SemanticTopicMatcher(
                similarity_threshold=semantic_similarity_threshold
            )
            logger.info(f"Semantic matching enabled with threshold: {semantic_similarity_threshold}")
        except Exception as e:
            logger.warning(f"Failed to initialize semantic matcher: {e}")
            self.semantic_matcher = None

        # Initialize novelty detector
        self.novelty_detection_enabled = enable_novelty_detection
        if enable_novelty_detection:
            try:
                self.novelty_detector = NoveltyDetector(novelty_threshold=novelty_threshold)
                logger.info(f"Novelty detection enabled with threshold: {novelty_threshold}")
            except Exception as e:
                logger.warning(f"Failed to initialize novelty detector: {e}")
                self.novelty_detector = None
                self.novelty_detection_enabled = False
        else:
            self.novelty_detector = None

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

        Key behavior: Before creating new topics, checks for semantically similar
        existing topics. If found, adds new key points to existing topic instead
        of creating a duplicate.

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

        # Get existing topics for this digest_topic (for semantic matching)
        existing_topics = []
        existing_topic_names = ""
        try:
            existing_topics = self.db.get_recent_episode_topics(
                digest_topic=digest_topic,
                days=30  # Look back further for existing topic names
            )
            logger.info(f"Retrieved {len(existing_topics)} existing topics for semantic matching")

            # Generate topic names list for prompt
            if self.semantic_matcher and existing_topics:
                existing_topic_names = self.semantic_matcher.get_topic_names_for_prompt(
                    existing_topics, max_topics=50
                )
        except Exception as e:
            logger.warning(f"Failed to retrieve existing topics: {e}")

        # Create prompt for GPT (now includes existing topics)
        prompt = self._create_extraction_prompt(transcript, digest_topic, existing_topic_names)
        schema = self._create_extraction_schema()

        try:
            # Call GPT-4o-mini with structured output
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

            # Store each topic in database (respect max_topics limit)
            stored_topics = []
            topics_matched = 0
            topics_new = 0

            for topic_data in extracted_topics[: self.max_topics]:
                try:
                    topic_name = topic_data["name"]
                    key_points = topic_data["key_points"]

                    # First: Try semantic matching against existing topics
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

                    if matched_topic:
                        # Found existing topic - add new info to it
                        topics_matched += 1
                        topic_slug = matched_topic.topic_slug
                        parent_topic_id = matched_topic.topic_id

                        # Merge key points (new points only)
                        existing_points_lower = {p.lower() for p in matched_topic.key_points}
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
                        # Combine existing and new key points (limit to 4)
                        final_key_points = (matched_topic.key_points + new_key_points)[:4]

                        # Calculate novelty based on new key points
                        novelty_score = len(new_key_points) / max(len(key_points), 1)

                        logger.info(
                            f"Matched '{topic_name}' to existing '{matched_topic.topic_name}' "
                            f"(similarity: {matched_topic.similarity_score:.2f}), "
                            f"adding {len(new_key_points)} new key points"
                        )
                    else:
                        # New topic - calculate novelty the old way
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
                        }
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to store topic '{topic_data['name']}': {e}"
                    )

            logger.info(
                f"Episode {episode_guid}: {topics_new} new topics, "
                f"{topics_matched} matched to existing topics"
            )

            # Log if we hit the limit
            if len(extracted_topics) > self.max_topics:
                logger.info(
                    f"Episode {episode_guid} had {len(extracted_topics)} topics, "
                    f"stored top {self.max_topics} (max_topics_per_episode limit)"
                )

            return stored_topics

        except Exception as e:
            logger.error(f"Topic extraction failed for {episode_guid}: {e}")
            raise

    def _create_extraction_prompt(
        self,
        transcript: str,
        digest_topic: str,
        existing_topic_names: str = ""
    ) -> str:
        """
        Create GPT prompt for topic extraction.

        Args:
            transcript: Full episode transcript
            digest_topic: Parent topic name
            existing_topic_names: Formatted list of existing topic names to encourage reuse

        Returns:
            Formatted prompt string
        """
        # Truncate transcript to 4000 chars (enough context, saves tokens)
        truncated_transcript = transcript[:4000]

        # Build the existing topics section if we have any
        existing_topics_section = ""
        if existing_topic_names:
            existing_topics_section = f"""

IMPORTANT - Existing Topics to Reuse:
The following topics already exist in our database. If any topic in this transcript matches one of these (even if phrased differently), USE THE EXISTING TOPIC NAME exactly as shown:
{existing_topic_names}

When you find content about an existing topic:
- Use the EXACT existing topic name (don't create a variation)
- Mark is_update=true
- Add only NEW key points not covered before
- Describe what's new in evolution_summary
"""

        return f"""Analyze this transcript and extract ALL significant high-level topics discussed that are relevant to "{digest_topic}".

For each topic, provide:
1. **Name**: Clear, specific topic name (e.g., "GPT-5 Multimodal Release", "OpenAI Leadership Crisis")
2. **Type**: Classification from these categories:
   - model_release: New model announcements, updates, versions
   - use_case: Applications, implementations, real-world usage
   - personality: Key people in the news (CEOs, researchers, leaders)
   - research: Papers, studies, breakthroughs
   - company_news: Funding, acquisitions, partnerships, business developments
   - regulation: Policy, legal, governance
   - technique: New methods, approaches, architectures
   - other: Miscellaneous or uncategorized

3. **Key Points**: 2-4 bullet points of NEW/INTERESTING information (avoid repeating common knowledge)
4. **Is Update**: Boolean - is this new information about an existing topic?
5. **Related To**: If is_update=true, what's the root topic? (e.g., "gpt-5-release")
6. **Evolution Summary**: If is_update=true, briefly describe what changed/what's new
{existing_topics_section}
Instructions:
- Extract EVERY distinct topic, not just the top 1-3
- Focus on newsworthy events, developments, or discussions
- Classify each topic accurately by type
- PRIORITIZE reusing existing topic names when content matches
- Mark as update if it builds on/evolves a known topic
- Avoid overly generic topics (e.g., "AI is advancing" is too broad)
- Each topic should be specific enough to track over time
- Key points should capture NEW information, not repeat what's already known

Transcript (first 4000 chars):
{truncated_transcript}

Extract all significant topics with full classification."""

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
                                "description": "Specific topic name",
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
                                "description": "Key insights about this topic",
                            },
                            "is_update": {
                                "type": "boolean",
                                "description": "Is this new info about an existing topic?",
                            },
                            "related_to": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "null"}
                                ],
                                "description": "Root topic if this is an update (optional)",
                            },
                            "evolution_summary": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "null"}
                                ],
                                "description": "What changed/what's new if this is an update (optional)",
                            },
                        },
                        "required": ["name", "type", "key_points", "is_update", "related_to", "evolution_summary"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 15,  # Allow up to 15 topics per episode
                }
            },
            "required": ["topics"],
            "additionalProperties": False,
        }

    def _normalize_topic_name(self, topic_name: str) -> str:
        """
        Normalize topic name to slug for deduplication.

        Examples:
            "GPT-5 Release" -> "gpt-5-release"
            "OpenAI Crisis!" -> "openai-crisis"
            "Meta's AI Push" -> "metas-ai-push"

        Args:
            topic_name: Original topic name

        Returns:
            Normalized slug
        """
        # Lowercase
        slug = topic_name.lower()
        # Remove special characters except spaces and hyphens
        slug = re.sub(r"[^\w\s-]", "", slug)
        # Replace multiple spaces/hyphens with single hyphen
        slug = re.sub(r"[-\s]+", "-", slug)
        # Strip leading/trailing hyphens
        slug = slug.strip("-")
        return slug
