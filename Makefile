.PHONY: setup run ingest info doctor lint format test clean help

# Research Swarm — Makefile

VENV     = .venv

ifeq ($(OS),Windows_NT)
PYTHON_BOOTSTRAP ?= py -3
PYTHON   = $(VENV)/Scripts/python.exe
PIP      = $(VENV)/Scripts/pip.exe
ACTIVATE = $(VENV)/Scripts/activate
BLANK_LINE_CMD = echo.
ENV_COPY_CMD = if not exist .env (copy .env.example .env & echo .env created from .env.example) else (echo .env already exists)
REMOVE_VENV_CMD = if exist $(VENV) rmdir /s /q $(VENV)
REMOVE_AUTOMATION_CACHE_CMD = if exist automation\__pycache__ rmdir /s /q automation\__pycache__
REMOVE_ROOT_CACHE_CMD = if exist __pycache__ rmdir /s /q __pycache__
REMOVE_DB_CMD = if exist automation\db rmdir /s /q automation\db
else
PYTHON_BOOTSTRAP ?= python3
PYTHON   = $(VENV)/bin/python
PIP      = $(VENV)/bin/pip
ACTIVATE = $(VENV)/bin/activate
BLANK_LINE_CMD = printf '\n'
ENV_COPY_CMD = if [ ! -f .env ]; then cp .env.example .env; echo .env created from .env.example; else echo .env already exists; fi
REMOVE_VENV_CMD = rm -rf $(VENV)
REMOVE_AUTOMATION_CACHE_CMD = rm -rf automation/__pycache__
REMOVE_ROOT_CACHE_CMD = rm -rf __pycache__
REMOVE_DB_CMD = rm -rf automation/db
endif

help:
	@$(BLANK_LINE_CMD)
	@echo   Research Swarm — Available Commands
	@echo   ====================================
	@echo   make setup       Create venv, install deps, copy .env.example
	@echo   make run         Run the swarm (set PROMPT="your prompt")
	@echo   make ingest      Vectorize documents from active agents/*/KB/ folders
	@echo   make info        Display the current swarm configuration
	@echo   make doctor      Validate config, files, KB, and tool wiring
	@echo   make lint        Run ruff linter on automation/
	@echo   make format      Run ruff formatter on automation/
	@echo   make test        Run the test suite
	@echo   make clean       Remove venv and cached files
	@$(BLANK_LINE_CMD)

setup:
	@echo Creating virtual environment...
	$(PYTHON_BOOTSTRAP) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@$(ENV_COPY_CMD)
	@$(BLANK_LINE_CMD)
	@echo Setup complete.
	@echo    1. Edit .env with your API keys  (see .env.example for instructions)
	@echo    2. Run: make doctor  (validate configuration)
	@echo    3. Run: make ingest  (build local knowledge base)
	@echo    4. Run: make run PROMPT="Your research question"

run:
ifndef PROMPT
	@echo ERROR: Please provide a prompt.
	@echo Usage: make run PROMPT="Your research question here"
	@exit /b 1
endif
	$(PYTHON) -m automation.main execute "$(PROMPT)"

ingest:
	@echo Vectorizing Knowledge Base documents...
	$(PYTHON) -m automation.ingest

info:
	$(PYTHON) -m automation.main info

doctor:
	$(PYTHON) -m automation.main doctor

lint:
	$(PYTHON) -m ruff check automation/

format:
	$(PYTHON) -m ruff format automation/

test:
	$(PYTHON) -m pytest tests/ -v

clean:
	@echo Cleaning up...
	@$(REMOVE_VENV_CMD)
	@$(REMOVE_AUTOMATION_CACHE_CMD)
	@$(REMOVE_ROOT_CACHE_CMD)
	@$(REMOVE_DB_CMD)
	@echo Cleaned.
