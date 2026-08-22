# RevPilot Setup & Developer Guide

## Prerequisites

- **Python**: 3.12 or 3.13+
- **Git**: 2.30+
- **Operating System**: macOS, Linux, or Windows (WSL recommended for Windows)

---

## 🚀 Quick Start (Under 2 Minutes)

### 1. Clone the Repository
```bash
git clone https://github.com/Rishabh-377/RevPilot.git
cd revpilot
```

### 2. Create and Activate Virtual Environment
```bash
# On macOS / Linux:
python3 -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell):
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment (Optional)
```bash
cp .env.example .env
```
*(Default settings run out-of-the-box in simulation mode without external API keys).*

---

## 🧪 Running Tests & Benchmarks

### Run the Full Test Suite (316 Tests)
```bash
pytest -v
```

### Run the Benchmark Evaluation
```bash
python -m scripts.run_benchmark --records 500 --seed 20260821
```

### Run the Non-Stationary Shift Experiment
```bash
python -m scripts.run_non_stationary_benchmark --records 150 --seed 20260821
```

### Run the Chaos & Adversarial Fault Suite
```bash
python -m scripts.run_chaos_suite
```

---

## 💻 Starting the Application & Dashboard

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Open your browser at:
- **Control Room Dashboard**: [http://localhost:8000/dashboard](http://localhost:8000/dashboard)
- **Interactive OpenAPI Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
