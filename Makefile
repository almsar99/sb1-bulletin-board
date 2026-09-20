.PHONY: install lint format test cov run migrate migrations superuser up down logs seed seed-flush \
	purge-signup-requests cleanup-media

install:
	pip install -r requirements/dev.txt

lint:
	flake8 config users ads web messaging tests
	black --check config users ads web messaging tests
	isort --check-only config users ads web messaging tests

format:
	black config users ads web messaging tests
	isort config users ads web messaging tests

test:
	pytest

cov:
	pytest --cov --cov-report=term-missing

run:
	python manage.py runserver 127.0.0.1:8000

migrate:
	python manage.py migrate

migrations:
	python manage.py makemigrations

superuser:
	python manage.py createsuperuser

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f web

seed:
	python manage.py seed_demo $(SEED_ARGS)

seed-flush:
	python manage.py seed_demo --flush

purge-signup-requests:
	python manage.py purge_signup_requests

cleanup-media:
	python manage.py cleanup_media
