SHELL := /bin/sh

TF ?= terraform
GO ?= go
PYTHON ?= python3

ROOT := $(CURDIR)
PROVIDER_DIR := $(ROOT)/provider/pineconebyoc
PROVIDER_SOURCE := pinecone.io/internal/pineconebyoc
PROVIDER_VERSION := 0.1.0
PROVIDER_MIRROR := $(ROOT)/provider-mirror
TF_CLI_CONFIG := $(ROOT)/dev.tfrc.hcl

.PHONY: provider-build cli-config fmt fmt-check validate-aws validate-gcp validate-azure validate-all wizard

provider-build:
	os=$$(uname -s | tr '[:upper:]' '[:lower:]'); \
	arch=$$(uname -m); \
	case "$$os" in darwin|linux) ;; *) echo "unsupported OS: $$os" >&2; exit 1 ;; esac; \
	case "$$arch" in x86_64|amd64) arch=amd64 ;; arm64|aarch64) arch=arm64 ;; *) echo "unsupported architecture: $$arch" >&2; exit 1 ;; esac; \
	platform="$${os}_$${arch}"; \
	bin_dir="$(PROVIDER_MIRROR)/$(PROVIDER_SOURCE)/$(PROVIDER_VERSION)/$${platform}"; \
	mkdir -p "$$bin_dir"; \
	cd "$(PROVIDER_DIR)" && $(GO) build -o "$$bin_dir/terraform-provider-pineconebyoc_v$(PROVIDER_VERSION)"

cli-config:
	printf '%s\n' 'provider_installation {' > "$(TF_CLI_CONFIG)"
	printf '%s\n' '  filesystem_mirror {' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '    path = "$(PROVIDER_MIRROR)"' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '    include = ["$(PROVIDER_SOURCE)"]' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '  }' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '  direct {' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '    exclude = ["$(PROVIDER_SOURCE)"]' >> "$(TF_CLI_CONFIG)"
	printf '%s\n' '  }' >> "$(TF_CLI_CONFIG)"
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
