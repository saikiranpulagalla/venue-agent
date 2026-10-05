# AI / Tool Disclosure

Update this file before submission with the exact tools actually used by the team.

## Runtime AI

- Optional planner: Google Gemini API, default model `gemini-3.8-flash` when `GEMINI_API_KEY` is configured.
- Runtime role: choose among server-generated, pre-simulated candidate IDs under explicit constraints.
- The runtime model does **not** receive arbitrary external tools, does not create mutation parameters, and does not certify the result.
- If the model is unavailable or returns an invalid plan, a deterministic constrained planner is used.

## Runtime deterministic tools

- FastAPI / Pydantic
- python-pptx
- LibreOffice Impress (headless rendering)
- PyMuPDF
- pytest

## Development AI tools

Before submission, list all significant coding/AI assistants actually used during the hackathon and what they were used for. Do not omit them if the WCC submission asks for disclosure.
