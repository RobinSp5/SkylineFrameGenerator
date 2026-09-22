.PHONY: setup dev backend frontend test e2e build

setup:
	cd backend && uv sync
	cd frontend && npm install && npx playwright install chromium

backend:
	cd backend && uv run uvicorn --factory app.main:create_app --reload --port 8000

frontend:
	cd frontend && npm run dev

dev:
	cd frontend && npx concurrently -k -n api,web "cd ../backend && uv run uvicorn --factory app.main:create_app --reload --port 8000" "npm run dev"

test:
	cd backend && uv run pytest -q
	cd frontend && npx vitest --run

e2e:
	cd frontend && npx playwright test

build:
	cd frontend && npm run build
