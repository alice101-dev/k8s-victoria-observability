const fs = require("fs");

const serviceAccount = "/var/run/secrets/kubernetes.io/serviceaccount";
const kubeToken = fs.readFileSync(`${serviceAccount}/token`, "utf8").trim();
const kubeApi = `https://${process.env.KUBERNETES_SERVICE_HOST}:${process.env.KUBERNETES_SERVICE_PORT}`;
const { ARGOCD_BASE_URL, ARGOCD_ACCOUNT, TARGET_NAMESPACE, TARGET_SECRET, TARGET_DEPLOYMENT } = process.env;

async function kube(method, path, body, contentType = "application/json") {
  const res = await fetch(kubeApi + path, {
    method,
    headers: { Authorization: `Bearer ${kubeToken}`, "Content-Type": contentType },
    body: body && JSON.stringify(body),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

async function argocd(method, path, token, body) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(ARGOCD_BASE_URL + path, { method, headers, body: body && JSON.stringify(body) });
  if (!res.ok) throw new Error(`${method} ${path}: ${res.status} ${await res.text()}`);
  return res.json();
}

async function isValid(token) {
  const res = await fetch(`${ARGOCD_BASE_URL}/api/v1/session/userinfo`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) return false;
  const info = await res.json();
  return info.loggedIn === true && info.username === ARGOCD_ACCOUNT;
}

const decode = (value) => Buffer.from(value, "base64").toString("utf8");

async function main() {
  const secretPath = `/api/v1/namespaces/${TARGET_NAMESPACE}/secrets/${TARGET_SECRET}`;
  const current = await kube("GET", secretPath);
  if (current?.data?.token && (await isValid(decode(current.data.token)))) {
    console.log(JSON.stringify({ level: "info", msg: "token is valid", account: ARGOCD_ACCOUNT }));
    return;
  }

  const admin = await kube("GET", "/api/v1/namespaces/argocd/secrets/argocd-initial-admin-secret");
  if (!admin) throw new Error("argocd-initial-admin-secret not found");
  const { token: session } = await argocd("POST", "/api/v1/session", null, {
    username: "admin",
    password: decode(admin.data.password),
  });
  const { token } = await argocd("POST", `/api/v1/account/${ARGOCD_ACCOUNT}/token`, session, {
    name: ARGOCD_ACCOUNT,
    id: `mcp-argocd-${Date.now()}`,
  });

  const secret = {
    apiVersion: "v1",
    kind: "Secret",
    metadata: { name: TARGET_SECRET, namespace: TARGET_NAMESPACE, labels: { "app.kubernetes.io/name": "mcp-argocd" } },
    type: "Opaque",
    data: { token: Buffer.from(token).toString("base64") },
  };
  if (current) {
    secret.metadata.resourceVersion = current.metadata.resourceVersion;
    await kube("PUT", secretPath, secret);
  } else {
    await kube("POST", `/api/v1/namespaces/${TARGET_NAMESPACE}/secrets`, secret);
  }

  const deploymentPath = `/apis/apps/v1/namespaces/${TARGET_NAMESPACE}/deployments/${TARGET_DEPLOYMENT}`;
  if (await kube("GET", deploymentPath)) {
    await kube(
      "PATCH",
      deploymentPath,
      { spec: { template: { metadata: { annotations: { "kubectl.kubernetes.io/restartedAt": new Date().toISOString() } } } } },
      "application/strategic-merge-patch+json",
    );
  }
  console.log(JSON.stringify({ level: "info", msg: current ? "token replaced" : "token created", account: ARGOCD_ACCOUNT }));
}

main().catch((err) => {
  console.error(JSON.stringify({ level: "error", msg: err.message }));
  process.exit(1);
});
