# One command for the common tasks. On Windows without `make`: npm run stack (see package.json),
# or run the docker compose line itself.
.PHONY: dev down test logs images

dev:            ## API + voice worker + jobs worker + client dashboard (:5173) + platform app (:5174) + landing page (:4321) + MongoDB + Redis
	docker compose up --build

down:           ## stop everything (data volumes are kept)
	docker compose down

test:           ## lint, type check and tests on Linux
	docker compose run --rm tests

logs:           ## follow the backend logs
	docker compose logs -f api worker sales-worker jobs

images:         ## build the three production backend images locally
	docker build --target api    -t receptionist-api    AI-receptionist-1
	docker build --target worker -t receptionist-worker AI-receptionist-1
	docker build --target jobs   -t receptionist-jobs   AI-receptionist-1
