# VeriShield-AI: Detailed Project Workflow & Architecture Report

As requested, here is a detailed, comprehensive report on the VeriShield-AI project, covering its workflow, technology stack, system architecture, and overall operations. This document breaks down the processes to provide a clear understanding of how the system functions from end-to-end.

---

## 1. Project Overview

**VeriShield-AI** is a comprehensive **Multimodal Identity and Credential Risk Assessment** system. Its primary purpose is to assess the risk of fraud in uploaded identity documents (such as Aadhaar cards, PAN cards, Passports, and Certificates).

**Core Philosophy:** VeriShield-AI does not simply declare a document as "genuine" or "fake" in a binary manner. Instead, it aggregates evidence from a series of independent checks across different layers, calculates a comprehensive "Risk Score", and if a high risk is detected, it flags the document for human review. It always provides a fully traceable, detailed reasoning behind every score it generates, ensuring absolute transparency.

---

## 2. Technology Stack (Tools & Technologies Used)

The project leverages a robust and modern stack, where each tool serves a specific and critical purpose:

### **Backend (Data & Core Logic)**
* **Python:** The core programming language used to write the risk engine, verification logic, and orchestrate the entire pipeline.
* **FastAPI:** Used for building the REST APIs. It ensures high performance and automatically generates interactive API documentation (available at `/docs`) for easy testing and integration.
* **Uvicorn:** A fast ASGI server used to serve the FastAPI application.
* **MongoDB (Atlas / Local):** The database layer handling the storage of users, submitted cases, and document assessment details. Note that verification itself is pure computation over an image; the database is used solely for audit trails and authentication.
* **Pytest:** Used for automated testing of the rulebooks and logic. The suite currently boasts 168 passing tests, ensuring system stability.

### **Machine Learning & Computer Vision**
* **PaddleOCR:** Utilized for Optical Character Recognition (OCR). It extracts raw text and data from document images, which is then passed to the validation layers.
* **RetinaFace & ArcFace:** Employed for Face Verification (Layer 6). RetinaFace detects the face within the document and selfie, while ArcFace computes embeddings to match the identities.
* **ResNet-18 (Patch Classifier):** A trained model used for document tampering detection (Forensics). *Note: This layer is currently disabled as it has been measured to perform at chance on real-world data and needs further calibration with a synthetic tampered dataset.*

### **Frontend (User Interface)**
* **React:** The JavaScript library used for building the user dashboard and interfaces.
* **TailwindCSS:** A utility-first CSS framework used to rapidly design modern, responsive, and sleek user interfaces.

---

## 3. System Workflow (How It Works)

When a user submits a document, it doesn't just undergo a single check. The image is processed through an extensive **8-Layer Pipeline**. Every stage contributes its findings as "Signals" (evidence).

Here is the detailed breakdown of the workflow:

### **Layer 1: Ingest (Data Ingestion)**
* The system accepts document photographs (and an optional selfie).
* It decodes the image formats (including native HEIC support from modern smartphones) and performs sanity checks on the image resolution to ensure it's suitable for processing.

### **Layer 2: Classify (Document Identification)**
* The system analyzes the image to classify the document type (e.g., Aadhaar, PAN, Passport).
* It employs keyword/pattern-based classification. Crucially, if it cannot confidently identify the document, it returns an honest `UNKNOWN` rather than forcing a potentially incorrect guess.

### **Layer 3: OCR & Extract (Text Reading)**
* **OCR:** PaddleOCR reads the text from the image, generating text strings along with per-line confidence scores.
* **Extraction:** The system extracts specific structured fields like MRZ lines, PAN numbers, and Aadhaar numbers. *Security Note: Aadhaar and PAN numbers are aggressively masked in all system outputs.*

