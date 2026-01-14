"""
Topic Tracking Module

Extracts and tracks story arcs from transcripts.
Story arcs are evolving news narratives tracked across multiple episodes.
"""

from src.topic_tracking.topic_extractor import StoryArcExtractor
from src.topic_tracking.semantic_matcher import SemanticTopicMatcher

__all__ = ['StoryArcExtractor', 'SemanticTopicMatcher']
