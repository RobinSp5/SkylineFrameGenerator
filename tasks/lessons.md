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

## 2026-09-24 — `git commit -a` swept unrelated work into a commit
- **What happened:** A one-line terrain refactor was committed with `git commit -qam` while Robin's uncommitted frontend translation, `.gitignore` and `tasks/lessons.md` edits sat in the working tree. All of it landed in `02eb7fb` under a terrain message and was pushed to `main`.
- **Rule:** Never use `git commit -a` / `-am` in this repo. Stage explicit paths (`git add backend/...`) and check `git diff --cached --stat` before every commit when the working tree has changes that are not mine.
- **Check:** At session start, note which files are already modified; before each commit, confirm none of them are staged unless the user asked for it.

## 2026-09-24 — Redesign hid a primary control
- **Correction:** Robin: "der höhenmultiplikator ist weg, der muss da sein!" The UI redesign moved the building height factor into a collapsed "Plate and heights" section, and the dark map (inverted OSM tiles) was unreadable.
- **Rule:** A redesign keeps every control the user already relies on at the same level of visibility unless they agree to demote it. Collapsible sections only take settings people rarely touch (plate thickness), never the ones that shape the model (size, heights, detail).
- **Rule:** Never ship a dark map by inverting light raster tiles; check the dark map at real zoom in a screenshot before calling a theme done.
- **Check:** Before finishing a UI change, list every input id before and after and confirm none moved into a collapsed or hidden container.

## 2026-09-24 — Fix the cause for the whole world, not the city in front of you
- **Correction:** Robin: "du sollst jetzt ja nicht für jede stadt irgendwas raussuchen, sondern es sollte eine lösung geben sodass es überall funktioniert" and "es soll weltweit gehen, du brauchst nicht für alles tests machen!" I had tuned thresholds on Frankfurt, then started bisecting Marburg and Darmstadt, then a 25-place slicing sweep.
- **Rule:** When a printability or quality problem shows up in one place, look for the rule that makes it impossible by construction (fully supported from below, nothing thinner than two lines, geometry built exactly) before tuning a threshold. A threshold measured on one city is a symptom fix.
- **Rule:** Verify a general fix with one or two spot checks in places that exercise different code paths (LoD2 vs OpenStreetMap, flat vs terrain), not a sweep. Ask before starting anything that runs for half an hour.
