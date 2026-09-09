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

## Data layer

There is no Docker Compose file. MongoDB Atlas is used for local development as
well as production, so the connection string is the only difference between the
two and there is no second setup to keep working.

Put your cluster's URI in `backend/.env`:

```
VERISHIELD_MONGO_URL=mongodb+srv://USER:PASSWORD@CLUSTER/?retryWrites=true&w=majority
VERISHIELD_MONGO_DB=verishield
```

Then create the first admin, which also generates the JWT signing key:

```
cd backend
.venv/Scripts/python.exe ../scripts/bootstrap_admin.py
```

Without that key the server refuses to issue or accept tokens -- there is
deliberately no default.

Without a reachable database the pipeline still verifies documents; it keeps no
audit trail and refuses every authenticated request, and `/health` says both.

Object storage (retaining submitted images) is optional. Point
`VERISHIELD_MINIO_ENDPOINT` at any S3-compatible service, or leave it unset and
images are simply not retained.

## Regenerating the tamper dataset

```powershell
python scripts\generate_tampered_dataset.py --variants 3
python scripts\calibrate_forensics.py
```
