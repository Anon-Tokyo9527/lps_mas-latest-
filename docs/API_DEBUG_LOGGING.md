# API Debug Logging

This document describes the server-side diagnostics used for headless debugging.

## Event Logs

Use:

```http
GET /logs?limit=200
GET /logs?since=120
GET /logs?level=ERROR
POST /logs/clear
```

The response includes recent in-memory entries and `file_path`.

Each entry contains:

- `seq`: monotonic log sequence number.
- `time`: Unix timestamp.
- `iso_time`: local readable timestamp.
- `level`: `DEBUG`, `INFO`, `WARN`, or `ERROR`.
- `message`: short event name.
- `data`: JSON-safe event details.

Logs are also written as JSONL files under:

```text
D:\workstation\isaac-sim-standalone-5.1.0-windows-x86_64\extsUser\LPB_config_server\logs
```

## Debug Snapshot

Use:

```http
GET /debug/snapshot
GET /debug/snapshot?world=0
GET /debug/snapshot?tasks=0
```

The snapshot contains:

- Runtime and API server status.
- Active task, queued tasks, task status counts, and optional task details.
- Command script status.
- Scene index: shelves, shelf packages, conveyors, pallets, slots, pallet transport targets.
- Robot positions for shuttles, transporters, and manipulators.
- Candidate pool records.
- Held packages and shuttle roof queues.
- Transporter pallet load records.
- Conveyor package flow records.
- Optional USD world object positions.
- Current log file path and next log sequence.

Use `world=0` when you only need high-level simulator state and want a smaller response.

## Important Event Names

- `HTTP request handled`: method, path, status code, request body, response summary.
- `Task queued`: task metadata and queue summary.
- `Task started`: active task and queue summary.
- `Task step executing`: current step args and compact simulator snapshot.
- `Task step still running`: long-running step heartbeat.
- `Task step done`: compact snapshot after a step completes.
- `Task done`: final compact snapshot for a task.
- `Task failed`: failing step and error string.
- `Candidate added`, `Candidate updated`, `Candidate status changed`.
- `Shuttle package shown on roof queue`.
- `Shuttle placed package on conveyor start`.
- `Conveyor package moved to candidate pool`.
- `Package virtually picked`.
- `Package virtually placed by manipulator`.
- `Pallet attached above transporter`.
- `Pallet stack moved`.
- `Pallet released from transporter`.

## Suggested Headless Debug Loop

1. Clear logs:

```powershell
Invoke-RestMethod -Method Post http://localhost:60124/logs/clear
```

2. Run the multi-agent client or one API command.

3. Inspect task state:

```powershell
Invoke-RestMethod http://localhost:60124/state
Invoke-RestMethod http://localhost:60124/debug/snapshot?world=0
```

4. Pull recent logs:

```powershell
Invoke-RestMethod "http://localhost:60124/logs?limit=500"
```

5. If the in-memory log is too short, open the returned `file_path` JSONL file.
