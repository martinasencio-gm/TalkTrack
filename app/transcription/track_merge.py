"""Merge per-track transcripts into one timeline.

Transcribing mic and system separately, rather than transcribing the mix,
removes two problems at once. The mix fed Whisper a doubled copy of every
remote sentence whenever the user was on speakers instead of headphones,
and speaker attribution had to be guessed by comparing the two tracks'
RMS in the same time window — a comparison that bleed defeats.

Here each track carries its own speaker label by construction. What's left
to handle is the bleed itself: the mic hears the speakers, so a remote
sentence can be transcribed off both tracks. Those duplicates are removed
by matching text at overlapping times, which is far stronger evidence than
comparing loudness.
"""
import os
import re
from dataclasses import replace
from difflib import SequenceMatcher

# Below this many words there isn't enough text to tell an echo from two
# people saying "yeah" at once, and deleting a real reply is worse than
# keeping a duplicate.
MIN_WORDS_FOR_DEDUP = 3

# Similarity at which two overlapping transcripts are the same utterance.
# The bleed copy is a degraded recording, so it rarely matches word for
# word; 0.75 tolerates that without merging genuinely different speech.
SIMILARITY_THRESHOLD = 0.75

_PUNCTUATION = re.compile(r"[^\w\s]")


def _normalise(text):
    return _PUNCTUATION.sub("", text.lower()).split()


def _overlaps(a, b):
    return a.start < b.end and b.start < a.end


def _is_echo_of(candidate, other):
    """True when candidate looks like a bleed copy of other."""
    if not _overlaps(candidate, other):
        return False
    words = _normalise(candidate.text)
    other_words = _normalise(other.text)
    if len(words) < MIN_WORDS_FOR_DEDUP or len(other_words) < MIN_WORDS_FOR_DEDUP:
        return False
    ratio = SequenceMatcher(None, " ".join(words), " ".join(other_words)).ratio()
    return ratio >= SIMILARITY_THRESHOLD


def merge_tracks(tracks):
    """Combine [(speaker, segments), ...] into one time-ordered list.

    The FIRST track is the echo-prone one and the only one segments are
    dropped from; later tracks are authoritative. In practice that means
    [("You", mic), ("Remote", system)] — the mic hears the speakers, but
    a loopback of the render stream never picks up the user's voice.

    Input segments are not mutated; labelled copies are returned.
    """
    if not tracks:
        return []

    labelled = [
        [replace(segment, speaker=speaker) for segment in segments]
        for speaker, segments in tracks
    ]

    kept = list(labelled[0])
    for authoritative in labelled[1:]:
        kept = [s for s in kept
                if not any(_is_echo_of(s, other) for other in authoritative)]

    merged = kept
    for authoritative in labelled[1:]:
        merged = merged + list(authoritative)
    return sorted(merged, key=lambda s: (s.start, s.end))


def dual_track_plan(session, diarization_enabled, hf_token, exists=os.path.exists):
    """Return [(speaker, path), ...] for a recording that should be
    transcribed track-by-track, or None to transcribe the mix.

    Declined when pyannote will run: full diarization clusters voices
    across the mixed audio and must keep seeing it. Where it would have
    fallen back to SimpleDiarizer, this replaces it — the same You/Remote
    labels, decided by which file a segment came from rather than by
    comparing the tracks' loudness in each window.
    """
    if not session:
        return None
    if diarization_enabled and hf_token:
        return None
    audio_files = session.get("audio_files", {})
    mic = audio_files.get("mic")
    system = audio_files.get("system")
    if not (mic and system):
        return None
    if not (exists(mic) and exists(system)):
        return None
    # Mic first: merge_tracks only drops from the first track, and only
    # the mic can contain a bleed copy of the other side.
    return [("You", mic), ("Remote", system)]
