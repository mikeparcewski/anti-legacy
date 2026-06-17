# Environments — modernized-application

> Deployment targets and per-environment configuration derived from `config.json`.

## Deployment target

Primary deployment platform: **the configured platform**.

Backing datastore: **the configured database**.

## Environment ladder

| Environment | Deployment target | Datastore | Notes |
|---|---|---|---|
| `local` | developer workstation | local the configured database | Run the app directly; see README.md. |
| `staging` | the configured platform | managed the configured database | Mirror of production; validate before promotion. |
| `production` | the configured platform | managed the configured database | Promote only after GATE_4_UAT sign-off. |

## Per-environment configuration

Each environment supplies its own values for the following configuration keys (do not hard-code them):

- **Database connection URL / host**
- **Database credentials**
- **Application listen port**
- **Log level**

## Setup per environment

1. Provision a **the configured database** instance and load the schema.
2. Set the configuration keys above for the environment.
3. Deploy the application artifact to **the configured platform**.
4. Run a smoke check confirming datastore connectivity.
