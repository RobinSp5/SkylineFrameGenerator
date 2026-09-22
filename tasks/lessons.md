# Lessons

Patterns learned from corrections in this project. Review at session start.

## 2026-09-22 — README language
- **Correction:** Robin: "die readme sollte immer auf englisch sein!" The MVP and Phase 2 READMEs were written in German because the chat, spec and plan are German.
- **Rule:** Repository-facing documentation (README, code comments, commit messages, CLI/API help text) is written in English regardless of the conversation language. German stays in chat, specs and plans for Robin, and in the UI strings, which are deliberately German for the German-speaking user.
- **Check before finishing a docs task:** grep README for umlauts/German words; if any, translate before committing.
