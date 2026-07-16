
```json
{
  "task_id": "JOB_REORG_001",
  "goal": "仓储整理（闭环 5 步）",
  "tasks": [
    {
      "header": {
        "message_id": "msg_001",
        "timestamp": 1715621001,
        "sender_id": "Central_Controller",
        "receiver_id": "Shuttle_Manager",
        "message_type": "ATOMIC_CMD",
        "conversation_id": "JOB_REORG_001"
      },
      "body": {
        "agent_role": "SHUTTLE",
        "action_type": "RETRIEVE_BIN",
        "payload": {
          "task_id": "T_1",
          "target_bin": {
            "id": "BIN_COLD_001",
            "position": [2.0, 1.0, 0.0]
          },    
          "destination": {
            "id": "HANDOVER_A",
            "position": [0.0, 20.0, 1.0]
          }
        }
      }
    },
    {
      "header": {
        "message_id": "msg_002",
        "timestamp": 1715621002,
        "sender_id": "Central_Controller",
        "receiver_id": "AGV_Manager",
        "message_type": "ATOMIC_CMD",
        "conversation_id": "JOB_REORG_001"
      },
      "body": {
        "agent_role": "AGV",
        "action_type": "LIFT_TRANSPORT",
        "payload": {
          "task_id": "T_2",
          "dependencies": ["T_1"],
          "pickup": {
            "id": "HANDOVER_A",
            "position": [2.0, 0.0, 0.0],
            "action": "LIFT_UP"
          },
          "dropoff": {
            "id": "WORKSTATION_B",
            "position": [20.0, 10.0, 0.0],
            "action": "DROP_DOWN"
          }
        }
      }
    },
    {
      "header": {
        "message_id": "msg_003",
        "timestamp": 1715621003,
        "sender_id": "Central_Controller",
        "receiver_id": "Arm_Manager",
        "message_type": "ATOMIC_CMD",
        "conversation_id": "JOB_REORG_001"
      },
      "body": {
        "agent_role": "ROBOTIC_ARM",
        "action_type": "PALLETIZE",
        "payload": {
          "task_id": "T_3",
          "dependencies": ["T_2"],
          "source": {
            "id": "WORKSTATION_B",
            "position": [20.0, 10.0, 0.8]
          },
          "target_container": {
            "id": "BIN_COLD_001",
            "position": [20.0, 10.0, 0.8],
            "stacking_strategy": "COMPACT"
          }
        }
      }
    },
    {
      "header": {
        "message_id": "msg_004",
        "timestamp": 1715621004,
        "sender_id": "Central_Controller",
        "receiver_id": "AGV_Manager",
        "message_type": "ATOMIC_CMD",
        "conversation_id": "JOB_REORG_001"
      },
      "body": {
        "agent_role": "AGV",
        "action_type": "LIFT_TRANSPORT",
        "payload": {
          "task_id": "T_4",
          "dependencies": ["T_3"],
          "pickup": {
            "id": "WORKSTATION_B",
            "position": [20.0, 10.0, 0.0],
            "action": "LIFT_UP"
          },
          "dropoff": {
            "id": "HANDOVER_C",
            "position": [4.0, 0.0, 0.0],
            "action": "DROP_DOWN"
          }
        }
      }
    },
    {
      "header": {
        "message_id": "msg_005",
        "timestamp": 1715621005,
        "sender_id": "Central_Controller",
        "receiver_id": "Shuttle_Manager",
        "message_type": "ATOMIC_CMD",
        
        "conversation_id": "JOB_REORG_001"
      },
      "body": {
        "agent_role": "SHUTTLE",
        "action_type": "STORE_BIN",
        "payload": {
          "task_id": "T_5",
          "dependencies": ["T_4"],
          "source_port": {
            "id": "HANDOVER_C",
            "position": [4.0, 0.0, 0.5]
          },
          "target_rack": {
            "id": "RACK_HOT_05",
            "position": [4.0, 5.0, 2.0]
          }
        }
      }
    }
  ]
}
```
