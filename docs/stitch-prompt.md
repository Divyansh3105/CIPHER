# Google Stitch prompts — CIPHER frontend redesign

Working notes for regenerating the UI in [Stitch](https://stitch.withgoogle.com). Stitch
designs **one screen per prompt**, so this is a base prompt (paste once, per screen) plus
five screen prompts. The last section lists what must survive the redesign, because several
UI details in this app are load-bearing rather than decorative.

---

## How to run it

1. Start a new Stitch project. Use **Experimental mode** for the first pass on the chat
   screen — it holds long prompts better; switch to Standard for the remaining screens once
   the style is set.
2. Upload the sample images **with the first message**, and say what to take from each one
   (layout? colour? density? type scale?). Stitch copies everything from a reference unless
   you tell it what you want.
3. Paste **Base prompt + Screen 1**. Iterate on that screen until the style is right.
4. For every later screen, paste **Base prompt + Screen N** into a *new* chat so it does not
   drift from an earlier screen's edits.

---

## Base prompt (prefix every screen prompt with this)

> I am redesigning CIPHER, a multi-persona AI assistant — a personal JARVIS. It is a
> desktop-first web app (Next.js + Tailwind), used at a laptop for long sessions, often
> hands-free by voice. The user is one person, its owner. There is no marketing surface, no
> onboarding, no pricing page — every screen is a working tool for someone who already knows
> what the app does.
>
> Design language: calm, dense, technical, and quiet. Neutral zinc/grey chrome, generous
> whitespace inside cards but tight vertical rhythm in lists, small type (13–14px body),
> clear hierarchy through weight and spacing rather than through colour or borders. No
> gradients, no glassmorphism, no glow, no neon "AI" styling, no decorative iconography, no
> hero sections. It should read like a well-made developer tool — closer to Linear or Vercel
> than to a consumer chatbot.
>
> Colour is reserved for meaning, never decoration. There are exactly four semantic uses:
> indigo for the JARVIS persona, amber for FRIDAY, red for ULTRON, and amber/red banner
> strips for degraded states. Everything else is greyscale.
>
> Support both light and dark themes with the same layout; dark is the primary.
>
> A persistent left rail (72px collapsed, icon + label) navigates five destinations: Chat,
> Memory, Documents, Agents, Control. Today each page cross-links ad hoc in its own header —
> replace that with one real navigation shell shared by every screen.
>
> Now design this screen:

---

## Screen 1 — Chat (the main screen)

> **Chat.** A three-column layout: the left nav rail, a conversation sidebar, and the
> conversation itself.
>
> - **Conversation sidebar:** a "New conversation" action and a list of past conversations,
>   each showing its title, relative time, and a small coloured dot for the persona it was
>   last answered in.
> - **Header bar** above the thread, holding: a three-way persona switcher (JARVIS / FRIDAY /
>   ULTRON) as a segmented control, and a small "model chip" on the right showing which model
>   is answering — it reads `Auto` normally, or a pinned model name like `Gemini 3.8 Flash`
>   with a way to unpin it.
> - **Message thread:** user messages and assistant messages, visually distinct without heavy
>   bubbles. Every assistant message carries a small persona label in that persona's colour.
>   Below an assistant message, two optional chip rows: **Recalled** chips (facts the
>   assistant remembered, clickable) and **Citation** chips (`filename · page 4`).
> - **Notice strips** that appear directly under the header when something is degraded — a
>   thin amber strip ("answered on the fallback model") and a thin red strip ("this reply was
>   filtered"). Show both in the design so I can see the treatment.
> - **Composer** at the bottom: a growing textarea, a send button, and a microphone toggle
>   button with a clear pressed/unpressed state.
> - **Voice status line** between the composer and the thread, showing one of four states —
>   `Listening…`, `Thinking…`, `Speaking…`, or a dropped-input message like "Didn't catch
>   that, JARVIS was still answering". It must be legible from across a desk, since in a
>   voice conversation nothing else on screen says whose turn it is.

---

## Screen 2 — Memory

> **Memory.** Everything the assistant remembers about its owner, with two views behind a
> segmented toggle in the header: **List** and **Galaxy**.
>
> - **List view:** memory cards in a single column. Each card shows the remembered fact, a
>   type tag (preference / fact / event), when it was captured, and inline edit and delete
>   actions that only appear on hover.
> - **Galaxy view:** a full-bleed dark canvas holding a 3D force-directed graph of memories —
>   spheres connected by thin links, sized by how connected they are and coloured by memory
>   type. Overlay a floating legend and a floating inspector panel that appears when a node is
>   selected, showing that memory's text, type, date, and its nearest neighbours. Caption the
>   links "nearest by similarity", not "related".
> - A header search field, a memory count, and a destructive "Forget everything" action kept
>   visually far from everything else.

---

## Screen 3 — Documents

> **Documents.** Files the assistant can answer questions about.
>
> - A drop zone for PDF, DOCX, TXT and MD at the top — understated, not a giant dashed box.
> - A table or list of uploaded documents: filename, type, page count, number of embedded
>   passages, upload time, and an ingestion status that can be `queued`, `processing`, `ready`
>   or `failed`. Show all four states in the design.
> - Selecting a document opens a detail panel listing its extracted passages with page
>   numbers, so the owner can see exactly what the assistant can quote.
> - An empty state for when nothing has been uploaded yet.

---

## Screen 4 — Agents

> **Agents.** An activity trail for the multi-agent system, read after the fact to answer
> "why did it answer that way".
>
> - Top: three agent cards — Research, Coding, Memory — each with a one-line description and
>   an enable/disable toggle.
> - Below: a run history table, newest first. Columns: which agent ran, the routing decision,
>   duration in milliseconds, outcome (`ok`, `timeout`, `failed`, `skipped`), and a timestamp.
>   Failed and timed-out runs must be as visible as successful ones — this view exists to show
>   what went wrong, so do not grey failures out.
> - Expanding a row reveals what the agent was asked and what it contributed.

---

## Screen 5 — Control

> **Control.** The permission and safety console for actions the assistant can take on the
> owner's machine. This screen's job is to make the current state unmistakable at a glance.
>
> - A prominent master state banner at the top with two designs: **automation disabled**
>   (the default and safe state — calm, grey, clearly inert) and **automation enabled**
>   (alert, amber). Show both.
> - A large, unambiguous **kill switch**.
> - An allowlist of exactly four permitted actions, each with a risk tier badge:
>   `read-only`, `session`, and `sensitive`. Make the three tiers visually distinct.
> - A list of active grants with what each covers and when it expires.
> - An audit log at the bottom: every attempt including denials, with timestamp, action,
>   outcome, and reason. Denials should be as prominent as successes.

---

## What must survive the redesign

Not style preferences — these encode behaviour, and a redesign that drops them makes the app
lie about its own state.

| Element | Why it cannot be decorative |
| --- | --- |
| Persona accent colours (JARVIS indigo, FRIDAY amber, ULTRON red) | Used only on the message label and the sidebar sublabel. If they spread into the chrome, the reply's voice stops being identifiable at a glance. |
| The four voice states, including "dropped" | The dropped state outranks "thinking" deliberately: a reply in flight is the *reason* input was dropped, and showing "thinking" leaves the owner waiting for an answer to a question that was never sent. |
| Amber and red notice strips | Amber = answered on the fallback model or transcription moved to the server. Red = the ULTRON safety filter changed the reply. Both must be visible without being dismissible-by-accident. |
| Mic button pressed state | It reflects the microphone switch only — never the conversation state. It has been wrong before precisely because it was derived from the voice state machine. |
| Recalled and citation chips | Both are claims about provenance. A citation reads `file · page N` because chunks never span pages, so the page number is true. |
| The model chip's `Auto` vs pinned distinction | A pinned model never falls back. The whole point of naming a model is knowing which one answered. |
| Automation-disabled as the visibly default state | A fresh install is inert. The screen should look inert. |
