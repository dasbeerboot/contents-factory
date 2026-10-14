from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from factory.errors import FactoryError
from factory.publishers import media_host, publisher_for, required_env
from factory.publishers.base import PublishRequest
from factory.publishers.naver_blog import NaverBlogPublisher
from factory.publishers.youtube import usable_publish_at
from factory.spec.profile import PlatformConfig
from factory.spec.queue import Media
from tests.conftest import make_item

THREADS_ENV = {"THREADS_ACCESS_TOKEN": "secret-threads", "THREADS_USER_ID": "17890001"}
IG_ENV = {"IG_ACCESS_TOKEN": "secret-ig", "IG_USER_ID": "17890002"}
# No key: the TikTok bearer is fetched from `higgsfield auth token` per request.
TIKTOK_ENV: dict[str, str] = {}
YT_ENV = {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "csec",
          "YOUTUBE_REFRESH_TOKEN": "rtok"}
NAVER_ENV: dict[str, str] = {}
# Account ids are not secrets: they come from platforms.json (P0-SPEC §10 decision 6).
TIKTOK_SETTINGS = PlatformConfig(enabled=True, connector_id="connector-1")
NAVER_SETTINGS = PlatformConfig(enabled=True, blog_id="examplelog")
SECRETS = ("secret-threads", "secret-ig", "csec", "rtok")


def rendered(result) -> str:
    return result.render()


def assert_no_secrets(result) -> None:
    body = rendered(result)
    for secret in SECRETS:
        assert secret not in body


def test_registry_covers_five_platforms() -> None:
    from factory.publishers import REGISTRY
    assert set(REGISTRY) == {"threads", "instagram", "tiktok", "youtube", "naver_blog"}


def test_required_env_is_declared() -> None:
    assert required_env("threads") == ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
    assert required_env("tiktok") == ()
    assert required_env("naver_blog") == ()
    with pytest.raises(FactoryError):
        required_env("mastodon")


def test_threads_text_dry_run(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env=THREADS_ENV)
    result = publisher.publish(make_item(), dry_run=True)
    assert result.ok and result.dry_run and result.code == "DRY_RUN"
    steps = [request.step for request in result.requests]
    assert steps == ["create_container", "publish"]
    create = result.requests[0]
    assert create.method == "POST"
    assert create.url == "https://graph.threads.net/v1.0/17890001/threads"
    assert create.fields["media_type"] == "TEXT"
    assert create.fields["access_token"] == "<REDACTED:THREADS_ACCESS_TOKEN>"
    assert result.requests[1].url.endswith("/threads_publish")
    assert_no_secrets(result)


