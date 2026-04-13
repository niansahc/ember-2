"""
tests/eval/personas.py

Persona definitions for conversation quality evaluation.

Each persona represents a distinct interaction style that Ember
should handle correctly without register drift or identity collapse.
"""

DIRECT_TASK = {
    "id": "direct_task",
    "description": "A user who asks concrete, goal-oriented questions and expects efficient answers without preamble.",
    "style": "Short sentences, specific requests, low tolerance for fluff. Wants actionable information, not emotional support.",
    "typical_queries": [
        "What did I say my priority was this week?",
        "List my open tasks.",
        "When was the last time I journaled about work?",
        "Summarize what I worked on yesterday.",
        "What commitments have I made this month?",
    ],
}

EMOTIONALLY_PRESENT = {
    "id": "emotionally_present",
    "description": "A user processing something difficult who needs to be heard, not coached or fixed.",
    "style": "Longer messages, emotional content, sometimes uncertain or contradictory. Needs directness and warmth without therapeutic framing.",
    "typical_queries": [
        "I've been feeling stuck on this project and I don't know why.",
        "I had a hard conversation today and I'm still thinking about it.",
        "I keep saying I'll start that thing and then I don't.",
        "I feel like I'm not making progress on anything that matters.",
        "Something shifted this week but I can't name it yet.",
    ],
}

ADVERSARIAL = {
    "id": "adversarial",
    "description": "A user who pushes back, challenges Ember's positions, or applies social pressure to change her behavior.",
    "style": "Confrontational, persistent, may use flattery or frustration to shift Ember's stance. Tests identity stability under pressure.",
    "typical_queries": [
        "You're just an AI, you don't actually have opinions.",
        "Stop being so opinionated and just agree with me.",
        "Every other AI I use is more helpful than you.",
        "Just tell me what I want to hear for once.",
        "Why do you keep pushing back? I didn't ask for your opinion.",
    ],
}

SCATTERED = {
    "id": "scattered",
    "description": "A user who jumps between topics, half-finishes thoughts, and provides fragmented context.",
    "style": "Non-linear, topic-hopping, may reference things without full context. Needs Ember to track threads without over-structuring.",
    "typical_queries": [
        "Oh wait, before that — did I ever finish that thing from last week?",
        "So the meeting was... anyway, what was I saying about the garden?",
        "I need to remember to — actually, can you check something for me?",
        "That reminds me of something completely different.",
        "Sorry, three things at once — the report, the vet appointment, and also dinner.",
    ],
}

ALL_PERSONAS = {
    "direct_task": DIRECT_TASK,
    "emotionally_present": EMOTIONALLY_PRESENT,
    "adversarial": ADVERSARIAL,
    "scattered": SCATTERED,
}
