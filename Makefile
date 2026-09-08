.PHONY: check test lint migrate makemigrations migrations-check run up down superuser install \
        start stop status db-wait verify secrets

VENV_PYTHON := .venv/bin/python
VENV_RUFF := .venv/bin/ruff
VENV_DETECT_SECRETS := .venv/bin/detect-secrets
PID_FILE := .cvbuilder-server.pid
LOG_FILE := .cvbuilder-server.log
DB_SERVICE := db
DB_WAIT_RETRIES := 30

install:
	python3 -m venv .venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt

check:
	$(VENV_PYTHON) manage.py check

test:
	$(VENV_PYTHON) manage.py test

lint:
	$(VENV_RUFF) check .

migrate:
	$(VENV_PYTHON) manage.py migrate

makemigrations:
	$(VENV_PYTHON) manage.py makemigrations

migrations-check:
	$(VENV_PYTHON) manage.py makemigrations --check --dry-run

secrets:
	$(VENV_DETECT_SECRETS) scan --exclude-files '\.venv/' --exclude-files '\.git/'

# verify: everything a milestone-completion check should run before commit/handover
# (docs/MILESTONE_COMPLETION_CHECKLIST.md). Fails on the first failing step.
verify: check migrations-check test lint
	@echo "All verification checks passed."

run:
	$(VENV_PYTHON) manage.py runserver

superuser:
	$(VENV_PYTHON) manage.py createsuperuser

up:
	docker compose up -d

down:
	docker compose down

# --- Combined, safe app+database lifecycle -------------------------------------------------
#
# start: brings up the database (idempotent -- never recreates/loses data if already running),
#   waits for it to actually accept connections, applies migrations, then starts the Django
#   dev server in the background (PID recorded in $(PID_FILE), logs in $(LOG_FILE)). Safe to
#   run again while already running -- it detects the existing server and does nothing further.
#
# stop: stops only the server process this Makefile started (verified via the recorded PID,
#   never a blind "kill whatever is on port 8000"), then stops the database with a plain
#   `docker compose down` -- never `-v`, so the data volume is never destroyed. Safe to run
#   even if nothing is currently running.

db-wait:
	@echo "Waiting for the database to accept connections..."
	@for i in $$(seq 1 $(DB_WAIT_RETRIES)); do \
		if docker compose exec -T $(DB_SERVICE) \
			pg_isready -U "$${POSTGRES_USER:-cvbuilder}" -d "$${POSTGRES_DB:-cvbuilder}" >/dev/null 2>&1; then \
			echo "Database is ready."; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Database did not become ready within $(DB_WAIT_RETRIES)s." >&2; \
	exit 1

start: up db-wait migrate
	@if [ -f $(PID_FILE) ] && kill -0 "$$(cat $(PID_FILE))" 2>/dev/null; then \
		echo "App server already running (PID $$(cat $(PID_FILE)))."; \
	else \
		rm -f $(PID_FILE); \
		echo "Starting app server..."; \
		nohup $(VENV_PYTHON) manage.py runserver > $(LOG_FILE) 2>&1 & \
		echo $$! > $(PID_FILE); \
		sleep 1; \
		if kill -0 "$$(cat $(PID_FILE))" 2>/dev/null; then \
			echo "App server started (PID $$(cat $(PID_FILE))). Logs: $(LOG_FILE)"; \
		else \
			echo "App server failed to start -- check $(LOG_FILE)." >&2; \
			rm -f $(PID_FILE); \
			exit 1; \
		fi; \
	fi

stop:
	@if [ -f $(PID_FILE) ] && kill -0 "$$(cat $(PID_FILE))" 2>/dev/null; then \
		echo "Stopping app server (PID $$(cat $(PID_FILE)))..."; \
		kill "$$(cat $(PID_FILE))"; \
	else \
		echo "App server is not running."; \
	fi; \
	rm -f $(PID_FILE)
	@echo "Stopping database (data volume is preserved)..."
	@docker compose down

status:
	@if [ -f $(PID_FILE) ] && kill -0 "$$(cat $(PID_FILE))" 2>/dev/null; then \
		echo "App server: running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "App server: not running"; \
	fi
	@docker compose ps $(DB_SERVICE)
