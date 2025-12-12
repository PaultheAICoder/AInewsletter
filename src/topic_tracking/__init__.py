"""
Topic Tracking Module

Extracts and tracks topics from transcripts for deduplication and evolution tracking.
"""

from src.topic_tracking.topic_extractor import TopicExtractor
from src.topic_tracking.novelty_detector import NoveltyDetector

__all__ = ['TopicExtractor', 'NoveltyDetector']
