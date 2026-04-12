# Ember-2 v0.15.0

**TLDR:** v0.15.0 is a quality of life release. Ember got smarter about when to search, better at citing where her answers come from, and faster overall.

---

## Web Search

- **Ask-first mode:** Ember asks before searching. She flags the gap and checks if you want her to look it up.
- **Autonomous mode:** Toggle in Settings if you'd rather she just handle it.
- **Broader triggers:** Current events, financial data, sports, culture, and time-sensitive queries now route to web search without you having to spell it out.

## Memory and Retrieval

- **Temporal decay:** Recent records score higher than older ones on the same query. Last week outweighs last month.
- **Source citations:** Every response now shows where it came from. Source: Vault, Source: Web Search, Source: LLM, or any combination. No more mystery meat answers.

## Character and Behavior

- **Parenthetical question loop fixed:** Ember was ending responses with questions wrapped in parentheses and continuing the pattern after you asked her to stop. Fixed - she respects explicit objections to questions.
- **Topic fixation fixed:** Ember was returning to declined topics repeatedly. Fixed - when you say you don't want to talk about something, she drops it. She may ask once later from genuine curiosity, but she won't fixate.
- **Lowercase bug fixed:** Ember was lowercasing every response due to a bug in how internal reasoning traces were stripped from the stream. Fixed.
- **Constitutional review overhaul:** Review now asks three targeted questions instead of sending the full constitution. Faster and more accurate.

## Security and Privacy

- **Change PIN:** Now available in Settings under Security.
- **Disk encryption check:** Ember checks whether full disk encryption is enabled (BitLocker, FileVault, LUKS) and shows a reminder in Settings if it isn't. Your vault files are readable if someone extracts your drive without it. The fix is an OS setting, not Ember's job.

## Services

- **Auto-start:** The API starts automatically at login on Windows, macOS, and Linux.
- **Service controls:** Stop and restart the API from the glowing dot in the corner of the UI. It breathes when things are fine.

## Developer Tools

- **Vault switcher:** Switch between live, demo, and test vaults without restarting. Enable developer mode during install or via EMBER_DEV_MODE=true in .env. Don't demo with your real vault.
