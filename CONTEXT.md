# Ember-2

Ember-2 is a local, private personal intelligence system. This file fixes one word per concept for conversations and memory, so code, ADRs and plans say the same thing.

## Language

### Conversations

**Conversation**:
A titled thread of exchanges, optionally in a project.
_Avoid_: session, chat

**Turn**:
One side of a conversation: a user turn (what the user sent, exactly as sent) or an assistant turn (Ember's reply).

**Exchange**:
A user turn plus everything Ember does in response to it.
_Avoid_: turn, round, completion

**Exchange outcome**:
How an exchange ended when it stored no assistant turn: failed (Ember could not produce or save a reply) or interrupted (the client disconnected before the reply was stored).
_Avoid_: turn outcome, error turn

**Retry**:
An exchange whose user turn repeats the user turn of the exchange just before it, when that exchange stored no assistant turn.
_Avoid_: regenerate

**History**:
A conversation's turns as the user sees them on reload.
_Avoid_: transcript

## Relationships

- A **Conversation** has many **Exchanges**, in order. It is created with its first user **Turn**, so Ember never creates an empty one.
- An **Exchange** has exactly one user **Turn** and at most one assistant **Turn**.
- An **Exchange** has one exchange id, which is also the response id the client receives. Its **Turns** and its **Exchange outcome** carry it.
- Every stored user **Turn** ends in exactly one assistant **Turn** or exactly one **Exchange outcome**, unless Ember's process dies mid-exchange.
- Ember saves a reply before sending any of it. If the save fails, the user gets a storage error instead of the reply.
- A user **Turn** keeps what the user sent, exactly as sent. Ember adds nothing to it, and images are counted, not kept.
- Ember's own notes to the model (task, timer and search notes) never enter a user **Turn**, and nothing Ember works out about the user reads them.
- **History** shows every stored **Turn** in order, except that a user **Turn** and its **Retries** appear once. It never shows an **Exchange outcome**, and the vault keeps every **Exchange**.
- Retrieval and reflection share one filter and see only the **Turns** that pass it. Only **History** shows every **Turn**.

## Example dialogue

> **Dev:** "The model server was down, so Ember never replied. How many turns did that exchange produce?"
> **Domain expert:** "One. The user turn was stored when the message arrived. There is no assistant turn, because Ember never replied, so the exchange outcome is failed."
> **Dev:** "The user pressed Try again and it worked. What does **History** show after a reload?"
> **Domain expert:** "The message once, then the reply. The vault holds two exchanges: the failed one with its outcome, and the retry with its assistant turn."
> **Dev:** "The user closed the tab while the reply was still appearing. What is stored?"
> **Domain expert:** "The whole reply. Ember stores the reply before any of its text is sent, so that exchange already has its assistant turn and gets no outcome. Had the tab closed before the reply was stored, the exchange would end interrupted."
> **Dev:** "What does the user turn hold when someone sends only a photo?"
> **Domain expert:** "Empty text and an image count of one. The photo itself is not kept."

## Flagged ambiguities

- "turn" was used both for one side and for the pair (the plan's `turn_id`; "two records per turn" in the TDD). Resolved: a **Turn** is one side; the pair is an **Exchange**.
- "what the user typed" (plan decision A) is not what the client sends: the UI adds a note to the text when documents are attached, and the backend fills image-only messages with a placeholder sentence for the model. Resolved: a user **Turn** is what the client sent, unchanged by Ember.
- "regenerate" (UI code) re-sends the last user message whether or not its exchange has a reply, and "Try again" on the error bubble is the same call. Resolved: only a re-send after an exchange with no assistant turn is a **Retry**; regenerating after a reply is an ordinary new **Exchange**.
- "stop" (the UI's stop button) is not "interrupted". Today the stop button only stops the display; the request keeps running and Ember stores the whole reply. Resolved: only a client disconnect interrupts an **Exchange**.
- "session" meant three things: the storage and wire name for a **Conversation** (`session_id`, `X-Session-ID`, `memory/session/`), the UI login session in ADR-012, and an eval flag (`X-Test-Session`). Resolved: the thread is a **Conversation**. Code names and existing feature names, such as session reflection, stay as they are.
