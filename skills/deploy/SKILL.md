---
name: "anti-legacy:deploy"
description: >
  Package and deploy the modernized application to the configured deployment target.
  Supports GCP Cloud Run, AWS ECS/Fargate, Azure AKS, and generic Kubernetes.
  Generates Dockerfile, CI/CD pipeline config, and deployment manifests. Requires
  GATE_4_UAT to be cleared.
  Use when: "deploy the app", "generate the Dockerfile", "create CI/CD config",
  "package for Cloud Run", "generate Kubernetes manifests", "deployment".
---

# anti-legacy:deploy

The final pipeline phase. Takes the verified target codebase and produces
deployment-ready artifacts: Dockerfile, CI/CD pipeline config, and target
platform manifests. Designed to be committed alongside the code so the team's
git repo is the complete, reproducible delivery artifact.

## Cross-Platform Notes

Generates text-based deployment configs — no platform CLI required for
artifact generation. Platform CLI commands (gcloud, aws, kubectl) are
provided as reference commands for the team to run.

## Config

```bash
python3 -c "import json; c=json.load(open('.anti-legacy/config.json')); print(c['target_stack'], c['deployment_target'], c['target_path'])"
```

## Parameters

- **deployment_target** (optional): override from config. One of:
  `gcp-cloud-run`, `aws-ecs`, `azure-aks`, `kubernetes`, `docker-compose`
- **registry** (optional): container registry URL (e.g. `gcr.io/my-project`, `123456789.dkr.ecr.us-east-1.amazonaws.com`)
- **image_name** (optional): container image name. Defaults to `{project_name}`.

## Step 1: Verify GATE_4_UAT is cleared

```bash
python3 -c "
import json, sys
m = json.load(open('.anti-legacy/manifest.json'))
g = m['gates']['GATE_4_UAT']
if g['status'] != 'passed':
    print(f'BLOCKED: GATE_4_UAT is {g[\"status\"]}. Complete UAT and gate sign-off first.')
    sys.exit(1)
print('GATE_4_UAT: cleared ✓')
"
```

## Step 2: Query git-brain for deployment patterns

```bash
python3 .anti-legacy/run.py git_brain search \
  --query "deployment {target_stack} {deployment_target} Dockerfile CI/CD pipeline" \
  --limit 5
```

## Step 3: Generate Dockerfile

Write `{target_path}/Dockerfile` appropriate for the target stack:

**Java (Maven)**:
```dockerfile
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline
COPY src ./src
RUN mvn package -DskipTests

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /app/target/{project_name}-*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
```

**Go**:
```dockerfile
FROM golang:1.22-alpine AS build
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -o {project_name} .

FROM alpine:3.19
WORKDIR /app
COPY --from=build /app/{project_name} .
EXPOSE 8080
ENTRYPOINT ["./{project_name}"]
```

**C# (.NET)**:
```dockerfile
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /app
COPY *.csproj .
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o out

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /app/out .
EXPOSE 8080
ENTRYPOINT ["dotnet", "{ProjectName}.dll"]
```

**Python**:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

## Step 4: Generate deployment manifests

### GCP Cloud Run

Write `{target_path}/deploy/cloudrun.yaml`:
```yaml
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: {project_name}
  annotations:
    run.googleapis.com/ingress: all
spec:
  template:
    metadata:
      annotations:
        autoscaling.knative.dev/maxScale: "10"
        run.googleapis.com/cpu-throttling: "false"
    spec:
      containerConcurrency: 80
      timeoutSeconds: 300
      containers:
        - image: {registry}/{image_name}:latest
          ports:
            - containerPort: 8080
          resources:
            limits:
              memory: 512Mi
              cpu: "1"
          env:
            - name: PROJECT_NAME
              value: "{project_name}"
```

### AWS ECS (Fargate)

Write `{target_path}/deploy/task-definition.json` with Fargate task definition.
Write `{target_path}/deploy/service.json` with ECS service definition.

### Kubernetes / Azure AKS

Write `{target_path}/deploy/k8s/deployment.yaml`, `service.yaml`, and optionally
`ingress.yaml` with appropriate selectors and resource limits.

### Docker Compose (local / fileshare teams)

Write `{target_path}/docker-compose.yml` for local development and testing.

## Step 5: Generate CI/CD pipeline config

Write a pipeline config appropriate for the team's setup.

