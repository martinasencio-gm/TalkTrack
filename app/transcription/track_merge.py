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

# Corroboration required before any mic segment is treated as bleed.
#
# A single matching utterance is not evidence of bleed. Two people saying
# "sounds good to me" over each other is indistinguishable from an echo at
# the segment level — same words, same moment — and on the old per-segment
# rule the user's own agreement was silently deleted from the transcript
# of a recording made on headphones, where no bleed existed at all.
#
# What separates the two is scale. An open mic next to the speakers copies
# most of what the other side says, so real bleed shows up across the whole
# call; coincidental agreement happens once or twice. Requiring both a
# minimum count and a meaningful share of the remote track keeps the
# coincidences and still catches genuine bleed.
BLEED_MIN_MATCHES = 3
BLEED_MIN_REMOTE_FRACTION = 0.2

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


def _echo_indices(candidates, authoritative):
    """Positions in candidates that look like copies of authoritative speech."""
    return {i for i, s in enumerate(candidates)
            if any(_is_echo_of(s, other) for other in authoritative)}


def bleed_detected(echo_count, remote_count):
    """True when the matches are too widespread to be coincidence.

    Deliberately a whole-recording judgement rather than a per-segment one:
    an individual match cannot distinguish an echo from both people saying
    the same thing at the same moment, but the two look nothing alike
    across a full call.
    """
    if remote_count <= 0 or echo_count < BLEED_MIN_MATCHES:
        return False
    return echo_count / remote_count >= BLEED_MIN_REMOTE_FRACTION


def merge_tracks(tracks):
    """Combine [(speaker, segments), ...] into one time-ordered list.

    The FIRST track is the echo-prone one and the only one segments are
    dropped from; later tracks are authoritative. In practice that means
    [("You", mic), ("Remote", system)] — the mic hears the speakers, but
    a loopback of the render stream never picks up the user's voice.

    Segments are only dropped when the recording as a whole shows bleed
    (see bleed_detected). Without that check a mic track that never heard
    the speakers still lost any utterance that happened to collide with
    the same words from the other side.

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
        echoes = _echo_indices(kept, authoritative)
        if not bleed_detected(len(echoes), len(authoritative)):
            continue
        kept = [s for i, s in enumerate(kept) if i not in echoes]

    merged = kept
    for authoritative in labelled[1:]:
        merged = merged + list(authoritative)
    return sorted(merged, key=lambda s: (s.start, s.end))


def dual_track_plan(session, diarization_enabled, hf_token, exists=os.path.exists):
    """Return [(speaker, path), ...] for a recording that should be
    transcribed track-by-track, or None to transcribe the mix.

    Declined when full diarization will run: full diarization clusters voices
    across the mixed audio and must keep seeing it. Where it would have
    fallen back to SimpleDiarizer, this replaces it — the same You/Remote
    labels, decided by which file a segment came from rather than by
    comparing the tracks' loudness in each window.
    """
    if not session:
        return None
    if diarization_enabled and bool(hf_token):
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
