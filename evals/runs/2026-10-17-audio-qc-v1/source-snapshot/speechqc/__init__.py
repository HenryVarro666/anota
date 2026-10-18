"""Independent audio triage. Importing this package needs only Python's stdlib."""

from .schema import ANALYZER_VERSION, transcript_digest, validate_report

__version__ = ANALYZER_VERSION


def analyze(audio_path, transcript=None, config=None):
    """Analyze audio without running ASR or modifying the source recording."""
    from .analysis import analyze as implementation
    return implementation(audio_path, transcript, config)


def analyze_cached(audio_path, transcript, cache_dir, config=None):
    """Analyze with a verified content-addressed cache of complete reports."""
    from .cache import analyze_cached as implementation
    return implementation(audio_path, transcript, cache_dir, config)


__all__ = ["analyze", "analyze_cached", "transcript_digest", "validate_report"]
