# =============================================================================
# Terraform workspace orchestration (local state per workspace, git-ignored).
#   make init                 init all workspaces
#   make validate             fmt check + validate all workspaces
#   make plan  WS=vpc         plan one workspace
#   make apply WS=vpc         apply one workspace (asks for "yes")
#   make apply-all            apply all workspaces in dependency order
#   make teardown             delete cluster workloads in order (PVC disks included), then destroy
# AUTO_APPROVE=1 skips the prompts (careful).
#
# The project ID and repo URL are never committed: they come from ../.env (see ../.env.example)
# and are passed to Terraform as TF_VAR_*.
# =============================================================================
-include ../.env

ENV ?= staging
WS  ?=
# Dependencies are resolved by name (data sources), not remote state.
APPLY_ORDER := services vpc cloud-nat firewall kubernetes-engine artifact-registry cloud-storage secret-manager argocd-bootstrap

# GitOps repo URL: from .env, or the "origin" remote (SSH converted to HTTPS).
ifeq ($(strip $(GITOPS_REPO_URL)),)
GITOPS_REPO_URL := $(shell git remote get-url origin 2>/dev/null | sed -E 's|^git@([^:]+):|https://\1/|')
endif

export TF_VAR_project_id      := $(PROJECT_ID)
export TF_VAR_gitops_repo_url := $(GITOPS_REPO_URL)

TF_APPROVE := $(if $(filter 1,$(AUTO_APPROVE)),-auto-approve,)
WS_DIR      = environments/$(ENV)/$(1)

.PHONY: check-project init validate plan apply apply-all teardown

# Guard: never touch the wrong project, and never commit a project ID in tfvars.
check-project:
	@test -n "$(PROJECT_ID)" || { echo "ERROR: PROJECT_ID is empty — run: cp ../.env.example ../.env and set PROJECT_ID"; exit 1; }
	@active="$$(gcloud config get-value project 2>/dev/null)"; \
	if [ "$$active" != "$(PROJECT_ID)" ]; then \
		echo "ERROR: active gcloud project '$$active' != PROJECT_ID '$(PROJECT_ID)' in .env. Run: gcloud config set project $(PROJECT_ID)"; exit 1; \
	fi
	@if grep -l -E '^[[:space:]]*project_id[[:space:]]*=' environments/$(ENV)/*/$(ENV).tfvars 2>/dev/null; then \
		echo "ERROR: project_id is set in the tfvars above — remove it (public repo); it comes from .env"; exit 1; \
	fi
	@echo "OK: project $(PROJECT_ID)"

init:
	@for ws in $(APPLY_ORDER); do echo "== init $$ws"; terraform -chdir=$(call WS_DIR,$$ws) init -input=false || exit 1; done

validate:
	terraform fmt -check -recursive
	@for ws in $(APPLY_ORDER); do echo "== validate $$ws"; terraform -chdir=$(call WS_DIR,$$ws) validate || exit 1; done

plan: check-project
	@test -n "$(WS)" || { echo "Usage: make plan WS=<workspace>"; exit 1; }
	terraform -chdir=$(call WS_DIR,$(WS)) plan -var-file=$(ENV).tfvars

apply: check-project
	@test -n "$(WS)" || { echo "Usage: make apply WS=<workspace>"; exit 1; }
	terraform -chdir=$(call WS_DIR,$(WS)) apply -var-file=$(ENV).tfvars $(TF_APPROVE)

apply-all: check-project
	@for ws in $(APPLY_ORDER); do \
		echo ""; echo "================ apply $$ws ================"; \
		terraform -chdir=$(call WS_DIR,$$ws) apply -var-file=$(ENV).tfvars $(TF_APPROVE) || exit 1; \
	done

teardown: check-project
	ENV=$(ENV) PROJECT_ID=$(PROJECT_ID) AUTO_APPROVE=$(AUTO_APPROVE) ./scripts/teardown.sh
