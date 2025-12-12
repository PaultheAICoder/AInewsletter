"""
Content Scoring System for YouTube Transcripts

Uses OpenAI GPT to evaluate transcript relevancy against configured topics.
Simplified version adapted from podscrape2-reference.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class ScoringResult:
    """Result container for content scoring operation."""
    episode_id: str
    scores: Dict[str, float]
    processing_time: float
    success: bool
    error_message: Optional[str] = None


class ContentScorer:
    """
    OpenAI-powered content scorer for transcripts.

    Evaluates transcripts against topic relevancy using structured JSON output
    with 0.0-1.0 scoring scale for each configured topic.
    """

    # Default score threshold for relevance
    DEFAULT_THRESHOLD = 0.65

    def __init__(
        self,
        topics: List[Dict[str, Any]],
        score_threshold: float = None,
        model: str = "gpt-4o-mini"
    ):
        """
        Initialize content scorer.

        Args:
            topics: List of topic dicts with 'name' and 'description'
            score_threshold: Minimum score for relevance (default: 0.65)
            model: OpenAI model to use (default: gpt-4o-mini)
        """
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")

        self.client = OpenAI(api_key=api_key, timeout=60.0)
        self.topics = topics
        self.score_threshold = score_threshold or self.DEFAULT_THRESHOLD
        self.model = model
        self.max_tokens = 1000

        logger.info(
            f"ContentScorer initialized with {len(topics)} topics, "
            f"model={model}, threshold={self.score_threshold}"
        )

    def _create_scoring_prompt(self, transcript: str) -> str:
        """Create the scoring prompt for the AI model."""
        topic_descriptions = []
        for topic in self.topics:
            topic_descriptions.append(f"- {topic['name']}: {topic.get('description', 'No description')}")

        # Truncate transcript to first 4000 chars for prompt
        truncated = transcript[:4000]
        if len(transcript) > 4000:
            truncated += "..."

        prompt = f"""You are an expert content analyst evaluating transcript relevancy.

Analyze this transcript and score its relevance to each topic on a scale of 0.0 to 1.0:

Topics to evaluate:
{chr(10).join(topic_descriptions)}

Scoring Guidelines:
- 0.0-0.3: Not relevant or only tangentially mentioned
- 0.4-0.6: Somewhat relevant, touches on topic but not central
- 0.7-0.8: Highly relevant, significant discussion of topic
- 0.9-1.0: Extremely relevant, topic is central to the content

Transcript to analyze:
{truncated}

Provide scores for each topic as a JSON object with topic names as keys and scores as values."""

        return prompt

    def _create_json_schema(self) -> dict:
        """Create JSON schema for structured output."""
        properties = {}
        for topic in self.topics:
            properties[topic['name']] = {
                "type": "number",
                "description": f"Relevance score for {topic['name']} (0.0-1.0)"
            }

        return {
            "type": "object",
            "properties": properties,
            "required": list(properties.keys()),
            "additionalProperties": False
        }

    def _clean_transcript(self, transcript: str) -> str:
        """
        Clean transcript by removing advertisement portions.
        Removes first 5% and last 5% where ads typically appear.
        """
        if not transcript or len(transcript) < 500:
            return transcript

        total_length = len(transcript)
        trim_amount = int(total_length * 0.05)

        cleaned = transcript[trim_amount:total_length - trim_amount]

        logger.debug(f"Transcript cleaned: {total_length} -> {len(cleaned)} chars")
        return cleaned

    def score_transcript(self, transcript: str, episode_id: str = None) -> ScoringResult:
        """
        Score a transcript against all active topics.

        Args:
            transcript: The transcript text
            episode_id: Optional episode identifier for logging

        Returns:
            ScoringResult with scores and metadata
        """
        start_time = datetime.now()

        # Clean transcript
        cleaned_transcript = self._clean_transcript(transcript)

        try:
            prompt = self._create_scoring_prompt(cleaned_transcript)
            schema = self._create_json_schema()

            # Call OpenAI API with structured output
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "content_scores",
                        "schema": schema,
                        "strict": True
                    }
                },
                max_tokens=self.max_tokens
            )

            # Parse response
            scores_json = response.choices[0].message.content
            scores = json.loads(scores_json)

            # Validate and clamp scores
            for topic_name, score in scores.items():
                if not isinstance(score, (int, float)):
                    scores[topic_name] = 0.0
                elif not (0.0 <= score <= 1.0):
                    logger.warning(f"Score {score} for {topic_name} outside range, clamping")
                    scores[topic_name] = max(0.0, min(1.0, float(score)))

            processing_time = (datetime.now() - start_time).total_seconds()

            # Log token usage
            if response.usage:
                logger.info(
                    f"Scored {episode_id or 'transcript'}: "
                    f"{response.usage.total_tokens} tokens, {processing_time:.2f}s"
                )

            return ScoringResult(
                episode_id=episode_id or "unknown",
                scores=scores,
                processing_time=processing_time,
                success=True
            )

        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()
            error_msg = f"Scoring failed: {e}"
            logger.error(error_msg)

            return ScoringResult(
                episode_id=episode_id or "unknown",
                scores={},
                processing_time=processing_time,
                success=False,
                error_message=error_msg
            )

    def is_relevant(self, scores: Dict[str, float]) -> bool:
        """
        Check if any topic score meets the relevance threshold.

        Args:
            scores: Dictionary of topic scores

        Returns:
            True if any score >= threshold
        """
        return any(score >= self.score_threshold for score in scores.values())

    def get_relevant_topics(self, scores: Dict[str, float]) -> List[str]:
        """
        Get list of topics that meet the relevance threshold.

        Args:
            scores: Dictionary of topic scores

        Returns:
            List of topic names meeting threshold
        """
        return [
            topic for topic, score in scores.items()
            if score >= self.score_threshold
        ]
