from pathlib import Path
import makeitdown.cli as cli
from makeitdown.cli import _build_parser


def test_cli_wires_args_to_convert_tree(tmp_path, monkeypatch):
    captured = {}

    def fake_convert_tree(input_dir, output_dir, **kw):
        captured["input_dir"] = Path(input_dir)
        captured["output_dir"] = Path(output_dir)
        captured.update(kw)
        return {"succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
                "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []}

    monkeypatch.setattr(cli, "convert_tree", fake_convert_tree)
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)

    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "-o", "./out", "--ocr-engine", "cloud", "--cloud-consent",
                   "--cloud-token", "TKN", "--workers", "3", "--skip-existing"])
    assert rc == 0
    assert captured["output_dir"] == Path("./out")
    assert captured["ocr_engine"] == "cloud"
    assert captured["cloud_token"] == "TKN"
    assert captured["workers"] == 3
    assert captured["skip_existing"] is True


def test_cli_defaults_output_and_reads_token_from_env(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(
                            {"output_dir": Path(output_dir), **kw}) or
                        {"succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
                         "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []})
    monkeypatch.setenv("PADDLEOCR_AISTUDIO_TOKEN", "ENVTKN")

    src = tmp_path / "docs"; src.mkdir()
    rc = cli.main([str(src), "--cloud-consent"])
    assert rc == 0
    assert captured["output_dir"] == tmp_path / "docs_md"
    assert captured["cloud_token"] == "ENVTKN"
    assert captured["ocr_engine"] == "cloud"


def _report(**over):
    base = {"succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
            "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []}
    base.update(over)
    return base


def test_cli_quality_defaults_and_flags_wired(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(kw) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    src = tmp_path / "in"; src.mkdir()

    # defaults (use local engine to avoid cloud consent gate)
    cli.main([str(src), "--ocr-engine", "local"])
    assert captured["quality_check"] is True
    assert captured["quality_thresholds"].min_chars == 20
    assert captured["quality_thresholds"].garbled_ratio == 0.02

    assert captured["quality_thresholds"].min_confidence == 0.6

    # overrides
    cli.main([str(src), "--ocr-engine", "local", "--no-quality-check", "--warn-min-chars", "5",
              "--warn-min-chars-per-page", "80", "--warn-garbled-ratio", "0.1",
              "--warn-repeat-count", "100", "--warn-min-confidence", "0.7"])
    assert captured["quality_check"] is False
    t = captured["quality_thresholds"]
    assert (t.min_chars, t.min_chars_per_page, t.garbled_ratio, t.repeat_count) == (5, 80, 0.1, 100)
    assert t.min_confidence == 0.7


def test_cli_summary_includes_warned(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: _report(succeeded=3, warned=2))
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    src = tmp_path / "in"; src.mkdir()
    cli.main([str(src), "--ocr-engine", "local"])
    out = capsys.readouterr().out
    assert "warned=2" in out


def test_cli_structure_headings_builds_structurer(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(kw) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)

    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--ocr-engine", "local", "--structure-headings",
                   "--cloud-consent",
                   "--llm-base-url", "http://x/v1",
                   "--llm-model", "deepseek-chat", "--llm-api-key", "K"])
    assert rc == 0
    s = captured["structurer"]
    assert s is not None
    assert s.model == "deepseek-chat"
    assert s.base_url == "http://x/v1"


def test_cli_structure_headings_reads_llm_config_from_env(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(kw) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    monkeypatch.setenv("MAKEITDOWN_LLM_BASE_URL", "http://env/v1")
    monkeypatch.setenv("MAKEITDOWN_LLM_MODEL", "qwen")
    monkeypatch.setenv("MAKEITDOWN_LLM_API_KEY", "ENVK")

    src = tmp_path / "in"; src.mkdir()
    cli.main([str(src), "--ocr-engine", "local", "--structure-headings", "--cloud-consent"])
    assert captured["structurer"].model == "qwen"


def test_cli_structure_headings_fail_fast_without_config(monkeypatch, capsys, tmp_path):
    called = {"n": 0}

    def spy(input_dir, output_dir, **kw):
        called["n"] += 1
        return _report()

    monkeypatch.setattr(cli, "convert_tree", spy)
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    for var in ("MAKEITDOWN_LLM_BASE_URL", "MAKEITDOWN_LLM_MODEL", "MAKEITDOWN_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--structure-headings"])
    assert rc != 0
    assert called["n"] == 0
    assert "structure-headings" in capsys.readouterr().err


def test_cli_structure_headings_requires_external_processing_consent(monkeypatch, capsys, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(
        cli, "convert_tree",
        lambda *a, **kw: called.update(n=called["n"] + 1) or _report(),
    )
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([
        str(src), "--ocr-engine", "local", "--structure-headings",
        "--llm-base-url", "https://llm.example/v1",
        "--llm-model", "model", "--llm-api-key", "K",
    ])
    assert rc == 2 and called["n"] == 0
    assert "上传" in capsys.readouterr().err


def test_cli_default_passes_no_structurer(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(kw) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    src = tmp_path / "in"; src.mkdir()
    cli.main([str(src), "--ocr-engine", "local"])
    assert captured["structurer"] is None


def test_cli_summary_includes_structured_when_enabled(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: _report(succeeded=4, structured=4))
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    monkeypatch.setenv("MAKEITDOWN_LLM_BASE_URL", "http://env/v1")
    monkeypatch.setenv("MAKEITDOWN_LLM_MODEL", "qwen")
    monkeypatch.setenv("MAKEITDOWN_LLM_API_KEY", "ENVK")
    src = tmp_path / "in"; src.mkdir()
    cli.main([str(src), "--ocr-engine", "local", "--structure-headings", "--cloud-consent"])
    assert "structured=4" in capsys.readouterr().out


def test_cli_rejects_output_inside_input(monkeypatch, capsys, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(
        cli, "convert_tree",
        lambda *a, **kw: called.update(n=called["n"] + 1) or _report(),
    )
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--ocr-engine", "local", "-o", str(src / "out")])
    assert rc == 2 and called["n"] == 0
    assert "输出目录不能位于输入目录内部" in capsys.readouterr().err


def test_cross_check_flag_parses():
    args = _build_parser().parse_args(["indir", "--ocr-cross-check"])
    assert args.ocr_cross_check is True


def test_cross_check_defaults_off():
    args = _build_parser().parse_args(["indir"])
    assert args.ocr_cross_check is False


def test_cli_notes_actionable_skips(monkeypatch, capsys, tmp_path):
    skipped = [{"file": "a.doc", "reason": "needs WPS/Office or LibreOffice"}]
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: _report(skipped_unsupported=1,
                                                                    skipped=skipped))
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    src = tmp_path / "in"; src.mkdir()
    cli.main([str(src), "--ocr-engine", "local"])
    err = capsys.readouterr().err
    assert "1 file(s)" in err and "report" in err.lower()


def test_ocr_engine_defaults_to_cloud():
    args = _build_parser().parse_args(["indir"])
    assert args.ocr_engine == "cloud"
    assert args.cloud_consent is False
    assert args.cross_check_mode == "cloud"


def test_cloud_consent_flag_parses():
    args = _build_parser().parse_args(["indir", "--cloud-consent", "--cross-check-mode", "local"])
    assert args.cloud_consent is True
    assert args.cross_check_mode == "local"


def test_auto_with_local_available_not_blocked(monkeypatch, tmp_path, capsys):
    # auto + local installed + no consent → must NOT return 2 (local will run, no upload)
    import makeitdown.cli as cli
    monkeypatch.setattr(cli.LocalOCR, "is_available", staticmethod(lambda: True))
    called = {}
    monkeypatch.setattr(cli, "convert_tree", lambda *a, **k: called.update({"ran": True}) or {
        "succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
        "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []})
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--ocr-engine", "auto"])
    assert rc == 0 and called.get("ran") is True


def test_cloud_verifier_with_consent_prints_upload_notice(monkeypatch, tmp_path, capsys):
    import makeitdown.cli as cli
    monkeypatch.setattr(cli.LocalOCR, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(cli, "convert_tree", lambda *a, **k: {
        "succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
        "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []})
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--ocr-engine", "local", "--ocr-cross-check",
                   "--cross-check-mode", "cloud", "--cloud-consent"])
    assert rc == 0
    assert "上传" in capsys.readouterr().err


def test_strict_exits_nonzero_on_failures(monkeypatch, tmp_path):
    import makeitdown.cli as cli
    report = _report(succeeded=1, failed=2,
                     failures=[{"file": "a.pdf", "error": "boom"}])
    monkeypatch.setattr(cli, "convert_tree", lambda *a, **k: dict(report))
    monkeypatch.setattr(cli.LocalOCR, "is_available", staticmethod(lambda: True))
    src = tmp_path / "in"; src.mkdir()
    # default: partial failure is not fatal (degradation philosophy)
    assert cli.main([str(src), "--ocr-engine", "local"]) == 0
    # --strict: scripts/CI can rely on the exit code
    assert cli.main([str(src), "--ocr-engine", "local", "--strict"]) == 1
    # --strict with zero failures still exits 0
    report["failed"] = 0; report["failures"] = []
    assert cli.main([str(src), "--ocr-engine", "local", "--strict"]) == 0


def test_cli_rejects_non_directory_input(tmp_path, monkeypatch, capsys):
    # 单文件输入是本轮"空跑"的根因：必须在调用 convert_tree 前 fail-fast。
    called = {"n": 0}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)

    a_file = tmp_path / "one.pdf"
    a_file.write_text("x", encoding="utf-8")
    rc_file = cli.main([str(a_file), "--ocr-engine", "local"])

    missing = tmp_path / "nope"
    rc_missing = cli.main([str(missing), "--ocr-engine", "local"])

    assert rc_file == 2 and rc_missing == 2
    assert called["n"] == 0                      # 从未进入转换
    assert "目录" in capsys.readouterr().err     # 给了可读原因


def test_convert_tree_warns_on_empty_dir(tmp_path, capsys):
    from makeitdown.pipeline import convert_tree
    from makeitdown.quality import QualityThresholds
    empty = tmp_path / "empty"; empty.mkdir()
    report = convert_tree(empty, tmp_path / "out", ocr_engine="local",
                          ocr_model="PP-StructureV3", cloud_token=None,
                          workers=1, skip_existing=False, text_threshold=50,
                          report_path=tmp_path / "out" / "report.json",
                          quality_check=True, quality_thresholds=QualityThresholds(),
                          keep_images=False, structurer=None,
                          cross_check=False, cross_check_ratio=0.0,
                          cross_check_mode="cloud", cloud_consent=False,
                          mineru_token=None)
    assert report["succeeded"] == 0
    assert "0" in capsys.readouterr().err   # 明说"找到 0 个文件"，而非静默


def test_list_knobs_covers_flags_and_env():
    import json
    from makeitdown.cli import _list_knobs_json

    d = json.loads(_list_knobs_json())
    assert d["module"] == "makeitdown"
    assert "--ocr-engine" in d["flags"]
    assert "--ocr-cross-check" in d["flags"]
    assert "PADDLEOCR_AISTUDIO_TOKEN" in d["env"]
    assert "MINERU_API_TOKEN" in d["env"]


def test_list_knobs_flag_returns_zero_and_prints_json(capsys):
    import json

    from makeitdown.cli import main

    assert main(["--list-knobs"]) == 0
    json.loads(capsys.readouterr().out)  # valid JSON, no crash


def test_cli_pdf_reader_inspector_reaches_convert_tree(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda input_dir, output_dir, **kw: captured.update(kw) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)
    src = tmp_path / "in"
    src.mkdir()

    cli.main([str(src), "--ocr-engine", "local"])
    assert captured["pdf_reader_inspector"] is False

    cli.main([str(src), "--ocr-engine", "local", "--pdf-reader-inspector"])
    assert captured["pdf_reader_inspector"] is True
