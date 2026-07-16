# Usage

To enable this extension, go to Windows > Extensions menu and enable LPB Config Loader.

The World Controls panel has a `Config JSON` path field. Press `LOAD` to build the scene from that JSON file.

Default config:

```text
config/default_scene.json
```

High-level JSON fields supported by this loader:

```json
{
  "warehouse": { "dimensions": [40, 30, 8] },
  "shelves": [
    {
      "area": { "center": [24, 14, 0], "size": [18, 12, 0] },
      "arrangement": "rows",
      "rows": 3,
      "columns": 4,
      "spacing": [1.8, 2.2],
      "shelf": { "dimensions": [8, 1, 6], "segments": 2, "rotation": 90 },
      "packages": { "mode": "random", "min_count": 1, "max_count": 5 }
    }
  ],
  "conveyors": [
    { "name": "Inbound", "start": [2, 4, 0], "end": [18, 4, 0], "segment_length": 2.0 }
  ],
  "vehicles": {
    "shuttles": [{ "name": "shuttle1", "position": [4, 10, 0] }],
    "transporters": [{ "name": "transporter1", "position": [4, 2, 0] }]
  },
  "palletizing_areas": [
    {
      "area": { "center": [10, 8, 0], "size": [6, 4, 0] },
      "robot_arm": { "name": "pallet_arm1", "relative_position": [-1, 0, 0] },
      "pallets": [{ "name": "Pallet_1", "relative_position": [1.2, 0, 0] }]
    }
  ]
}
```

Existing low-level scene files that already contain `objects` and `agents` can still be loaded directly.

Detailed Chinese configuration guide:

```text
docs/SCENE_CONFIG.md
```

HTTP API server guide:

```text
docs/API_SERVER.md
```

Web console and fixed demo script guide:

```text
docs/COMMAND_CONSOLE.md
```

Simple fixed-sequence API test client:

```text
client/api_sequence_client.py
```
