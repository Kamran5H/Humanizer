import io
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docx
from starlette.testclient import TestClient
from humanizer.web import app

print("==================================================")
print("     HUMANIZER PRO WEB STUDIO API TEST SUITE      ")
print("==================================================")

client = TestClient(app)

# 1. TEST SERVE INDEX
print("\n[1/6] Testing GET / (Studio HTML Single Page)...")
resp = client.get("/")
assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
assert "Humanizer Pro Studio" in resp.text
assert "scoreDialCircle" in resp.text
assert "btnHumanize" in resp.text
print("  -> GET /: PASSED")

# 2. TEST SCORE API
print("\n[2/6] Testing POST /api/score...")
test_text = (
    "It is important to note that machine learning leverages multifaceted paradigms "
    "to delve into unprecedented transformative landscapes. Furthermore, this plays a crucial role."
)
resp = client.post("/api/score", json={"text": test_text})
assert resp.status_code == 200
data = resp.json()
print("  Score result:", data)
assert "ai_score" in data
assert "burstiness" in data
assert "perplexity" in data
assert data["ai_tells"] >= 2, f"Expected AI tells to be detected, got: {data['ai_tells']}"
print("  -> POST /api/score: PASSED")

# 3. TEST PROVIDERS API
print("\n[3/6] Testing GET /api/providers...")
resp = client.get("/api/providers")
assert resp.status_code == 200
providers = resp.json()
print(f"  Received {len(providers)} providers from API")
assert len(providers) >= 1
assert any(p["name"] == "Gemini" for p in providers)
print("  -> GET /api/providers: PASSED")

# 4. TEST RESET COOLDOWNS API
print("\n[4/6] Testing POST /api/reset-cooldowns...")
resp = client.post("/api/reset-cooldowns")
assert resp.status_code == 200
assert resp.json() == {"success": True}
print("  -> POST /api/reset-cooldowns: PASSED")

# 5. TEST FILE UPLOAD API
print("\n[5/6] Testing POST /api/upload (.txt and .docx)...")
txt_file = io.BytesIO(b"Synthetic biology explores novel genetic constructs to manufacture therapeutics.")
resp = client.post(
    "/api/upload",
    files={"file": ("sample.txt", txt_file, "text/plain")},
)
assert resp.status_code == 200
upload_data = resp.json()
print("  Upload response:", upload_data)
assert upload_data["success"] is True
assert "Synthetic biology" in upload_data["text"]
assert upload_data["words"] == 9
print("  -> POST /api/upload: PASSED")

# 6. TEST DOWNLOAD DOCX API & FAST HUMANIZE API
print("\n[6/6] Testing POST /api/humanize & POST /api/download-docx...")
humanize_resp = client.post(
    "/api/humanize",
    json={
        "text": "It is important to note that the experiment succeeded.",
        "scientific": True,
        "stealth_level": 1,
        "use_gemini": False,  # fast offline pipeline for deterministic test
    },
)
assert humanize_resp.status_code == 200
h_data = humanize_resp.json()
print("  Humanize response:", h_data)
assert h_data["success"] is True
assert "The experiment succeeded." in h_data["output"]

# Download DOCX test
download_resp = client.post(
    "/api/download-docx",
    json={"text": h_data["output"]},
)
assert download_resp.status_code == 200
assert "wordprocessingml.document" in download_resp.headers.get("content-type", "")
# Verify resulting bytes are valid docx
doc_in = docx.Document(io.BytesIO(download_resp.content))
assert len(doc_in.paragraphs) >= 1
assert "The experiment succeeded." in doc_in.paragraphs[0].text
print("  -> POST /api/humanize & POST /api/download-docx: PASSED")

print("\n==================================================")
print("     ALL 6 WEB STUDIO API TESTS PASSED 100%!     ")
print("==================================================")
