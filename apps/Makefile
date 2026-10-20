# =============================================================================
# Build & release the 6 microservice images to Artifact Registry, then bump the GitOps tag.
#   make test                 → go vet + go build
#   make auth                 → docker login to Artifact Registry (once)
#   make push   [TAG=...]     → build linux/amd64 (GKE nodes) & push all images
#   make release [TAG=...]    → push + write TAG to k8s/services/values.yaml
#                               (commit & push → Argo CD syncs automatically)
# The project ID is read from ../.env (not committed). The registry is deliberately empty in Git —
# Argo CD gets it from Terraform (argocd-bootstrap); only the tag is committed.
# =============================================================================
-include ../.env

REGION   ?= asia-southeast2
ENV      ?= staging
REPO     ?= $(ENV)-shop
REGISTRY  = $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(REPO)
# Unique tag per commit (the AR repo uses immutable tags).
TAG      ?= $(shell git rev-parse --short=8 HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
# GKE nodes are amd64; Apple Silicon laptops are arm64 → cross-build required.
PLATFORM ?= linux/amd64
SERVICES := api-gateway user-service product-service order-service payment-service notification-service
VALUES   := k8s/services/values.yaml

.PHONY: test check-project auth push release

test:
	cd src && go vet ./... && go build ./...

check-project:
	@test -n "$(PROJECT_ID)" || { echo "ERROR: PROJECT_ID is empty — run: cp ../.env.example ../.env and set PROJECT_ID"; exit 1; }
	@test "$$(gcloud config get-value project 2>/dev/null)" = "$(PROJECT_ID)" || \
		{ echo "ERROR: active gcloud project is not $(PROJECT_ID) — run: gcloud config set project $(PROJECT_ID)"; exit 1; }

auth:
	gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet

push: check-project
	@for s in $(SERVICES); do \
		echo ">> $${s} → $(REGISTRY)/$${s}:$(TAG)"; \
		docker buildx build --platform $(PLATFORM) --build-arg SERVICE=$${s} \
			-t $(REGISTRY)/$${s}:$(TAG) --push src || exit 1; \
	done

release: push
	perl -pi -e 's/^(  tag: ).*$$/$${1}"$(TAG)"/' $(VALUES)
	@echo ""
	@echo "Tag $(TAG) written to $(VALUES). Continue with GitOps:"
	@echo "  git add $(VALUES) && git commit -m 'release shop $(TAG)' && git push"
