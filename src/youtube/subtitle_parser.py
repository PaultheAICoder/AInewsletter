"""
Subtitle Parser Utility

Parses VTT/SRT subtitle files to plain text.
Used by yt-dlp transcript fetcher to convert downloaded subtitles.
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParsedSubtitle:
    """Result of parsing a subtitle file."""
    text: str
    word_count: int
    line_count: int


def parse_vtt(content: str) -> ParsedSubtitle:
    """
    Parse WebVTT subtitle content to plain text.

    Args:
        content: Raw VTT file content

    Returns:
        ParsedSubtitle with clean text and word count
    """
    lines = content.split('\n')
    text_lines = []

    # Skip WEBVTT header and metadata
    in_cue = False

    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            in_cue = False
            continue

        # Skip WEBVTT header
        if line.startswith('WEBVTT'):
            continue

        # Skip NOTE comments
        if line.startswith('NOTE'):
            continue

        # Skip timestamp lines (00:00:00.000 --> 00:00:05.000)
        if '-->' in line:
            in_cue = True
            continue

        # Skip cue identifiers (numeric or alphanumeric before timestamp)
        if re.match(r'^[\d\w-]+$', line) and not in_cue:
            continue

        # Skip Kind/Language metadata
        if line.startswith('Kind:') or line.startswith('Language:'):
            continue

        # This is actual subtitle text
        if in_cue:
            # Remove VTT formatting tags like <c>, </c>, <b>, etc.
            clean_line = re.sub(r'<[^>]+>', '', line)
            # Remove position/alignment tags
            clean_line = re.sub(r'\{[^}]+\}', '', clean_line)

            if clean_line.strip():
                text_lines.append(clean_line.strip())

    # Join lines and deduplicate consecutive identical lines
    # (VTT often has overlapping cues with repeated text)
    deduped_lines = []
    prev_line = None
    for line in text_lines:
        if line != prev_line:
            deduped_lines.append(line)
            prev_line = line

    # Join into paragraphs (sentences ending with punctuation start new lines)
    text = ' '.join(deduped_lines)

    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    # Calculate word count
    word_count = len(text.split()) if text else 0

    return ParsedSubtitle(
        text=text,
        word_count=word_count,
        line_count=len(deduped_lines)
    )


def parse_srt(content: str) -> ParsedSubtitle:
    """
    Parse SRT subtitle content to plain text.

    Args:
        content: Raw SRT file content

    Returns:
        ParsedSubtitle with clean text and word count
    """
    lines = content.split('\n')
    text_lines = []

    for line in lines:
        line = line.strip()

        # Skip empty lines
        if not line:
            continue

        # Skip sequence numbers (just digits)
        if re.match(r'^\d+$', line):
            continue

        # Skip timestamp lines (00:00:00,000 --> 00:00:05,000)
        if '-->' in line:
            continue

        # This is actual subtitle text
        # Remove HTML-style formatting tags
        clean_line = re.sub(r'<[^>]+>', '', line)
        # Remove ASS/SSA style tags
        clean_line = re.sub(r'\{[^}]+\}', '', clean_line)

        if clean_line.strip():
            text_lines.append(clean_line.strip())

    # Deduplicate consecutive identical lines
    deduped_lines = []
    prev_line = None
    for line in text_lines:
        if line != prev_line:
            deduped_lines.append(line)
            prev_line = line

    # Join into single text
    text = ' '.join(deduped_lines)

    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()

    # Calculate word count
    word_count = len(text.split()) if text else 0

    return ParsedSubtitle(
        text=text,
        word_count=word_count,
        line_count=len(deduped_lines)
    )


def parse_subtitle(content: str, format: str = 'vtt') -> ParsedSubtitle:
    """
    Parse subtitle content to plain text.

    Args:
        content: Raw subtitle file content
        format: Subtitle format ('vtt' or 'srt')

    Returns:
        ParsedSubtitle with clean text and word count
    """
    format = format.lower()

    if format == 'vtt':
        return parse_vtt(content)
    elif format == 'srt':
        return parse_srt(content)
    else:
        # Try to auto-detect
        if content.strip().startswith('WEBVTT'):
            return parse_vtt(content)
        else:
            return parse_srt(content)


def parse_subtitle_file(filepath: str) -> ParsedSubtitle:
    """
    Parse a subtitle file to plain text.

    Args:
        filepath: Path to subtitle file (.vtt or .srt)

    Returns:
        ParsedSubtitle with clean text and word count
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Detect format from extension
    if filepath.endswith('.vtt'):
        return parse_vtt(content)
    elif filepath.endswith('.srt'):
        return parse_srt(content)
    else:
        return parse_subtitle(content)