def test_threads_replies_extend_the_chain(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env=THREADS_ENV)
    item = make_item(extra={"replies": "first reply\nsecond reply"})
    result = publisher.publish(item, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps == ["create_container", "publish", "reply_1_container", "reply_1_publish",
                     "reply_2_container", "reply_2_publish"]
    assert result.requests[2].fields["reply_to_id"] == "<previous_post_id>"


def test_threads_video_requires_public_url(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env=THREADS_ENV)
    local = make_item(media=[Media(path="/tmp/reel.mp4", kind="video")])
    assert publisher.publish(local, dry_run=True).code == "MEDIA_PUBLIC_URL_REQUIRED"
    hosted = make_item(media=[Media(path="/tmp/reel.mp4", kind="video",
                                    public_url="https://cdn.test/reel.mp4")])
    result = publisher.publish(hosted, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps == ["create_container", "poll_container", "publish"]
    assert result.requests[0].fields["media_type"] == "VIDEO"
    assert result.requests[0].fields["video_url"] == "https://cdn.test/reel.mp4"
    assert "topic_tag" not in result.requests[0].fields


def test_threads_text_limit(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env=THREADS_ENV)
    result = publisher.publish(make_item(text="x" * 501), dry_run=True)
    assert result.ok is False and result.code == "TEXT_TOO_LONG"


def test_instagram_single_image_dry_run(isolated_home: Path) -> None:
    publisher = publisher_for("instagram", isolated_home, env=IG_ENV)
    item = make_item(platform="instagram", kind="image",
                     media=[Media(path="/tmp/a.jpg", public_url="https://cdn.test/a.jpg")])
    result = publisher.publish(item, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps == ["create_image", "poll_container", "media_publish", "permalink"]
    assert result.requests[0].url == ("https://graph.instagram.com/v23.0/17890002/media")
    assert result.requests[0].fields["image_url"] == "https://cdn.test/a.jpg"
    assert_no_secrets(result)


def test_instagram_carousel_children(isolated_home: Path) -> None:
    publisher = publisher_for("instagram", isolated_home, env=IG_ENV)
    item = make_item(platform="instagram", kind="image",
                     media=[Media(path=f"/tmp/{index}.jpg",
                                  public_url=f"https://cdn.test/{index}.jpg")
                            for index in range(3)])
    result = publisher.publish(item, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps[:4] == ["create_child_1", "create_child_2", "create_child_3",
                         "create_carousel"]
    assert result.requests[3].fields["media_type"] == "CAROUSEL"
    assert result.requests[3].fields["children"] == "<child_ids_joined_by_comma>"


def test_instagram_reels_flags(isolated_home: Path) -> None:
    publisher = publisher_for("instagram", isolated_home, env=IG_ENV)
    item = make_item(platform="instagram", kind="video",
                     media=[Media(path="/tmp/r.mp4", kind="video",
                                  public_url="https://cdn.test/r.mp4")],
                     extra={"ai_generated": "true", "cover_url": "https://cdn.test/c.jpg"})
    result = publisher.publish(item, dry_run=True)
    fields = result.requests[0].fields
    assert fields["media_type"] == "REELS"
    assert fields["share_to_feed"] == "true"
    assert fields["is_ai_generated"] == "true"
    assert fields["cover_url"] == "https://cdn.test/c.jpg"


def test_instagram_requires_media(isolated_home: Path) -> None:
    publisher = publisher_for("instagram", isolated_home, env=IG_ENV)
    result = publisher.publish(make_item(platform="instagram"), dry_run=True)
    assert result.code == "MEDIA_REQUIRED"


def test_tiktok_dry_run_shape(isolated_home: Path) -> None:
    publisher = publisher_for("tiktok", isolated_home, env=TIKTOK_ENV,
                              settings=TIKTOK_SETTINGS)
    item = make_item(platform="tiktok", kind="video", text="Hook line\nbody line",
                     media=[Media(path="/tmp/r.mp4", kind="video",
                                  public_url="https://cdn.test/r.mp4")])
    result = publisher.publish(item, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps == ["mcp_accounts", "media_import_url_1", "tiktok_prepare_publish",
                     "tiktok_publish", "tiktok_publish_status"]
    prepare = result.requests[2].fields["arguments"]
    assert prepare["mode"] == "DIRECT_POST"
    assert prepare["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert prepare["media_type"] == "VIDEO"
    assert prepare["title"] == "Hook line"
    assert prepare["video_url"] == "https://cdn.test/r.mp4"
    assert prepare["connector_id"] == "conn***"
    assert result.requests[3].fields["arguments"]["publish_token"] == (
        "<REDACTED:TIKTOK_PUBLISH_TOKEN>")
    # The bearer names its source rather than a credentials.env key, because there
    # is no key to paste any more.
    assert result.requests[0].fields["authorization"] == (
        "<REDACTED:higgsfield auth token>")
    assert_no_secrets(result)


def test_tiktok_photo_mode(isolated_home: Path) -> None:
    publisher = publisher_for("tiktok", isolated_home, env=TIKTOK_ENV,
                              settings=TIKTOK_SETTINGS)
    item = make_item(platform="tiktok", kind="image", text="Deck title",
                     media=[Media(path=f"/tmp/{index}.jpg",
                                  public_url=f"https://cdn.test/{index}.jpg")
                            for index in range(2)])
    result = publisher.publish(item, dry_run=True)
    steps = {request.step: request for request in result.requests}
    assert set(steps) == {"mcp_accounts", "media_import_url_1", "media_import_url_2",
                          "tiktok_prepare_publish", "tiktok_publish",
                          "tiktok_publish_status"}
    prepare = steps["tiktok_publish"].fields["arguments"]
    assert prepare["media_type"] == "PHOTO"
    assert prepare["photo_cover_index"] == 0
    assert prepare["auto_add_music"] is True
    assert len(prepare["photo_images"]) == 2


def test_youtube_dry_run_body(isolated_home: Path, tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"0" * 2048)
    publisher = publisher_for("youtube", isolated_home, env=YT_ENV)
    item = make_item(platform="youtube", kind="video", text="Title line\nthe description",
                     media=[Media(path=str(video), kind="video")],
                     scheduled_at="2036-10-15T12:00:00Z",
                     extra={"tags": "shipping,postmortem", "ai_generated": "true"})
    result = publisher.publish(item, dry_run=True)
    steps = [request.step for request in result.requests]
    assert steps == ["refresh_token", "resumable_init", "resumable_put"]
    body = result.requests[1].fields["body"]
    assert body["snippet"]["title"] == "Title line"
    assert body["snippet"]["tags"] == ["shipping", "postmortem"]
    assert body["snippet"]["categoryId"] == "27"
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["publishAt"] == "2036-10-15T12:00:00Z"
    assert body["status"]["containsSyntheticMedia"] is True
    assert result.requests[1].fields["x_upload_content_length"] == "2048"
    assert_no_secrets(result)


def test_youtube_past_slot_drops_schedule(isolated_home: Path, tmp_path: Path) -> None:
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"0")
    publisher = publisher_for("youtube", isolated_home, env=YT_ENV)
    item = make_item(platform="youtube", kind="video", text="Title line",
                     media=[Media(path=str(video), kind="video")],
                     scheduled_at="2020-01-01T12:00:00Z")
    body = result_body = publisher.publish(item, dry_run=True).requests[1].fields["body"]
    assert "publishAt" not in result_body["status"]
    assert body["status"]["privacyStatus"] == "private"


def test_usable_publish_at_filters() -> None:
    assert usable_publish_at("skip") is None
    assert usable_publish_at("") is None
    assert usable_publish_at("2036-01-01T00:00:00Z") == "2036-01-01T00:00:00Z"
    with pytest.raises(FactoryError):
        usable_publish_at("tomorrow")


def test_youtube_requires_video(isolated_home: Path) -> None:
    publisher = publisher_for("youtube", isolated_home, env=YT_ENV)
    result = publisher.publish(make_item(platform="youtube"), dry_run=True)
    assert result.code == "MEDIA_REQUIRED"


def test_naver_dry_run_job(isolated_home: Path) -> None:
    publisher = publisher_for("naver_blog", isolated_home, env=NAVER_ENV,
                              settings=NAVER_SETTINGS)
    item = make_item(platform="naver_blog", text="## Title\nx\n\n## Body\nbody\n\n## Tags\n#a",
                     scheduled_at="2026-10-15T12:07:00Z",
                     extra={"title": "A post title", "category": "Notes", "tags": "a,b"})
    result = publisher.publish(item, dry_run=True)
    assert [request.step for request in result.requests] == ["node_publish"]
    job = result.requests[0].fields["job"]
    assert job["blogId"] == "examplelog"
    assert job["category"] == "Notes"
    assert job["tags"] == ["a", "b"]
    assert job["publishAt"] == "2026-10-15T21:00"
    assert "markdown" not in job
    assert job["markdown_chars"] > 0


def test_tiktok_without_connector_id_fails(isolated_home: Path) -> None:
    publisher = publisher_for("tiktok", isolated_home, env=TIKTOK_ENV,
                              settings=PlatformConfig(enabled=True))
    item = make_item(platform="tiktok", kind="video", text="Hook",
                     media=[Media(path="/tmp/r.mp4", kind="video",
                                  public_url="https://cdn.test/r.mp4")])
    assert publisher.publish(item, dry_run=True).code == "CONNECTOR_ID_MISSING"


def test_naver_without_blog_id_fails(isolated_home: Path) -> None:
    publisher = publisher_for("naver_blog", isolated_home, env=NAVER_ENV,
                              settings=PlatformConfig(enabled=True))
    item = make_item(platform="naver_blog", extra={"title": "t", "category": "c",
                                                   "tags": "a"})
    assert publisher.publish(item, dry_run=True).code == "BLOG_ID_MISSING"


def test_naver_requires_category_and_tags(isolated_home: Path) -> None:
    publisher = publisher_for("naver_blog", isolated_home, env=NAVER_ENV,
                              settings=NAVER_SETTINGS)
    item = make_item(platform="naver_blog", extra={"title": "t"})
    assert publisher.publish(item, dry_run=True).code == "CATEGORY_REQUIRED"


def test_naver_output_parser() -> None:
    marker = "__FACTORY_NAVER_RESULT__"
    parsed = NaverBlogPublisher.parse_output(f"noise\n{marker}" '{"ok": true, "log_no": "1"}')
    assert parsed["log_no"] == "1"
    with pytest.raises(FactoryError) as error:
        NaverBlogPublisher.parse_output("nothing here")
    assert error.value.code == "NODE_RESULT_MISSING"


def test_platform_mismatch_is_refused(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env=THREADS_ENV)
    result = publisher.publish(make_item(platform="instagram"), dry_run=True)
    assert result.code == "PLATFORM_MISMATCH"


def test_media_host_plan_is_redacted() -> None:
    plan = media_host.plan(Media(path="/tmp/a.jpg"), profile="_example",
                           item_id="q_20261015120000_ab12",
                           env={"SUPABASE_URL": "https://db.test", "SUPABASE_BUCKET": "cf"})
    assert plan.url == "https://db.test/storage/v1/object/cf/_example/q_20261015120000_ab12/a.jpg"
    assert plan.public_url.startswith("https://db.test/storage/v1/object/public/cf/")
    assert plan.headers["authorization"] == "<REDACTED:SUPABASE_SERVICE_ROLE_KEY>"


def test_media_host_passes_through_existing_url() -> None:
    url = media_host.upload(Media(path="/tmp/a.jpg", public_url="https://cdn.test/a.jpg"),
                            profile="_example", item_id="q_20261015120000_ab12", env={})
    assert url == "https://cdn.test/a.jpg"


def test_publish_request_dict_roundtrip() -> None:
    request = PublishRequest("step", "POST", "https://x.test", {"a": 1}, "note")
    assert request.as_dict() == {"step": "step", "method": "POST", "url": "https://x.test",
                                 "fields": {"a": 1}, "note": "note"}


def test_live_publish_requires_env(isolated_home: Path) -> None:
    publisher = publisher_for("threads", isolated_home, env={})
    result = publisher.publish(make_item(), dry_run=False)
    assert result.ok is False and result.code == "ENV_MISSING"


def test_naver_node_dry_run_plan(isolated_home: Path) -> None:
    """The vendored node script must produce a plan without touching the browser."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    publisher = NaverBlogPublisher(isolated_home, env=NAVER_ENV, settings=NAVER_SETTINGS)
    job = {"blogId": "examplelog", "title": "One cached header cost three days",
           "category": "Notes", "tags": ["build", "postmortem"],
           "publishAt": "2026-10-20T21:00",
           "markdown": "## Title\nOne cached header cost three days\n\n## Body\n"
                       "The fix was one line of config.\n\n## Tags\n#build #postmortem\n"}
    result = publisher._invoke(job, dry_run=True)
    assert result["ok"] is True and result["code"] == "DRY_RUN"
    plan = result["plan"]
    assert plan["editor_url"] == "https://blog.naver.com/examplelog?Redirect=Write&"
    assert plan["reserved_stamp"] == "2026.10.20 21:00"
    assert plan["component_counts"]["documentTitle"] == 1
    assert plan["steps"] == ["start_cors_server", "ensure_editor", "inject_document",
                             "validate_editor", "publish_panel"]


def test_naver_node_rejects_bad_publish_minute(isolated_home: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    publisher = NaverBlogPublisher(isolated_home, env=NAVER_ENV, settings=NAVER_SETTINGS)
    job = {"blogId": "examplelog", "title": "t", "category": "Notes", "tags": ["a"],
           "publishAt": "2026-10-20T21:07",
           "markdown": "## Title\nt\n\n## Body\nb\n\n## Tags\n#a\n"}
    result = publisher._invoke(job, dry_run=True)
    assert result == {"ok": False, "code": "PUBLISH_MINUTE_NOT_TEN"}


NAVER_VENDOR = Path(__file__).resolve().parent.parent / "factory" / "publishers" / "naver"


def test_naver_cors_server_serves_the_bundle(isolated_home: Path) -> None:
    """The document reaches the editor over loopback, so size is not capped by the REPL."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    build = isolated_home / "state" / "naver" / "build"
    build.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"document": {"components": [{"@ctype": "text"}] * 500}})
    (build / "probe.json").write_text(body, encoding="utf-8")
    script = f'''
      import {{ startCorsServer }} from {json.dumps(str(NAVER_VENDOR / "cors_server.mjs"))};
      const cors = await startCorsServer({{ buildRoot: {json.dumps(str(build))} }});
      const ok = await fetch(`${{cors.origin}}/build/probe.json`);
      const payload = await ok.json();
      const outside = await fetch(`${{cors.origin}}/build/../../../etc/hosts`);
      const missing = await fetch(`${{cors.origin}}/build/nope.json`);
      console.log(JSON.stringify({{
        port_assigned: cors.port > 0,
        status: ok.status,
        cors: ok.headers.get("access-control-allow-origin"),
        components: payload.document.components.length,
        traversal: outside.status,
        missing: missing.status,
      }}));
      await cors.close();
    '''
    completed = subprocess.run(["node", "--input-type=module", "-e", script],
                               capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, completed.stderr[-400:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["port_assigned"] is True
    assert result["status"] == 200
    assert result["cors"] == "*"
    assert result["components"] == 500
    assert result["traversal"] in (403, 404)
    assert result["missing"] == 404


def test_naver_long_document_is_no_longer_capped(isolated_home: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    publisher = NaverBlogPublisher(isolated_home, env=NAVER_ENV, settings=NAVER_SETTINGS)
    body = "\n\n".join(f"Paragraph {index} carries enough words to grow the bundle."
                       for index in range(900))
    job = {"blogId": "examplelog", "title": "Long post", "category": "Notes",
           "tags": ["a"], "publishAt": "2026-10-20T21:00",
           "markdown": f"## Title\nLong post\n\n## Body\n{body}\n\n## Tags\n#a\n"}
    result = publisher._invoke(job, dry_run=True)
    assert result["ok"] is True
    assert result["plan"]["document_bytes"] > 90 * 1024
    assert result["plan"]["transport"] == "loopback_cors"
    assert result["plan"]["steps"][0] == "start_cors_server"
