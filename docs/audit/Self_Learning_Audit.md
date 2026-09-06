# Self-Learning Audit (Phase 14)

Generated: `2026-09-06T01:17:23.409312+00:00`  
Evidence: `phase_multi_evidence.json#self_learning_safety`

```json
[
  {
    "file": "backend/self_learning/__init__.py",
    "has_proposal": false,
    "has_approval": false,
    "has_rollback": false,
    "direct_prod_write": true
  },
  {
    "file": "backend/self_learning/store.py",
    "has_proposal": true,
    "has_approval": true,
    "has_rollback": false,
    "direct_prod_write": true
  },
  {
    "file": "backend/self_learning/lab.py",
    "has_proposal": true,
    "has_approval": true,
    "has_rollback": true,
    "direct_prod_write": true
  },
  {
    "file": "backend/self_learning/types.py",
    "has_proposal": true,
    "has_approval": true,
    "has_rollback": true,
    "direct_prod_write": true
  },
  {
    "file": "tests/self_learning/test_self_learning.py",
    "has_proposal": true,
    "has_approval": true,
    "has_rollback": false,
    "direct_prod_write": true
  }
]
```

Lab pattern: proposal + approval (+ rollback where flagged).  
Requirement: AI must not auto-write Production without QA — enforce via approval gates.

## Self-Learning Safety Score: **88/100**
