"""
scripts/seed_identity_template.py

Template for seeding personal profile records into the Ember-2 vault.

HOW TO USE
----------
1. Copy this file to your private_vault directory (outside the repo):
       cp scripts/seed_identity_template.py ../private_vault/seed_identity.py

2. Edit ../private_vault/seed_identity.py — replace all placeholder text
   with your real information.

3. Run it from the repo root:
       python ../private_vault/seed_identity.py

4. Verify records were written:
       python tools/inspect_indexes.py

GUIDELINES
----------
- Each record must be at least 40 characters or it will be silently skipped.
- Write in first person, full sentences — not key-value pairs.
- Use `category` in metadata to group related records.
- Safe to re-run: exact duplicates are skipped automatically.
- Keep this template file committed. Keep your real seed file in private_vault (gitignored).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.service import MemoryService

memory_service = MemoryService()

PROFILE_RECORDS = [
    # --- Identity ---
    # Your name, pronouns, any identity context you want Ember to know.
    {
        "text": "My name is [Name]. I go by [Nickname]. My pronouns are [pronouns]. [Any other identity context].",
        "tags": ["identity", "profile"],
        "metadata": {"category": "identity"},
    },

    # --- Location and environment ---
    # Where you live, your physical context, relationship to place.
    {
        "text": "I live in [City, State/Country]. [Describe your home environment and any relevant context about where you live and work].",
        "tags": ["location", "home", "profile"],
        "metadata": {"category": "environment"},
    },

    # --- Cognitive profile ---
    # How you think, process information, and learn best. Include any
    # neurodivergence, learning preferences, or cognitive style notes.
    {
        "text": "I think and process information by [describe your cognitive style]. I learn best through [formats or approaches that work for you].",
        "tags": ["cognitive", "profile"],
        "metadata": {"category": "cognitive_profile"},
    },

    # --- Health context ---
    # Chronic conditions, energy variability, anything that affects
    # how you work day to day. Only include what you want Ember to know.
    {
        "text": "I live with [health conditions or context]. My energy and capacity vary. [Any notes about what this means for your productivity or pacing].",
        "tags": ["health", "profile"],
        "metadata": {"category": "health"},
    },

    # --- Professional identity ---
    # Your role, domain, skills, and work context.
    {
        "text": "I work as a [role] in [domain or industry]. My areas of expertise include [list key domains, skills, or tools].",
        "tags": ["work", "profession", "profile"],
        "metadata": {"category": "professional"},
    },

    # --- Current projects ---
    # What you are actively building or working on right now.
    {
        "text": "My current major project is [project name] — [brief description of what it is, why it matters, and the scope of the work].",
        "tags": ["project", "profile"],
        "metadata": {"category": "current_project"},
    },

    # --- Personal interests and practices ---
    # Hobbies, creative work, spiritual practice, anything that shapes
    # how you spend your time and what you care about.
    {
        "text": "Outside of work I spend time on [interests, practices, or pursuits]. [Brief description of what these mean to you or how they show up in your life].",
        "tags": ["interests", "profile"],
        "metadata": {"category": "personal"},
    },

    # --- Communication preferences ---
    # How you want Ember to communicate with you. Be specific.
    {
        "text": "I prefer communication that is [describe your style preferences]. I dislike [what to avoid]. [Anything else about tone, format, or approach].",
        "tags": ["communication", "preferences", "profile"],
        "metadata": {"category": "communication"},
    },

    # --- What you value from AI ---
    # What makes Ember useful to you. What kind of relationship you want.
    {
        "text": "What I value most from an AI system: [list what matters to you — synthesis, pattern recognition, continuity, precision, etc.]. [How you want to feel in the interaction].",
        "tags": ["ai-preferences", "values", "profile"],
        "metadata": {"category": "ai_relationship"},
    },

    # --- Relationships (optional) ---
    # People in your life who are relevant to your work or context.
    # Only include what is useful for Ember to know.
    {
        "text": "I live with [person/people]. [Brief context about who they are and how they relate to your work or life]. [Any relevant notes].",
        "tags": ["relationships", "profile"],
        "metadata": {"category": "relationships"},
    },
]


def seed_identity() -> None:
    print(f"Seeding {len(PROFILE_RECORDS)} profile records...\n")

    written = 0
    skipped = 0

    for record in PROFILE_RECORDS:
        result = memory_service.write(
            text=record["text"],
            memory_type="profile",
            source="seed_identity",
            tags=record.get("tags", []),
            metadata=record.get("metadata", {}),
        )

        preview = record["text"][:60].replace("\n", " ")

        if result:
            print(f"  + {preview}...")
            written += 1
        else:
            print(f"  - skipped (duplicate): {preview}...")
            skipped += 1

    print(f"\nDone. {written} written, {skipped} skipped.")


if __name__ == "__main__":
    seed_identity()
