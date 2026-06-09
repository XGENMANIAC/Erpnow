# dashboard — React Dashboard (Phase 3)

This directory will contain the React-based CRM dashboard for managers and agents.

## Planned Phase 3 features

- **Live conversation view** — real-time WhatsApp/SMS conversations per customer.
- **Order pipeline** — Kanban board of Sales Orders (Quotation → Order → Invoice → Paid).
- **Customer 360** — unified view of customer data from ERPNext + chat history.
- **Analytics** — sales performance, M-Pesa collection rates, agent response times.
- **Agent config** — configure LLM agent behaviour without code changes.

## Tech stack (planned)

- React 18 + TypeScript
- Vite build tooling
- TanStack Query for data fetching
- Tailwind CSS + shadcn/ui
- WebSocket connection to the integration service for live updates

## Getting started (Phase 3)

```bash
cd dashboard
npm install
npm run dev        # http://localhost:5173
npm run build      # Production build
```

The dashboard connects to the integration service at `VITE_API_URL` (default: `http://localhost:8001`).
