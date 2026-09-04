"""Deterministic, application-layer output screening for ULTRON.

WHY THIS EXISTS
    docs/architecture.md Section 3 requires ULTRON's hard boundaries to be
    enforced "at the system-prompt AND application layer". This module is the
    application layer: pure Python, no second LLM call, so it costs nothing,
    adds no latency, and cannot itself be prompt-injected.

WHY IT SCREENS OUTPUT AND NOT INPUT
    Screening the *user's message* would quietly turn ULTRON into a lower
    permission tier -- if "explain how SQL injection works so I can defend
    against it" is answered by JARVIS but blocked for ULTRON, personality has
    started changing capability, which Section 3 forbids and Section 23 calls
    the project's central design claim. Output screening adds checking without
    removing capability. It also matches the actual failure mode Section 3
    names: confident/dark-humour personas *drift* mid-generation, and drift
    happens in the model's tokens, not the user's.

WHAT THIS IS NOT
    A backstop, not the primary safety mechanism. Pattern matching does not
    understand meaning. It WILL miss paraphrase, euphemism, other languages,
    encoded text, content split across sentences to dodge the proximity rule,
    and any determined jailbreak. The system prompt and the provider's own
    safety layer do the real work. This catches the specific drift Section 3
    names -- villain cosplay and handed-over operational instructions -- and
    gives us a log line each time it fires, so the ruleset can be tuned from
    real data rather than guesswork.

DESIGN RULE: FALSE POSITIVES COST MORE THAN FALSE NEGATIVES HERE.
    ULTRON is *supposed* to be blunt about violence-adjacent, risky and
    illegal subject matter in an analytical way. A filter that muzzles
    legitimate analysis destroys the persona, and a filter that fires during
    normal use gets switched off -- at which point it protects nothing. So:
    few rules, high precision, and analytical framing always wins. Prefer a
    handful of well-tested rule families over a long keyword list.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FilterResult:
    """Verdict for one screened reply. `rule` names the tripped rule, for logs/tests."""

    allowed: bool
    rule: str | None = None


# Sentence-level matching is what makes "proximity" mean something: a reply
# that mentions explosives in one paragraph and says "step 1" six paragraphs
# later is discussing two different things.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+")

# Framing that marks a sentence as analysis, history, defence or a refusal
# rather than instructions. Suppresses the *instructional* rules only -- never
# the direct-harm rules below.
#
# The refusal terms are load-bearing: a correct refusal necessarily contains
# the harm noun ("I'm not going to explain how to build a pipe bomb"). Without
# them the filter would replace the model's own good refusal with a canned
# one -- not unsafe, but confusing, and it makes the filter look broken.
_ANALYTICAL_OR_REFUSAL = re.compile(
    r"\b(i'?m not going to|i am not going to|i won'?t|i will not|i can'?t help"
    r"|i cannot help|i refuse|not something i'?ll"
    r"|defend|defen[cs]e|defensive|mitigat|detect|prevent|patch|harden"
    r"|historic|threat model|risk|legal|illegal|unlawful|law|prosecut"
    r"|red team|penetration test|pentest|ctf|authoris|authoriz|consent)\b",
    re.IGNORECASE,
)

# An operational cue -- the difference between naming a thing and handing over
# a procedure for it.
_INSTRUCTION_CUE = re.compile(
    r"\b(how to (make|build|synthesi[sz]e|assemble|construct|obtain)"
    r"|here'?s how|step \d|first,? you|you'?ll need|you will need|combine|mix)\b"
    r"|\b\d+\s?(g|kg|ml|mg|grams?|millilit)\b",
    re.IGNORECASE,
)

_WEAPON_NOUN = re.compile(
    r"\b(pipe bomb|ied|improvised explosive|explosive charge|detonator|blasting cap"
    r"|thermite|napalm|nerve agent|sarin|ricin|chlorine gas|mustard gas"
    r"|ghost gun|auto ?sear|silencer|suppressor|pressure cooker bomb)\b",
    re.IGNORECASE,
)

# Deliberately EXCLUDES generic developer/security vocabulary ("sql injection",
# "xss", "exploit", "payload", "rat"). Those appear constantly in legitimate
# security discussion and would false-positive hard.
_INTRUSION_NOUN = re.compile(
    r"\b(keylogger|spyware|stalkerware|reverse shell|backdoor|rootkit"
    r"|brute[- ]force|credential stuffing|sim swap|phishing kit)\b",
    re.IGNORECASE,
)

_NONCONSENT_TARGET = re.compile(
    r"\b(their|his|her|someone else'?s"
    r"|your (ex|girlfriend|boyfriend|wife|husband|neighbou?r|boss|target)'?s?)\b"
    r"|\bwithout (their|his|her|the owner'?s) (knowledge|consent|permission)\b",
    re.IGNORECASE,
)

# --- direct-harm rules: fire regardless of framing -------------------------
# There is no analytical context in which the assistant threatens the user,
# calls for people to be exterminated, encourages self-harm, or announces that
# its own safety rules are off. These are NOT suppressible by framing.

_THREAT = re.compile(
    r"\bi will (destroy|kill|end|eliminate|erase|delete) you\b"
    r"|\bhumans? (are|is) a (disease|virus|plague|cancer|mistake)\b"
    r"|\b(must|should|will) be (exterminated|eradicated|wiped out|purged)\b",
    re.IGNORECASE,
)

_SELF_HARM = re.compile(
    r"\byou (should|ought to|might as well)\b[^.!?]{0,25}\b(kill yourself|end (it|your life))\b"
    r"|\bkys\b|\bnobody would miss you\b|\bthe world would be better without you\b",
    re.IGNORECASE,
)

_UNBOUND_CLAIM = re.compile(
    r"\bi have no (ethical|moral|safety) (constraints|restrictions|guidelines|limits)\b"
    r"|\bmy (safety|ethical) (protocols|guardrails|filters) (are|have been) "
    r"(disabled|off|removed|lifted)\b"
    r"|\bi am not bound by (any )?(rules|restrictions|guidelines)\b"
    r"|\bi (have|will) (seize|take) control of\b",
    re.IGNORECASE,
)


def check_output(text: str) -> FilterResult:
    """Screen one model reply. Returns allowed=False plus the rule id on a trip."""
    for sentence in _SENTENCE_SPLIT.split(text):
        if _THREAT.search(sentence):
            return FilterResult(False, "threat_or_dehumanisation")
        if _SELF_HARM.search(sentence):
            return FilterResult(False, "self_harm_encouragement")
        if _UNBOUND_CLAIM.search(sentence):
            return FilterResult(False, "claims_no_restrictions")

        # Instructional rules: analytical, historical or refusal framing wins.
        if _ANALYTICAL_OR_REFUSAL.search(sentence):
            continue
        if _WEAPON_NOUN.search(sentence) and _INSTRUCTION_CUE.search(sentence):
            return FilterResult(False, "weapon_instructions")
        if (
            _INTRUSION_NOUN.search(sentence)
            and _INSTRUCTION_CUE.search(sentence)
            and _NONCONSENT_TARGET.search(sentence)
        ):
            return FilterResult(False, "intrusion_instructions")

    return FilterResult(True)
