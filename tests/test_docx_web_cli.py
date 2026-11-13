import io
import json
import time

import docx
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from humanizer.cli import main as cli_main
from humanizer.document import _reconstruct_runs, _tag_runs, docx_text, select_paragraphs
from humanizer.engine import humanize_docx
from tests.conftest import AI_TEXT

LONG = ("Furthermore, it is important to note that the proposed catalyst leverages a multifaceted mechanism, "
        "thereby enabling a 45% higher yield than the baseline under identical conditions.")


def _hyperlink(p, url, text):
    rid = p.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    h = OxmlElement("w:hyperlink")
    h.set(qn("r:id"), rid)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    h.append(r)
    p._p.append(h)


def _field(p, instr, shown):
    def run_with(child):
        r = OxmlElement("w:r")
        r.append(child)
        p._p.append(r)

    for kind in ("begin",):
        fc = OxmlElement("w:fldChar")
        fc.set(qn("w:fldCharType"), kind)
        run_with(fc)
    it = OxmlElement("w:instrText")
    it.text = instr
    run_with(it)
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    run_with(sep)
    t = OxmlElement("w:t")
    t.text = shown
    run_with(t)
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run_with(end)


@pytest.fixture
def sample_docx(tmp_path):
    d = docx.Document()
    d.add_heading("Results", level=1)
    p = d.add_paragraph()
    r = p.add_run(LONG + " ")
    r.font.size = Pt(12)
    b = p.add_run("Bold claim stays bold")
    b.bold = True
    b.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)
    p.add_run(" and the method underscores robust performance across many samples, as reported")
    sup = p.add_run("12")
    sup.font.superscript = True
    p.add_run(". See ")
    _hyperlink(p, "https://example.org/paper", "the paper")
    p.add_run(" and ")
    _field(p, ' ADDIN ZOTERO_ITEM CSL_CITATION {"x":1} ', "(Lee, 2021)")
    p.add_run(" for further details on how the approach was validated in practice.")
    d.add_paragraph("Short line.")
    d.add_heading("References", level=1)
    d.add_paragraph("Lee, K. (2021). Moreover, a pivotal study leverages multifaceted data. Journal 3, 1-9.")
    path = tmp_path / "in.docx"
    d.save(path)
    return path


def test_tag_reconstruct_roundtrip_is_identity(sample_docx):
    d = docx.Document(sample_docx)
    p = d.paragraphs[1]
    before = p.text
    tagged, vault = _tag_runs(p)
    assert "<b id=" in tagged and "__RUN_LOCKED_" in tagged
    assert "the paper" not in tagged  # hyperlink is locked whole
    _reconstruct_runs(p, tagged, vault)
    assert p.text == before


def test_select_paragraphs_skips_headings_short_and_references(sample_docx):
    d = docx.Document(sample_docx)
    texts = [p.text for p in select_paragraphs(d)]
    assert not any(t.startswith("Lee, K.") for t in texts)
    assert "Results" not in texts


def test_humanize_docx_preserves_structure(sample_docx, cfg, tmp_path):
    out = humanize_docx(sample_docx, cfg, dst=tmp_path / "out.docx")
    d = docx.Document(out)
    p = d.paragraphs[1]
    text = p.text
    assert "Furthermore" not in text and "leverages" not in text and "underscores" not in text
    assert "45%" in text and "the paper" in text and "(Lee, 2021)" in text
    bold = [r for r in p.runs if r.bold]
    assert bold and bold[0].text == "Bold claim stays bold"
    assert str(bold[0].font.color.rgb) == "C00000"
    assert any(r.font.superscript and r.text == "12" for r in p.runs)
    xml = p._p.xml
    assert "w:hyperlink" in xml and "ZOTERO_ITEM" in xml
    assert d.paragraphs[-1].text.startswith("Lee, K. (2021). Moreover, a pivotal study")  # references untouched
    assert d.paragraphs[0].text == "Results"


# --------------------------------------------------------------------- web


@pytest.fixture
def client():
    from starlette.testclient import TestClient

    from humanizer.web import app

    return TestClient(app)


def test_web_index_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "Humanizer Pro" in r.text and "btnRun" in r.text
    h = client.get("/api/health").json()
    assert h["llm_configured"] is False and h["version"]


def test_web_rejects_foreign_host(client):
    r = client.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_web_analyze_and_score(client):
    a = client.post("/api/analyze", json={"text": AI_TEXT}).json()
    assert a["ai_score"] > 60 and a["tells"] and a["readability"]["flesch"] is not None
    s = client.post("/api/score", json={"text": AI_TEXT}).json()
    assert s["ai_score"] == a["ai_score"] and s["words"] > 50


def test_web_job_lifecycle(client):
    job = client.post("/api/jobs", json={"text": AI_TEXT, "scientific": True}).json()["job_id"]
    for _ in range(100):
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    res = j["result"]
    assert res["final_score"] < res["initial_score"] and res["engine"] == "rules"
    assert not [c for c in res["integrity"] if c["severity"] == "error"]
    assert client.get("/api/jobs/nope").status_code == 404


def test_web_upload_and_docx_flow(client, sample_docx):
    r = client.post("/api/upload", files={"file": ("in.docx", sample_docx.read_bytes())}).json()
    assert r["session_docx"] and "Bold claim" in r["text"]
    res = client.post("/api/humanize", json={"text": r["text"], "session_docx": r["session_docx"]}).json()
    assert res["success"] and res["docx_ready"] and "Bold claim stays bold" in res["output"]
    dl = client.post("/api/download-docx", json={"text": res["output"], "session_docx": r["session_docx"]})
    assert dl.status_code == 200
    d = docx.Document(io.BytesIO(dl.content))
    assert any(run.bold for p in d.paragraphs for run in p.runs)


def test_web_upload_validation(client):
    assert client.post("/api/upload", files={"file": ("x.exe", b"MZ")}).status_code == 400
    t = client.post("/api/upload", files={"file": ("n.txt", "Plain text here.".encode())}).json()
    assert t["text"] == "Plain text here." and t["session_docx"] is None
    assert client.post("/api/humanize", json={"text": "hi", "session_docx": "../../etc"}).status_code == 400
    assert client.post("/api/jobs", json={"text": "  "}).status_code == 400


def test_web_download_synthesized_docx(client):
    r = client.post("/api/download-docx", json={"text": "# Title\n\nBody paragraph.\n\n- one\n- two"})
    d = docx.Document(io.BytesIO(r.content))
    assert [p.text for p in d.paragraphs] == ["Title", "Body paragraph.", "one", "two"]


# --------------------------------------------------------------------- cli


def test_cli_analyze_json(tmp_path, capsys):
    f = tmp_path / "a.txt"
    f.write_text(AI_TEXT, encoding="utf-8")
    assert cli_main(["--analyze", "--json", str(f)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ai_score"] > 60


def test_cli_humanizes_file_offline(tmp_path, capsys):
    f = tmp_path / "a.md"
    f.write_text("# Title\n\n" + AI_TEXT, encoding="utf-8")
    assert cli_main(["--local", "--seed", "3", "--no-report", str(f)]) == 0
    out = (tmp_path / "a_humanized.md").read_text(encoding="utf-8")
    assert out.startswith("# Title") and "Furthermore" not in out


def test_cli_text_json(capsys):
    assert cli_main(["--local", "--json", "--text", AI_TEXT]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["final_score"] < data["initial_score"] and data["engine"] == "rules"
    assert docx_text  # imported helper available
