# Frontend

React + TypeScript + Vite operational UI. It uses relative `/api/v1` requests behind Nginx or
`VITE_API_BASE_URL` for direct local development.

## Development

```bash
npm ci
npm run dev       # http://127.0.0.1:5173
npm run build
```

The local FastAPI default is `http://127.0.0.1:8010`. Keep `VITE_API_BASE_URL` empty when the
built SPA is served by Nginx on `http://127.0.0.1:8080`.

## Browser Tests

```bash
npx playwright test
```

The default for both browser and API traffic is the production-shaped local Nginx entry at
`http://127.0.0.1:8080`. Override `PLAYWRIGHT_FRONTEND_BASE_URL` and
`PLAYWRIGHT_API_BASE_URL` only when debugging Vite or FastAPI directly.

Set `PLAYWRIGHT_EXCEL_SAMPLE_PATH` to a Tekla delimited export or workbook containing the
required steel-list columns to include the successful Excel Final upload/download digest test.
A generic `.xls` or `.xlsx` workbook is an intentional negative input. The suite creates
deterministic job fixtures for retry and DXF download paths rather than relying on existing rows.

Authentication state is stored in `sessionStorage`; SSE authentication uses the HttpOnly cookie
set by the login/refresh endpoints. Download retries always obtain a new signed URL.
