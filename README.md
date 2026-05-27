# BDA Final — Backend for LLM Integration

This repository contains a simple frontend demo and a minimal Node.js backend that enriches prompts with real CWA weather, disaster data, and Taiwan holiday data. Gemini is opt-in only, so the app works even when no model key is configured.

Environment variables (create a `.env` file):

- `GEMINI_API_KEY` — your API key for the generative model provider.
- `MODEL_NAME` — optional, default `models/gemini-mini`.
- `ENABLE_GEMINI` — optional, set to `true` only if you want the backend to call Gemini. Defaults to local fallback generation.
- `PORT` — optional, default `3000`.
- `CWA_WEATHER_API_KEY` — required for fetching current temperature, current weather, earthquake reports, and weather alerts from the Taiwan CWA open data platform.

Install & run:

```bash
npm install
npm start
```

Endpoint:

- `POST /api/generate` — accepts JSON `{ userKey, weather, festival, userData }` and returns `{ text, promptUsed, context }`.
  - `userData` should include product and user fields the frontend knows about, e.g. `{ name, cityName, mainProduct, mainPrice, recProduct, recPrice, intentLabel }`.
  - `context.weather` contains the parsed CWA station, temperature, and weather text.
  - `context.holiday` contains the next Taiwan public holiday within 30 days, if any.
  - `context.disaster` contains earthquake and weather-alert summaries.

Note: This example uses the public Generative Language endpoint pattern only when `ENABLE_GEMINI=true`. If `CWA_WEATHER_API_KEY` is missing, the backend will not be able to fetch the CWA context and the prompt will degrade gracefully. Holiday data comes from the local Taiwan holiday library and does not require a key.
