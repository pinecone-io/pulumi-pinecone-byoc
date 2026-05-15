SHELL := /bin/sh

TF ?= terraform
GO ?= go
PYTHON ?= python3

ROOT := $(CURDIR)
PROVIDER_DIR := $(ROOT)/provider/pineconebyoc
PROVIDER_BIN_DIR := $(PROVIDER_DIR)/bin
PROVIDER_BIN := $(PROVIDER_BIN_DIR)/terraform-provider-pineconebyoc
TF_CLI_CONFIG := $(ROOT)/dev.tfrc.hcl

.PHONY: provider-build cli-config fmt fmt-check validate-aws validate-gcp validate-azure validate-all wizard

provider-build:
	mkdir -p "$(PROVIDER_BIN_DIR)"
	cd "$(PROVIDER_DIR)" && $(GO) build -o "$(PROVIDER_BIN)"

cli-config:
	printf '%s\n' 'provider_installation {' > "$(TF_CLI_CONFIG)"
	printf '%s\n' '  dev_overrides {' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '    "pinecone.io/internal/pineconebyoc" = "$(PROVIDER_BIN_DIR)"' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '  }' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '  direct {}' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '}' >> "$(TF_CLI_CONFIG)"

fmt:
	$(TF) fmt -recursive .

fmt-check:
	$(TF) fmt -check -recursive .

validate-aws: provider-build cli-config
	cd examples/aws && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) init -backend=false
	cd examples/aws && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) validate

validate-gcp: provider-build cli-config
	cd examples/gcp && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) init -backend=false
	cd examples/gcp && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) validate

validate-azure: provider-build cli-config
	cd examples/azure && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) init -backend=false
	cd examples/azure && TF_CLI_CONFIG_FILE="$(TF_CLI_CONFIG)" $(TF) validate

validate-all: fmt-check validate-aws validate-gcp validate-azure

wizard:
	$(PYTHON) setup/wizard.py
