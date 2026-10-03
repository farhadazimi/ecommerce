#!/usr/bin/env bash
# Local Kubernetes rehearsal of the CCE deployment on kind:
#   kind cluster (+ ingress-nginx + metrics-server) -> build & load images -> deploy local overlay
# Then:  E2E_API_URL=http://shop.localtest.me E2E_FRONTEND_URL=http://shop.localtest.me make e2e
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
CLUSTER=${CLUSTER:-ecommerce}
TAG=${IMAGE_TAG:-dev}

if ! kind get clusters | grep -qx "$CLUSTER"; then
  cat <<EOF | kind create cluster --name "$CLUSTER" --image kindest/node:v1.33.1 --wait 120s --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true,topology.kubernetes.io/zone=zone-a"
    extraPortMappings:
      - {containerPort: 80, hostPort: 80, protocol: TCP}
      - {containerPort: 443, hostPort: 443, protocol: TCP}
EOF
fi
kubectl config use-context "kind-$CLUSTER"

echo "==> ingress-nginx (stands in for the ELB-backed CCE ingress)"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.12.1/deploy/static/provider/kind/deploy.yaml
# trust X-Forwarded-* from the (simulated) ELB in front of the controller
kubectl -n ingress-nginx patch configmap ingress-nginx-controller --type merge \
  -p '{"data":{"use-forwarded-headers":"true","compute-full-forwarded-for":"true","enable-real-ip":"true"}}'
kubectl -n ingress-nginx wait --for=condition=Available deploy/ingress-nginx-controller --timeout=300s

echo "==> metrics-server (HPA)"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.7.2/components.yaml
kubectl -n kube-system patch deploy metrics-server --type json \
  -p '[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' || true

echo "==> build + load images"
docker build -q -f "$ROOT/backend/Dockerfile" -t "local/ecommerce-backend:$TAG" "$ROOT"
docker build -q -f "$ROOT/order-service/Dockerfile" -t "local/ecommerce-order-service:$TAG" "$ROOT"
docker build -q -t "local/ecommerce-frontend:$TAG" "$ROOT/frontend"
for img in ecommerce-backend ecommerce-order-service ecommerce-frontend; do
  kind load docker-image --name "$CLUSTER" "local/$img:$TAG"
done
for img in mysql:8.0 redis:7.4-alpine chrislusf/seaweedfs:4.48; do
  docker image inspect "$img" >/dev/null 2>&1 && kind load docker-image --name "$CLUSTER" "$img" || true
done

SEED=true "$ROOT/scripts/k8s-deploy.sh" local
echo
echo "Storefront: http://shop.localtest.me   API docs: http://shop.localtest.me/api/docs"
