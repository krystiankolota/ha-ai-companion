# Frontend

The frontend uses React, Vite, and Tailwind CSS.

## Build

From the `frontend` directory, run:

```bash
npm ci
npm run build
```

Vite writes the bundle to `ha-ai-companion/static/dist`. The post-build script copies it and the shared backend files to the HACS component.

## Development

Run `npm run dev` from the `frontend` directory. The Vite configuration does not define an API or WebSocket proxy. Configure one if the development server must reach the backend.

See the [development guide](../ha-ai-companion/DOCS.md#development) for backend setup and tests.
