# Velveteen Insight Engine

## Qué es
Sistema editorial y de portafolio asistido por IA dentro de The Velveteen Project, un founder-led applied decision systems lab.

## Filosofía
- Nada de humo
- Nada de sobreingeniería
- Nada de automatización vacía
- Preferir reglas deterministas cuando basten
- El LLM no controla la lógica del sistema
- Código limpio, tipado y mantenible

## Stack
Python 3.11+, FastAPI, Pydantic, httpx, SQLite, pytest, ruff, mypy.

## Estado actual
- Fase 1: FastAPI, webhook Telegram, SQLite
- Fase 2: parsing Telegram, reply/url/voice flags, classifier determinista
- Fase 3: voz + transcripción no fatal
- Fase 4: discovery service con arXiv + Hacker News, ranking heurístico, persistencia en signals

## Distinciones importantes
- `message_type` = tipo técnico del input
- `channel` = carril editorial
No mezclarlos.

## Qué evitar
- No usar LangChain salvo necesidad real
- No scraping HTML frágil
- No LLM para lógica de ranking/clasificación si una heurística basta
- No reescribir fases previas sin razón fuerte
