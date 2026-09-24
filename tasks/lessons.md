# Lessons

Patterns learned from corrections in this project. Review at session start.

## 2026-09-22 — README language
- **Correction:** Robin: "die readme sollte immer auf englisch sein!" The MVP and Phase 2 READMEs were written in German because the chat, spec and plan are German.
- **Rule:** Repository-facing documentation (README, code comments, commit messages, CLI/API help text) is written in English regardless of the conversation language. German stays in chat, specs and plans for Robin, and in the UI strings, which are deliberately German for the German-speaking user.
- **Check before finishing a docs task:** grep README for umlauts/German words; if any, translate before committing.

## 2026-09-23 — Subagent fan-out burned the session limit
- **What happened:** One research subagent was given a broad worldwide licence survey; it spawned ~20 of its own subagents, and all but one died on the account session limit, losing most of the work.
- **Rule:** When dispatching a research agent, state explicitly "do not dispatch subagents". Split broad research into a few narrow, sequential dispatches instead, or do the decisive checks yourself with curl/WebFetch — targeted primary-source fetches proved faster and more reliable than the fan-out.
- **Check:** Before dispatching, ask "could this agent reasonably fan out?" If yes, forbid it in the prompt.

## 2026-09-23 — Launch video language
- **Correction:** Robin: "das video sollte nur englisch sein". The /brag video kept the German UI labels verbatim ("Ort suchen", "Generieren") next to English overlay copy.
- **Rule:** Public-facing marketing output (videos, share copy, launch posts) is English only. When a recreated UI is German, translate its labels in the recreation; never quote the German UI verbatim.
- **Check:** Before rendering, grep the composition for umlauts and the known German UI strings.
