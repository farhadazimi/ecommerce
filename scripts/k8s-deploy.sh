#!/usr/bin/env bash
# Deploy an overlay in safe order (used by `make k8s-deploy`, kind and GitLab CI):
#   1. config, services, ingress, policies (+ local stand-ins on kind)
#   2. schema migration Job(s) -> wait for completion
#   3. optional demo seed Job (SEED=true)
#   4. application Deployments -> wait for rollouts
#
# usage: scripts/k8s-deploy.sh <local|staging|production> [namespace]
set -euo pipefail

OVERLAY=${1:?usage: $0 <local|staging|production> [namespace]}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIR="$ROOT/infrastructure/kubernetes/overlays/$OVERLAY"
TIMEOUT=${TIMEOUT:-300s}
SEED=${SEED:-false}
RENDERED=$(mktemp)
trap 'rm -f "$RENDERED"' EXIT

kubectl kustomize "$DIR" > "$RENDERED"
NS=${2:-$(awk '/^kind: Namespace/{f=1} f&&/^  name:/{print $2; exit}' "$RENDERED")}
echo "==> overlay=$OVERLAY namespace=$NS"

APP='app.kubernetes.io/component in (api,orders,web)'
JOBS='app.kubernetes.io/component in (migration,seed)'

echo "==> [1/4] namespace, config, services, ingress, policies"
kubectl apply -f "$RENDERED" -l 'app.kubernetes.io/component notin (api,orders,web,migration,seed)'
if ! kubectl -n "$NS" get secret ecommerce-secrets >/dev/null 2>&1; then
  echo "ERROR: secret '$NS/ecommerce-secrets' does not exist - create it first (see infrastructure/kubernetes/base/secrets.yaml)" >&2
  exit 1
fi
if kubectl -n "$NS" get deploy -l app.kubernetes.io/component=local-standin -o name | grep -q .; then
  echo "    waiting for local stand-ins (mysql/redis/object-storage)"
  kubectl -n "$NS" wait --for=condition=Available deploy -l app.kubernetes.io/component=local-standin --timeout="$TIMEOUT"
fi

echo "==> [2/4] migrations"
kubectl -n "$NS" delete job -l app.kubernetes.io/component=migration --ignore-not-found --wait=true
kubectl apply -f "$RENDERED" -l app.kubernetes.io/component=migration
for job in $(kubectl -n "$NS" get job -l app.kubernetes.io/component=migration -o name); do
  if ! kubectl -n "$NS" wait --for=condition=complete "$job" --timeout="$TIMEOUT"; then
    kubectl -n "$NS" logs "$job" --tail=50 || true
    echo "ERROR: $job failed" >&2
    exit 1
  fi
done

if [[ "$SEED" == "true" ]] && grep -q "app.kubernetes.io/component: seed" "$RENDERED"; then
  echo "==> [3/4] demo seed"
  kubectl -n "$NS" delete job -l app.kubernetes.io/component=seed --ignore-not-found --wait=true
  kubectl apply -f "$RENDERED" -l app.kubernetes.io/component=seed
  kubectl -n "$NS" wait --for=condition=complete job -l app.kubernetes.io/component=seed --timeout="$TIMEOUT"
else
  echo "==> [3/4] demo seed skipped"
fi

echo "==> [4/4] application rollout"
kubectl apply -f "$RENDERED" -l "$APP"
for d in order-service backend frontend; do
  kubectl -n "$NS" rollout status "deploy/$d" --timeout="$TIMEOUT"
done
kubectl -n "$NS" get deploy,svc,ingress,hpa,pdb
echo "==> deployed $OVERLAY"
