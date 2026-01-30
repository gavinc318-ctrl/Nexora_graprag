# rag_sample

## UI vs Service Separation

To keep the architecture clean and API-ready:
- UI modules (e.g., `gradio_ui.py`, `graphmng_gr.py`) must only handle display and input wiring.
- All functional/business logic must live in service/core modules (e.g., `core.py`, `graphmng_service.py`, or other non-UI modules).
- When adding new features, do not place functional logic inside UI modules.
# Nexora_graprag
