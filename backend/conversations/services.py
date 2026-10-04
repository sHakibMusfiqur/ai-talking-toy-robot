import logging
import time

from django_mongodb_backend import transaction

from memory.models import Memory
from safety.models import SafetyEvent

from .ai_client import AIServiceError, call_ai_turn
from .ai_contract import (
    HARD_FAILURE_ACTION,
    HARD_FAILURE_EMOTION,
    HARD_FAILURE_LANGUAGE,
    HARD_FAILURE_TEXT,
    validate_ai_input,
)
from .models import AIInteraction, Conversation, Message

logger = logging.getLogger(__name__)

RECENT_TURNS_LIMIT = 10

# Section 30 severity is derived from the frozen `action_taken` vocabulary
# (display-only field; no separate contract value is invented here).
SEVERITY_BY_SAFETY_ACTION = {
    "BLOCKED": "high",
    "REDIRECTED": "medium",
    "ALLOWED_WITH_CAUTION": "low",
    "ALLOWED": "low",
}


def build_context(conversation):
    
    recent_turns = []
    for message in conversation.messages.order_by("timestamp", "pk"):
        text = (message.text or "").strip()
        if not text:
            continue
        role = "user" if message.role == Message.ROLE_CHILD else "assistant"
        recent_turns.append({"role": role, "text": text})
    return {
        "recent_turns": recent_turns[-RECENT_TURNS_LIMIT:],
        # Memory retrieval/context injection is Phase 14 - the write side
        # only (memory_writes acceptance) is in scope for Phase 11.
        "memories": [],
    }


def build_ai_input(
    conversation,
    child_profile,
    robot,
    *,
    text,
    audio,
    language_hint,
    mode,
    context,
):
    payload = {
        "conversation_id": str(conversation.pk),
        "child_profile_id": str(child_profile.pk),
        "robot_id": str(robot.pk),
        "text": text,
        "audio": audio,
        "language_hint": language_hint,
        "mode": mode,
        "context": context,
    }
    return validate_ai_input(payload, conversation_id=str(conversation.pk))


def _store_memory_writes(writes, *, child_profile, conversation):
    """Write-side memory stub (Phase 14 owns CRUD/retrieval).

    Contract-valid entries are stored unless they cannot fit the model
    (a key longer than the column); skipped entries never fail the turn.
    """
    stored = []
    skipped = 0
    for entry in writes:
        key = entry["key"].strip()
        value = entry["value"].strip()
        if not key or len(key) > 100 or not value:
            skipped += 1
            continue
        Memory.objects.create(
            child_profile=child_profile,
            key=key,
            value=value,
            source_conversation=conversation,
        )
        stored.append({"key": key, "value": value})
    if skipped:
        logger.warning(
            "Skipped %s memory_writes entries that do not fit the model",
            skipped,
        )
    return stored


def _persist_turn(
    *,
    conversation,
    child_profile,
    robot,
    text,
    audio,
    output,
    latency_ms,
):
    with transaction.atomic():
        child_message = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_CHILD,
            text=text or "",
            audio_reference="",
        )
        robot_message = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ROBOT,
            text=output["text"],
        )
        AIInteraction.objects.create(
            message=robot_message,
            latency_ms=latency_ms,
            safety_flags=output["safety"] or {},
            emotion=output["emotion"],
            action=output["action"],
        )
        stored_memories = _store_memory_writes(
            output["memory_writes"],
            child_profile=child_profile,
            conversation=conversation,
        )
        safety = output["safety"]
        if safety is not None and safety["flagged"]:
            SafetyEvent.objects.create(
                child_profile=child_profile,
                robot=robot,
                conversation=conversation,
                # The flagged *input* is the turn worth reviewing (the
                # robot reply is the approved redirect, not the trigger).
                message=child_message,
                category=safety["category"],
                severity=SEVERITY_BY_SAFETY_ACTION[safety["action_taken"]],
                action_taken=safety["action_taken"],
            )
        conversation.language = output["language"]
        conversation.save(update_fields=["language"])
    return stored_memories


def _persist_fallback(*, conversation, text, audio, latency_ms, reason):
    with transaction.atomic():
        child_message = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_CHILD,
            text=text or "",
            audio_reference="",
        )
        robot_message = Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ROBOT,
            text=HARD_FAILURE_TEXT,
        )
        AIInteraction.objects.create(
            message=robot_message,
            latency_ms=latency_ms,
            safety_flags={"failed": True, "reason": reason},
            emotion=HARD_FAILURE_EMOTION,
            action=HARD_FAILURE_ACTION,
        )
    return child_message, robot_message


def run_conversation_turn(
    *,
    robot,
    child_profile,
    conversation,
    text,
    audio,
    language_hint,
    mode,
):
    """Run one turn end-to-end; returns the Section 15.3 shaped response.

    Never raises for an AI-service problem: every `AIServiceError`
    becomes the local fallback response with the robot reply persisted, so
    the device always gets an answer in the same response cycle.
    """
    if conversation is None:
        with transaction.atomic():
            conversation = Conversation.objects.create(
                robot=robot, child_profile=child_profile
            )

    context = build_context(conversation)
    payload = build_ai_input(
        conversation,
        child_profile,
        robot,
        text=text,
        audio=audio,
        language_hint=language_hint,
        mode=mode,
        context=context,
    )

    started = time.monotonic()
    try:
        output = call_ai_turn(payload)
    except AIServiceError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "AI turn falling back (reason=%s, robot=%s, latency_ms=%s)",
            exc.reason,
            robot.pk,
            latency_ms,
        )
        _persist_fallback(
            conversation=conversation,
            text=text,
            audio=audio,
            latency_ms=latency_ms,
            reason=exc.reason,
        )
        return {
            "conversation_id": str(conversation.pk),
            "text": HARD_FAILURE_TEXT,
            "language": HARD_FAILURE_LANGUAGE,
            "emotion": HARD_FAILURE_EMOTION,
            "action": HARD_FAILURE_ACTION,
            # Not evaluated - never a fabricated "allowed".
            "safety": None,
            "audio": None,
            "memory_writes": [],
        }

    latency_ms = int((time.monotonic() - started) * 1000)
    stored_memories = _persist_turn(
        conversation=conversation,
        child_profile=child_profile,
        robot=robot,
        text=text,
        audio=audio,
        output=output,
        latency_ms=latency_ms,
    )
    return {
        "conversation_id": str(conversation.pk),
        "text": output["text"],
        "language": output["language"],
        "emotion": output["emotion"],
        "action": output["action"],
        "safety": output["safety"],
        "audio": output["audio"],
        "memory_writes": stored_memories,
    }