### **Layer 4: Validate (Rulebooks and Checksums)**
This layer applies deterministic, mathematical rules to the extracted data:
* **Aadhaar:** Validates the Aadhaar number using the Verhoeff checksum algorithm, catching single-digit typos or adjacent transpositions.
* **PAN Card:** Verifies the structural format, holder category, and cross-checks the surname initial (the 5th character of the PAN must match the surname on the card).
* **Passport (MRZ):** Performs ICAO 9303 check-digit arithmetic. It calculates composite digits and verifies dates, rendering hand-edited MRZs easily detectable.
* **Certificates:** Compares numeric scores with printed words for consistency.

### **Layer 5: Forensics (Tamper Detection)**
* Evaluates the image pixels and metadata to detect digital tampering or photoshopped patches.
* *Current Status: This layer is temporarily turned off because initial calibration against real and tampered documents showed high false-positive rates. It awaits a fully labeled tampered dataset for accurate threshold tuning.*

### **Layer 6: Face Verification (Identity Matching)**
* Detects the face on the ID document and compares it with the submitted selfie or across multiple documents to ensure the person is the same.

### **Layer 7: Cross-Document Consistency**
* If multiple documents are submitted (e.g., PAN and Aadhaar), this layer cross-checks consistency.
* It uses fuzzy name matching and Date of Birth (DOB) comparisons to tolerate slight transliteration differences or clerical spelling variations.

### **Layer 8: Registry (Authority Verification)**
* Designed as a swappable interface to connect with government registries (like UIDAI or Income Tax portals).
* Currently, it uses a mock/synthetic development registry to verify if the extracted details exist in the authority records.

---

## 4. The Risk Engine (How Decisions Are Made)

All signals from the 8 layers flow into the **Risk Engine**. The engine uses a transparent additive weighting mechanism (no black boxes).

**The Signal Contract:**
Every stage emits `Signal` objects with the following statuses:
* **PASS:** The check was satisfied.
* **WARN:** Suspicious, but not strictly disqualifying.
* **FAIL:** The check failed entirely.
* **SKIP:** The check did not apply (this is never treated as passing evidence).
* **ERROR:** The check could not run due to missing information.

**Decision Logic:**
The Risk Engine aggregates these signals to compute a final score. 
* **Blocking Signals:** An expired passport might score a low fraud risk (because it is genuinely real), but it triggers a `blocking` signal. This routes it immediately to `MANUAL_REVIEW` instead of an automatic acceptance.
* **Evidence Coverage:** The system calculates how much expected evidence was actually checked. If coverage is below 55% (e.g., due to extreme blurriness), the engine refuses to return an `ACCEPT` decision, escalating the case to human review regardless of the low risk score.

---

## 5. Quick Start (How to Run Locally)

To set up and run the system on your local development machine, follow these steps:

**1. Starting the Backend API Server:**
```powershell
cd backend
python -m venv .venv

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Install standard dependencies
pip install -r requirements.txt

# Run the test suite
pytest

# Start the server
python -m uvicorn app.main:app --reload --port 8000
```
You can access the interactive API interface at `http://localhost:8000/docs` to test document uploads.

**2. Installing ML Dependencies (Optional for Full OCR/Face support):**
```powershell
cd backend
pip install -r requirements-ml.txt
```
*(Note: These models are large and will dynamically load/release based on VRAM availability).*

**3. Database Configuration:**
* Create a `.env` file in the `backend/` directory.
* Set your `VERISHIELD_MONGO_URL` to point to a MongoDB cluster (Atlas or local).
* Run `python scripts/bootstrap_admin.py` to create the initial admin user and generate a JWT signing key.

---

## Conclusion

**VeriShield-AI** is a highly architected, transparent risk assessment engine. Rather than relying solely on opaque AI classifications, it prioritizes deterministic mathematical checks (like MRZ and Verhoeff algorithms) combined with strategic ML (OCR and Face Matching). Because it explains exactly *why* a document is risky, it is perfectly suited for financial, compliance, and high-security environments where auditability and accountability are mandatory.
