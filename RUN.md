# Running VeriShield

## Backend API

In the VS Code terminal (Ctrl+`), from the repo root:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1        # PowerShell. Use .venv\Scripts\activate.bat in cmd
python -m uvicorn app.main:app --reload --port 8000
```

If PowerShell blocks the activate script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Or skip activation entirely and call the interpreter directly:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

Then open:

- http://localhost:8000/docs  -- interactive API, upload a document and see the full result
- http://localhost:8000/health -- what this deployment can and cannot do right now

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest              # all of them
.\.venv\Scripts\python.exe -m pytest -v           # one line per test
.\.venv\Scripts\python.exe -m pytest tests/test_rulebooks.py   # one file
```

## Evaluation on your own documents

```powershell
cd backend
.\.venv\Scripts\python.exe ..\scripts\evaluate.py
```

Reads every image under `backend/data/datasets/raw/<type>/`, treats the folder
name as the ground-truth label, and reports accuracy, extraction rates,
decision distribution, confidence calibration and per-stage timings.

## Data layer (optional)

Start Docker Desktop first, then from the repo root:

```powershell
docker compose up -d
docker compose ps
```

Without it the pipeline still verifies documents; it just keeps no audit trail.
`/health` says so explicitly.

## Regenerating the tamper dataset

```powershell
python scripts\generate_tampered_dataset.py --variants 3
python scripts\calibrate_forensics.py
```
