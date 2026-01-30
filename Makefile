OCI_BIN = $(shell which docker || which podman)

# Image configuration - using local tags by default
# Override with: make build IMG_REGISTRY=quay.io IMG_NAMESPACE=it-bench
IMG_REGISTRY ?= localhost
IMG_NAMESPACE ?= itbench
VERSION ?= latest

# Constructed image tags
AGENT_IMG = $(IMG_REGISTRY)/$(IMG_NAMESPACE)/agent:$(VERSION)
EVALUATOR_IMG = $(IMG_REGISTRY)/$(IMG_NAMESPACE)/evaluator:$(VERSION)

# Architecture configuration
PLATFORMS ?= linux/arm64,linux/amd64

.PHONY: help
help: ## Display this help.
	@echo "\033[1mCurrent Configuration:\033[0m"
	@echo "  Registry:      $(IMG_REGISTRY)"
	@echo "  Namespace:     $(IMG_NAMESPACE)"
	@echo "  Version:       $(VERSION)"
	@echo "  Platforms:     $(PLATFORMS)"
	@echo "  Agent Image:   $(AGENT_IMG)"
	@echo "  Eval Image:    $(EVALUATOR_IMG)"
	@echo ""
	@echo "\033[1mExamples:\033[0m"
	@echo "  # Build for local use"
	@echo "  make build"
	@echo ""
	@echo "  # Build for remote registry"
	@echo "  make build IMG_REGISTRY=quay.io IMG_NAMESPACE=it-bench VERSION=v1.0"
	@echo ""
	@echo "  # Build for single architecture"
	@echo "  make build PLATFORMS=linux/amd64"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\033[1mAvailable Targets:\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Build

.PHONY: build-agent
build-agent: ## Build agent container image for multiple architectures
	$(OCI_BIN) manifest rm $(AGENT_IMG) || true
	$(OCI_BIN) manifest create $(AGENT_IMG)
	$(OCI_BIN) build -f itbench/Dockerfile.agent --platform=$(PLATFORMS) --manifest $(AGENT_IMG) .

.PHONY: build-evaluator
build-evaluator: ## Build evaluator container image for multiple architectures
	$(OCI_BIN) manifest rm $(EVALUATOR_IMG) || true
	$(OCI_BIN) manifest create $(EVALUATOR_IMG)
	$(OCI_BIN) build -f itbench/Dockerfile.evaluator --platform=$(PLATFORMS) --manifest $(EVALUATOR_IMG) .

.PHONY: build
build: build-agent build-evaluator ## Build both agent and evaluator images

##@ Push

.PHONY: push-agent
push-agent: ## Push agent image to container registry
	$(OCI_BIN) manifest push $(AGENT_IMG)

.PHONY: push-evaluator
push-evaluator: ## Push evaluator image to container registry
	$(OCI_BIN) manifest push $(EVALUATOR_IMG)

.PHONY: push
push: push-agent push-evaluator ## Push both images to container registry

##@ Build and Push

.PHONY: build-push-agent
build-push-agent: build-agent push-agent ## Build and push agent image

.PHONY: build-push-evaluator
build-push-evaluator: build-evaluator push-evaluator ## Build and push evaluator image

.PHONY: build-push
build-push: build push ## Build and push both images

##@ Clean

.PHONY: clean-manifests
clean-manifests: ## Remove local image manifests
	$(OCI_BIN) manifest rm $(AGENT_IMG) || true
	$(OCI_BIN) manifest rm $(EVALUATOR_IMG) || true

##@ Run

.PHONY: run-evaluator
run-evaluator: ## Run evaluator container with Scenarios mounted
	$(OCI_BIN) run -d --name itbench-evaluator \
		-p 9009:9009 \
		--env-file .env \
		-v $(PWD)/Scenarios:/home/agentbeats/itbench_eval/Scenarios:ro \
		$(EVALUATOR_IMG)

.PHONY: run-agent
run-agent: ## Run agent container
	$(OCI_BIN) run -d --name itbench-agent \
		-p 9019:9019 \
		--env-file .env \
		$(AGENT_IMG)

.PHONY: run
run: run-evaluator run-agent ## Run both evaluator and agent containers

.PHONY: stop-evaluator
stop-evaluator: ## Stop evaluator container
	$(OCI_BIN) stop itbench-evaluator || true
	$(OCI_BIN) rm itbench-evaluator || true

.PHONY: stop-agent
stop-agent: ## Stop agent container
	$(OCI_BIN) stop itbench-agent || true
	$(OCI_BIN) rm itbench-agent || true

.PHONY: stop
stop: stop-evaluator stop-agent ## Stop both containers

.PHONY: logs-evaluator
logs-evaluator: ## Show evaluator logs
	$(OCI_BIN) logs -f itbench-evaluator

.PHONY: logs-agent
logs-agent: ## Show agent logs
	$(OCI_BIN) logs -f itbench-agent

##@ Utilities

.PHONY: config
config: ## Show current configuration
	@echo "OCI Binary:    $(OCI_BIN)"
	@echo "Registry:      $(IMG_REGISTRY)"
	@echo "Namespace:     $(IMG_NAMESPACE)"
	@echo "Version:       $(VERSION)"
	@echo "Platforms:     $(PLATFORMS)"
	@echo ""
	@echo "Agent Image:   $(AGENT_IMG)"
	@echo "Eval Image:    $(EVALUATOR_IMG)"