**GitHub Actions** — write `{target_path}/.github/workflows/ci.yml`:
```yaml
name: CI/CD

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build
        run: {stack_build_command}
      - name: Test
        run: {stack_test_command}

  deploy:
    needs: build-and-test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build image
        run: docker build -t {registry}/{image_name}:${{ github.sha }} .
      - name: Push image
        run: docker push {registry}/{image_name}:${{ github.sha }}
      - name: Deploy to {deployment_target}
        run: {platform_deploy_command}
```

**GitLab CI** — write `{target_path}/.gitlab-ci.yml` with equivalent stages.

## Step 6: Write deployment runbook

Write `{target_path}/DEPLOY.md`:
- Prerequisites (toolchain, credentials required)
- How to build the image locally
- How to run locally with docker-compose
- How to deploy to {deployment_target}
- How to roll back (previous image tag)
- Health check endpoint
- Environment variables required

## Step 7: Store deploy patterns in git-brain

```bash
python3 .anti-legacy/run.py git_brain store \
  --content "Deployment [{project_name}]: {target_stack} → {deployment_target}. Image: {registry}/{image_name}. Dockerfile at {target_path}/Dockerfile. Pipeline: {ci_platform}." \
  --tags "pattern,deploy,{target_stack},{deployment_target}" \
  --category patterns
```

## Step 8: Done-gate, then register + advance phase to complete

**Done-gate (BLOCKING).** Before registering the deploy artifact or advancing the
phase, assert that the chosen deploy artifact is a real FILE that deploy actually
wrote. Pick `{deploy_artifact}` = the manifest file produced for
`{deployment_target}` — for example `Dockerfile` (always written by Step 3), or
the platform manifest written in Step 4 (`deploy/cloudrun.yaml`,
`deploy/task-definition.json`, `deploy/k8s/deployment.yaml`, or
`docker-compose.yml`). It MUST be an existing file, never a directory.

```bash
python3 -c "
import os, sys
target_path = '{target_path}'
deploy_artifact = '{deploy_artifact}'   # e.g. Dockerfile or deploy/cloudrun.yaml
p = os.path.join(target_path, deploy_artifact)
if not os.path.isfile(p):
    print(f'BLOCKED: deploy artifact {p} is missing or not a file. Generate it before completing.')
    sys.exit(1)
print(f'Deploy artifact present: {p} ✓')
"
```

If this assertion FAILS, do NOT run `register --status final` and do NOT run
`advance`; surface the missing artifact to the user and stop (the user may fix and
retry). The `register --status final` and `advance complete` below are CONDITIONAL
on the assertion passing.

Only on success, register the deploy artifact (a FILE, not a directory) and advance
the phase to `complete`:

```bash
python3 .anti-legacy/run.py manifest register deployment-artifacts \
  --path {deploy_artifact} \
  --format text \
  --produced-by anti-legacy:deploy \
  --status final \
  --depends-on build-integrity

python3 .anti-legacy/run.py manifest advance complete
```

## Step 9: Final pipeline summary

Print the complete pipeline summary:

```
Pipeline Complete — {project_name}
===================================
Source: {source_apps_summary}
Target: {target_stack} → {deployment_target}

Phases completed:
  ✓ Survey     → graphs/<app>.db + legacy-graph.digest.txt
  ✓ Analyze    → analysis-report.md
  ✓ Extraction → coverage-report.json + annotations.jsonl + requirements_graph.json
  ✓ Blueprint  → blueprint.json / blueprint.md
  ✓ Test       → {contract_count} test contracts
  ✓ Review     → review_packet.md (GATE_1 cleared)
  ✓ Plan       → task.md (GATE_2 cleared)
  ✓ Build      → {task_count} tasks (GATE_3 auto-cleared)
  ✓ UAT        → {req_count} requirements reviewed (GATE_4 cleared)
  ✓ Deploy     → Dockerfile + {deployment_target} manifests

Artifacts: .anti-legacy/manifest.json
Git-brain: {brain_path}

To deploy:
  cd {target_path}
  docker build -t {image_name} .
  {platform_deploy_command}
```

## Output

- `{target_path}/Dockerfile` — container image definition
- `{target_path}/deploy/` — platform manifests
- `{target_path}/{ci_config}` — CI/CD pipeline
- `{target_path}/DEPLOY.md` — deployment runbook
- Git-brain: deployment pattern stored for reuse
- Manifest: phase = `complete`, pipeline = **complete**
