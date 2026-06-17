# Dependencies — modernized-application

> Service-, database-, and file-level dependencies derived from `requirements_graph.json` (`data_access` + `dependencies`). Infrastructure-level, not a code callgraph.

## Database

Primary datastore: **the configured database**.

## Data stores and files

_No data-access assets declared in the requirements graph._

## Service dependencies

Capabilities this application depends on (internal inter-service / inter-requirement edges):

- `REQ_CAP_9107DA333DDD`
- `REQ_CAP_C40036939A6D`
- `REQ_CAP_E76FB0C5201D`

## Source-system provenance

This application was modernized from:

| Source app | Language |
|---|---|
| `apache-kafka` | java |
| `apache-pulsar` | java |
