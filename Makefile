.PHONY: check test lint migrate makemigrations run up down superuser install

VENV_PYTHON := .venv/bin/python

install:
	python3 -m venv .venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt

check:
	$(VENV_PYTHON) manage.py check

test:
	$(VENV_PYTHON) manage.py test

lint:
	.venv/bin/ruff check .

migrate:
	$(VENV_PYTHON) manage.py migrate

makemigrations:
	$(VENV_PYTHON) manage.py makemigrations

run:
	$(VENV_PYTHON) manage.py runserver

superuser:
	$(VENV_PYTHON) manage.py createsuperuser

up:
	docker compose up -d

down:
	docker compose down
