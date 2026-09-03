# LinkedIn voice exemplars

These are posts Carlos actually published. They are the register to match:
a concrete image or number in the first line, one system explained in plain
terms, one result with a number, and one idea he is willing to defend over
everything else. Never copy sentences from them; match the shape and the
confidence.

## Exemplar 1 — CARMEN (AIiH 2026, Imperial College London)

In many Colombian homes, the early warning system is a grandmother.

She's the one who notices you look off, who knows what you took and when it's time to go. But the nearest hospital can be hours away, and cardio-cerebrovascular disease is the leading cause of death in Colombia — nearly 1 in 3.

Last week at Imperial College London I presented CARMEN at AIiH 2026: a multi-agent system that tries to learn her questions.

What it is:

→ Six vital signs, reported through a Telegram bot. No app to install. The whole kit costs ~€46.

→ A forecasting engine that reaches 0.78 AUROC twelve hours ahead of deterioration at the home cadence, and holds 0.76 on a hospital it had never seen. The standard clinical rule sits near chance.

→ Two LLM agents orchestrated in LangGraph — a nurse that speaks like family, a physician agent grounded by RAG over clinical guidelines. Neither diagnoses. Neither prescribes.

The part I'd defend hardest isn't the model. It's the constraints.

Deterministic overrides that fire regardless of what any model outputs. Escalation that only moves upward automatically — lowering a risk tier is a human, signed act. Every report reaching a real clinical team. Agents advise, the clinician decides.

Most agent projects break in production because nobody wrote down what the agent isn't allowed to do. In healthcare you don't get to skip that step. That discipline turns out to be portable.

Vignette study with cardiologists under way. Home pilot in rural Colombia next.

## Exemplar 2 — CEMRACS 2026 hydrology

Three parameters per country beat a random forest and a neural net.

Six weeks at CEMRACS 2026 (CIRM, Luminy), building the physical layer of a model that carries drought from rainfall to sovereign default risk. My part was the hydrology.

The hard question: if a river runs dry, how much electricity does a country actually lose?

log CF = α + β·log Q(this year) + ρ·log Q(last year)

Capacity factor against river discharge now, and discharge last year — that second term because reservoirs remember. Fit on EIA generation records, then projected on ISIMIP climate for 151 countries across three emission scenarios.

Out of sample, it beat both a random forest and a neural net.

Not because trees and networks are bad. Because a dam is a physical object with a memory of about a year. Write that structure into the model and you don't have to learn it from data you don't have.

Interpretability wasn't the price of accuracy here. It was the source of it.

What the projections show: the median country barely moves, the tail collapses. Mauritania, Mozambique, Nicaragua, Morocco, Tunisia, Honduras. Zambia already runs 88% of its grid on water.

In April 2024, Colombia's reservoirs were weeks from national rationing. Not a scenario for 2100.

## What these have in common

- The first line is a claim or an image, never a description of a paper.
- One number that a hiring manager can repeat.
- The model or system is explained in one or two lines of plain language.
- One sentence names what he would defend hardest.
- The signal from outside is evidence; his work is the subject.
- No emojis in the body, no generic call to action, no hashtags soup.
