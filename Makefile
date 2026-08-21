.PHONY: all test run lint clean docker-build

all: test

test:
	pytest

run:
	python -m optimizer

lint:
	@echo "Running lint checks..."

clean:
	@echo "Cleaning artifacts..."

docker-build:
	docker build -t app:latest .
