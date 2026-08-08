# GreenScheduler: Environmentally-Aware AI Infrastructure Scheduler

GreenScheduler jointly optimizes AI workload scheduling across five dimensions:

| Dimension | Source | Signal |
|---|---|---|
| Carbon Intensity | Electricity Maps / WattTime | gCO₂eq/kWh |
| Renewable Availability | Grid forecast APIs | % renewable mix |
| Water Stress | WRI Aqueduct / local sensors | Water stress index |
| Workload Deadlines | Job metadata | SLA windows |
| Community Priority | Configurable weights | Equity scoring |

## Quick Start

```bash
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
# Edit config/settings.yaml with your API keys and region settings
python -m scheduler.main
```

## API Usage

```bash
# Submit a workload
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "training-run-001",
    "gpu_hours": 10,
    "deadline": "2026-08-09T18:00:00Z",
    "priority": "standard",
    "regions": ["us-east-1", "eu-west-1", "us-west-2"]
  }'

# Get schedule recommendation
curl http://localhost:8000/api/v1/schedule/training-run-001
```

## How It Works

### Unified Scoring Objective

For each candidate (region, time_window) pair, GreenScheduler computes:

```
Score(r, t) = w_c · NormalizedCarbon(r,t)
            + w_w · NormalizedWater(r,t)
            - w_r · RenewableFraction(r,t)
            + w_d · DeadlinePressure(t, deadline)
            - w_p · CommunityPriority(r)
```

Lower score = better. The scheduler picks the lowest-scoring window that satisfies all hard constraints.

### Hard Constraints
- Job must complete before deadline
- Carbon intensity must not exceed regional hard cap (configurable)
- Water stress must not exceed threshold during drought alerts

### Soft Constraints (weighted)
- Prefer windows with >60% renewable mix
- Prefer regions with lower community water burden
- Defer non-urgent jobs during peak grid stress

## Project Structure

```
.
├── scheduler/          # Core scheduling engine
│   ├── main.py         # Entry point
│   ├── engine.py       # Multi-objective optimizer
│   ├── scorer.py       # Unified scoring function
│   └── constraints.py  # Hard and soft constraint checks
├── data/               # Environmental data connectors
│   ├── carbon.py       # Carbon intensity fetcher
│   ├── water.py        # Water stress fetcher
│   ├── renewable.py    # Renewable forecast fetcher
│   └── cache.py        # Caching layer
├── workloads/          # Workload management
│   ├── job.py          # Job model
│   ├── queue.py        # Job queue
│   └── deadline.py     # Deadline pressure calculator
├── api/                # REST API
│   ├── app.py          # FastAPI application
│   └── routes.py       # API endpoints
├── dashboard/          # Monitoring dashboard
│   └── app.py          # Streamlit dashboard
├── config/             # Configuration
│   ├── settings.example.yaml
│   └── loader.py
└── tests/              # Test suite
    ├── test_scorer.py
    ├── test_engine.py
    └── test_api.py
```
