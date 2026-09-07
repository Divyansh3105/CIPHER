"""POST /voice/transcribe -- speech to text that does not depend on Chrome.

The browser's `webkitSpeechRecognition` was Phase 4's whole voice input, and
it has a failure mode that looks like a network problem and is not: outside
Google Chrome, the API often *exists* and always fails with `error:
"network"`. Chromium forks (Brave, ungoogled builds, most embedded webviews)
ship the interface without Google's speech backend credentials, so every
attempt reaches nothing. Firefox and Safari do not implement it at all.

This is the fallback that makes voice work anywhere: the browser records
audio and posts it here, and Groq's hosted Whisper transcribes it. It needs
no new credential -- `GROQ_API_KEY` is already required for the fallback LLM
provider -- and no local model download, which is what makes it a better
first alternative than running Whisper on the user's own machine.

It is also the more private of the two. The browser API streams audio to
Google continuously while the microphone is on; this sends one clip per
utterance to a provider the project already talks to.

Format: whatever MediaRecorder produced. Chrome gives webm/opus, Firefox
ogg/opus, Safari mp4 -- Groq accepts all three, so nothing is transcoded and
no ffmpeg is needed.
"""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from groq import APIError, AsyncGroq

from app.api.deps import get_current_user_id
from app.core.config import Settings, get_settings
from app.core.ratelimit import CHAT_RULE, RateLimiter, get_rate_limiter
from app.models.schemas import TranscriptionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

#: Groq's hosted Whisper. `-turbo` is materially faster and slightly less
#: accurate; for short conversational utterances the speed is what matters,
#: because latency here is felt directly as the assistant being slow to
#: react. Both were verified live against this project's key.
TRANSCRIBE_MODEL = "whisper-large-v3-turbo"

#: One utterance, not a recording session. Generous for a spoken sentence and
#: small enough that a stuck recorder cannot upload a meeting.
MAX_AUDIO_BYTES = 8 * 1024 * 1024

#: Below this there is no speech, only a click or a stray keystroke opening
#: and closing the recorder. Rejected before spending a request.
MIN_AUDIO_BYTES = 1200


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
    user_id: UUID = Depends(get_current_user_id),
    settings: Settings = Depends(get_settings),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> TranscriptionResponse:
    # Shares the chat rule and its own key: transcription is per-utterance,
    # so it is bounded by the same "how fast can a person talk" ceiling, and
    # a stuck recorder should not eat the chat allowance.
    allowed, retry_after = rate_limiter.check(f"transcribe:{user_id}", CHAT_RULE)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many transcriptions. Try again in {retry_after}s.",
            headers={"Retry-After": str(retry_after)},
        )

    data = await audio.read()
    if len(data) < MIN_AUDIO_BYTES:
        # 200 with empty text, not an error: a recorder that opened and shut
        # on a stray click is a non-event, and surfacing it as a failure
        # would put an error banner on screen for doing nothing.
        return TranscriptionResponse(text="", model_used=TRANSCRIBE_MODEL)
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"That clip is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{MAX_AUDIO_BYTES // 1_048_576} MB.",
        )

    # The filename carries the container format, which is how Groq knows how
    # to decode it. Passing a generic name makes valid audio look corrupt.
    filename = audio.filename or "utterance.webm"

    client = AsyncGroq(api_key=settings.groq_api_key)
    try:
        result = await client.audio.transcriptions.create(
            file=(filename, data),
            model=TRANSCRIBE_MODEL,
            **({"language": language} if language else {}),
        )
    except APIError as exc:
        logger.warning("Transcription failed: %s", exc)
        raise HTTPException(
            status_code=502, detail=f"Could not transcribe that audio: {exc}"
        ) from exc

    return TranscriptionResponse(text=(result.text or "").strip(), model_used=TRANSCRIBE_MODEL)
