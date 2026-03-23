"""
src/onboarding/steps.py

Defines the ordered sequence of questions Ember asks during first-run onboarding.
Each OnboardingStep carries the question text, the profile memory category it maps
to, and the tags to attach to the written profile record.
"""

from dataclasses import dataclass


@dataclass
class OnboardingStep:
    key: str          # unique identifier, used in state metadata
    category: str     # profile record metadata category
    tags: list[str]   # tags written to the profile memory record
    question: str     # the question Ember asks the user


ONBOARDING_STEPS: list[OnboardingStep] = [
    OnboardingStep(
        key="identity",
        category="identity",
        tags=["identity", "profile"],
        question="Let's start with the basics. What's your name, and what pronouns do you use?",
    ),
    OnboardingStep(
        key="environment",
        category="environment",
        tags=["location", "home", "profile"],
        question="Where do you live, and what does your home or work environment look like?",
    ),
    OnboardingStep(
        key="health",
        category="health",
        tags=["health", "profile"],
        question=(
            "Is there any health context relevant to how you work? "
            "Chronic illness, energy variability, neurodivergence — anything that shapes "
            "your day-to-day capacity."
        ),
    ),
    OnboardingStep(
        key="professional",
        category="professional",
        tags=["work", "profession", "profile"],
        question="What do you do for work? Your role, domain, and what you spend your professional time on.",
    ),
    OnboardingStep(
        key="personal",
        category="personal",
        tags=["interests", "profile"],
        question="What do you do outside of work? What matters to you, what are you drawn to, what takes up your time?",
    ),
    OnboardingStep(
        key="communication",
        category="communication",
        tags=["communication", "preferences", "profile"],
        question="How do you want Ember to communicate with you? Tone, format, length — and what to avoid.",
    ),
    OnboardingStep(
        key="ai_relationship",
        category="ai_relationship",
        tags=["ai-preferences", "values", "profile"],
        question="Last one — what do you actually want from Ember? What would make this genuinely useful to you?",
    ),
]
