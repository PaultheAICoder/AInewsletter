"""
Topic Tracking Module

Extracts and tracks topics from transcripts for deduplication and evolution tracking.
Includes semantic matching to prevent duplicate topics and consolidate related content.
"""

from src.topic_tracking.topic_extractor import TopicExtractor
from src.topic_tracking.novelty_detector import NoveltyDetector
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

__all__ = ['TopicExtractor', 'NoveltyDetector', 'SemanticTopicMatcher']
