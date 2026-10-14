"""Higgsfield CLI wrapper. The command is built here, so the argv is what we assert on."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.generators import image


def job(**overrides: object) -> image.ImageJob:
    data: dict[str, object] = {"prompt": "a worn desk under window light",
                               "aspect_ratio": "4:5"}
    data.update(overrides)
    return image.ImageJob(**data)


def test_model_choice() -> None:
    assert image.pick_model(has_soul=True, has_reference=True,
                            needs_text=True) == image.MODEL_SOUL
    assert image.pick_model(has_soul=False, has_reference=True,
                            needs_text=False) == image.MODEL_REFERENCE
    assert image.pick_model(has_soul=False, has_reference=True,
                            needs_text=True) == image.MODEL_DESIGN
    assert image.pick_model(has_soul=False, has_reference=False,
                            needs_text=False) == image.MODEL_DESIGN


def test_command_shape() -> None:
    argv = image.build_command(job())
    assert argv[:4] == ["higgsfield", "generate", "create", "gpt_image_2"]
    assert argv[argv.index("--prompt") + 1] == "a worn desk under window light"
    assert argv[argv.index("--aspect_ratio") + 1] == "4:5"
    assert "--wait" in argv and argv[-1] == "--json"


def test_command_carries_references_and_soul(tmp_path: Path) -> None:
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"0")
    argv = image.build_command(job(model=image.MODEL_SOUL, soul_id="soul-1",
                                   references=(str(reference),), quality="2k"))
    assert argv[3] == image.MODEL_SOUL
    assert argv[argv.index("--image") + 1] == str(reference)
    assert argv[argv.index("--soul-id") + 1] == "soul-1"
    assert argv[argv.index("--quality") + 1] == "2k"


def test_validation_rules(tmp_path: Path) -> None:
    with pytest.raises(FactoryError) as empty:
        image.build_command(job(prompt="  "))
    assert empty.value.code == "PROMPT_REQUIRED"
    with pytest.raises(FactoryError) as aspect:
        image.build_command(job(aspect_ratio="7:3"))
    assert aspect.value.code == "ASPECT_UNSUPPORTED"
    with pytest.raises(FactoryError) as missing:
        image.build_command(job(references=("/nope/ref.png",)))
    assert missing.value.code == "REFERENCE_MISSING"
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"0")
    with pytest.raises(FactoryError) as many:
        image.build_command(job(references=tuple([str(reference)] * 4)))
    assert many.value.code == "TOO_MANY_REFERENCES"
    with pytest.raises(FactoryError) as soul:
        image.build_command(job(model=image.MODEL_SOUL))
    assert soul.value.code == "SOUL_ID_REQUIRED"


def test_plan_makes_no_call() -> None:
    plan = image.plan(job(count=3, credit_cap=30))
    assert plan["binary"] == "higgsfield"
    assert plan["runs"] == 3
    assert plan["credit_cap_per_item"] == 30
    assert plan["argv"][0] == "higgsfield"
    assert "higgsfield generate create" in plan["command"]
    assert plan["note"] == "no network call was made"


def test_generate_uses_the_injected_runner() -> None:
    seen: list[list[str]] = []

    def runner(command, _timeout):
        seen.append(list(command))
        return {"id": "job-1", "results": [{"url": "https://cdn.test/a.png"}]}

    results = image.generate(job(count=2), runner=runner)
    assert len(results) == 2
    assert len(seen) == 2
    assert image.result_paths(results[0]) == ["https://cdn.test/a.png"]


def test_result_paths_is_shape_tolerant() -> None:
    assert image.result_paths({"outputs": ["a.png"]}) == ["a.png"]
    assert image.result_paths({"assets": [{"raw_url": "b.png"}]}) == ["b.png"]
    assert image.result_paths({"url": "c.png"}) == ["c.png"]
    assert image.result_paths({}) == []


def test_parse_output_errors() -> None:
    assert image.parse_output('[{"id": "x"}]')["id"] == "x"
    for body, code in (("", "HIGGSFIELD_EMPTY_OUTPUT"),
                       ("not json", "HIGGSFIELD_OUTPUT_INVALID"),
                       ("[]", "HIGGSFIELD_EMPTY_OUTPUT"),
                       ('"text"', "HIGGSFIELD_OUTPUT_INVALID")):
        with pytest.raises(FactoryError) as error:
            image.parse_output(body)
        assert error.value.code == code


def test_cli_gen_image_dry_run(isolated_home: Path,
                               capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    answers = isolated_home / "answers.json"
    answers.write_text(json.dumps({"platforms": ["instagram"], "count": 2,
                                   "prompt_final": "a worn desk under window light"}),
                       encoding="utf-8")
    assert main(["brief", "new", "--kind", "image", "--format", "product_shot",
                 "--answers", str(answers)]) == 0
    brief_id = json.loads(capsys.readouterr().out)["brief"]
    assert main(["gen", "image", "--brief", brief_id, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["model"] == "gpt_image_2"
    assert plan["aspect_ratio"] == "4:5"
    assert plan["runs"] == 2
    assert plan["credit_cap_per_item"] == 10
    assert plan["argv"][plan["argv"].index("--prompt") + 1] == (
        "a worn desk under window light")
    assert plan["note"] == "no network call was made"


def test_cli_gen_image_uses_the_soul_model_when_the_profile_has_one(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    import json as json_module
    profile_dir = isolated_home / "profiles" / "souled"
    profile_dir.mkdir(parents=True)
    (profile_dir / "persona.md").write_text("A studio voice.", encoding="utf-8")
    (profile_dir / "soul.json").write_text(
        json_module.dumps({"provider": "higgsfield", "soul_id": "soul-abc"}),
        encoding="utf-8")
    (profile_dir / "locale").write_text("en", encoding="utf-8")
    answers = isolated_home / "answers2.json"
    answers.write_text(json_module.dumps({"platforms": ["instagram"], "count": 1,
                                          "prompt_final": "portrait at a desk"}),
                       encoding="utf-8")
    assert main(["brief", "new", "--kind", "image", "--format", "product_shot",
                 "--profile", "souled", "--answers", str(answers)]) == 0
    brief_id = json_module.loads(capsys.readouterr().out)["brief"]
    assert main(["gen", "image", "--brief", brief_id, "--dry-run"]) == 0
    plan = json_module.loads(capsys.readouterr().out)
    assert plan["model"] == "text2image_soul_v2"
    assert plan["soul_id"] == "soul-abc"
    assert "--soul-id" in plan["argv"]


def test_cli_gate_run_with_boxes(isolated_home: Path,
                                 capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    code = main(["gate", "run", "--text", "a short honest note", "--format",
                 "threads_post", "--kind", "text", "--gate", "caption_safe_zone",
                 "--box", "220,300,600,200,hook"])
    out = capsys.readouterr().out
    assert code == 0
    assert "caption_safe_zone" in out
    failing = main(["gate", "run", "--text", "note", "--format", "threads_post",
                    "--kind", "text", "--gate", "caption_safe_zone",
                    "--box", "10,10,900,1800"])
    assert failing == 3
    assert "LEFT_OUT" in capsys.readouterr().out


def test_cli_gate_run_skips_safe_zone_without_boxes(
        isolated_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    assert main(["gate", "run", "--text", "note", "--format", "threads_post",
                 "--kind", "text", "--gate", "caption_safe_zone"]) == 0
    assert "BOXES_REQUIRED" in capsys.readouterr().out


def test_cli_rejects_a_malformed_box(isolated_home: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    from factory.cli import main
    assert main(["gate", "run", "--text", "note", "--gate", "caption_safe_zone",
                 "--box", "1,2,3"]) == 3
    assert "BOX_INVALID" in capsys.readouterr().err
