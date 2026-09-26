"""Stock footage gating + Pexels/Pixabay response parsing (mocked HTTP)."""
from unittest import mock

import pytest

import providers.stock as stock
from core.errors import ProviderError
from core.settings import set_setting


@pytest.fixture(autouse=True)
def _tmp_search_cache(tmp_path, monkeypatch):
    """The 24h search cache lives on disk; keep tests from reading each other's
    entries (a stale hit would skip the mocked HTTP call and break the test)."""
    d = tmp_path / "search-cache"
    monkeypatch.setattr(stock, "_cache_dir",
                        lambda: (d.mkdir(parents=True, exist_ok=True) or d))
    stock._PIXABAY_HITS.clear()


def setup_function(_):
    set_setting("image.pexels_key", "")
    set_setting("image.pixabay_key", "")
    set_setting("video.stock_sources", "pexels")
    set_setting("video.asset_mode", "auto")


def test_mode_gating():
    assert not stock.stock_enabled()  # no key, auto

    set_setting("image.pexels_key", "k")
    assert stock.stock_enabled()  # auto + key

    set_setting("video.asset_mode", "image")
    assert not stock.stock_enabled()  # images forced even with key

    set_setting("video.asset_mode", "stock")
    assert stock.stock_enabled()

    set_setting("image.pexels_key", "")
    assert not stock.stock_enabled()  # stock mode without key = off


def test_fetch_requires_key(tmp_path):
    with pytest.raises(ProviderError):
        stock.fetch_stock_video("ocean waves", tmp_path / "v.mp4")


def test_fetch_picks_smallest_sufficient_mp4(tmp_path):
    set_setting("image.pexels_key", "k")
    files = [
        {"file_type": "video/mp4", "width": 2160, "height": 3840, "link": "http://x/4k"},
        {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "http://x/720"},
        {"file_type": "video/mp4", "width": 480, "height": 854, "link": "http://x/480"},  # too narrow
        {"file_type": "video/webm", "width": 1080, "height": 1920, "link": "http://x/webm"},
    ]
    out = tmp_path / "v.mp4"
    search = mock.Mock()
    search.raise_for_status = lambda: None
    search.json.return_value = {"videos": [{"url": "https://pexels.com/video/bermuda-triangle-deep-1",
                                            "title": "bermuda triangle mystery deep",
                                            "video_files": files}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"MP4DATA"
    with mock.patch.object(stock.httpx, "get",
                           side_effect=[search, dl]) as get:
        got = stock.fetch_stock_video("the bermuda triangle mystery deep", out)
    assert got == out and out.read_bytes() == b"MP4DATA"
    assert get.call_args_list[1].args[0] == "http://x/720"  # smallest ≥720 portrait


def test_fetch_no_results_raises(tmp_path):
    search = mock.Mock()
    search.raise_for_status = lambda: None
    search.json.return_value = {"videos": []}
    with mock.patch.object(stock.httpx, "get", return_value=search):
        with pytest.raises(ProviderError):
            stock.fetch_stock_video("nothing matches", tmp_path / "v.mp4")


def test_fetch_scene_stock_falls_back_to_visual_prompt(tmp_path):
    """Narration with no searchable subject must fall back to the visual prompt."""
    set_setting("image.pexels_key", "k")
    files = [{"file_type": "video/mp4", "width": 1080, "height": 1920,
              "link": "http://x/1080"}]
    miss = mock.Mock()
    miss.raise_for_status = lambda: None
    miss.json.return_value = {"videos": []}
    hit = mock.Mock()
    hit.raise_for_status = lambda: None
    hit.json.return_value = {"videos": [
        {"id": 7, "url": "https://pexels.com/video/brain-neurons-7",
         "title": "brain neurons", "video_files": files}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"CLIP"

    scene = {"scene_number": 4, "stock_query": "", "narration": "False memories also happen",
             "visual_prompt": "brain neurons firing under a microscope"}
    def _get(url, **kw):
        if url == "http://x/1080":
            return dl
        q = (kw.get("params") or {}).get("query", "")
        return hit if "brain neurons" in q else miss

    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        out = tmp_path / "s4.mp4"
        stock.fetch_scene_stock(scene, out)

    assert out.read_bytes() == b"CLIP"


def test_fetch_skips_excluded_ids(tmp_path):
    """A project must never reuse the same clip across scenes (project 246)."""
    set_setting("image.pexels_key", "k")
    files = [{"file_type": "video/mp4", "width": 1080, "height": 1920,
              "link": "http://x/1080"}]
    search = mock.Mock()
    search.raise_for_status = lambda: None
    search.json.return_value = {"videos": [
        {"id": 111, "url": "https://pexels.com/video/lion-savanna-1",
         "title": "lion savanna", "video_files": files},
        {"id": 222, "url": "https://pexels.com/video/lion-savanna-2",
         "title": "lion savanna", "video_files": files},
    ]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"SECOND"

    with mock.patch.object(stock.httpx, "get", side_effect=[search, dl]):
        out = tmp_path / "v.mp4"
        stock.fetch_stock_video("lion savanna", out, exclude_ids={111})

    assert out.read_bytes() == b"SECOND"          # 111 skipped, 222 used
    assert (tmp_path / "v.pexels-id").read_text() == "222"


# ── multi-source: Pexels + Pixabay ────────────────────────────────

def test_stock_sources_only_includes_sources_that_have_a_key():
    assert stock.stock_sources() == []                     # nothing configured

    set_setting("image.pexels_key", "p")
    assert stock.stock_sources() == ["pexels"]             # the old default

    set_setting("video.stock_sources", "pexels,pixabay")
    assert stock.stock_sources() == ["pexels"]             # pixabay has no key

    set_setting("image.pixabay_key", "b")
    assert stock.stock_sources() == ["pexels", "pixabay"]

    set_setting("video.stock_sources", "pixabay")
    assert stock.stock_sources() == ["pixabay"]

    set_setting("image.pixabay_key", "")
    # a typo or a removed key must never silently leave us with no source
    assert stock.stock_sources() == ["pexels"]


def test_pixabay_parses_tags_and_picks_smallest_sufficient(tmp_path):
    set_setting("image.pixabay_key", "b")
    set_setting("video.stock_sources", "pixabay")
    search = mock.Mock()
    search.raise_for_status = lambda: None
    search.json.return_value = {"hits": [{
        "id": 42, "tags": "mount everest, nepal, himalaya",
        "pageURL": "https://pixabay.com/videos/id-42/",
        "videos": {
            "large": {"url": "http://x/4k.mp4", "width": 3840, "height": 2160},
            "medium": {"url": "http://x/1080.mp4", "width": 1920, "height": 1080},
            "tiny": {"url": "http://x/480.mp4", "width": 640, "height": 360},
        }}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"PIXABAY"
    with mock.patch.object(stock.httpx, "get", side_effect=[search, dl]) as get:
        out = stock.fetch_stock_video("mount everest", tmp_path / "v.mp4",
                                      orientation="landscape")
    assert out.read_bytes() == b"PIXABAY"
    # smallest file >=1280 wide, landscape
    assert get.call_args_list[1].args[0] == "http://x/1080.mp4"
    assert (tmp_path / "v.pixabay-id").read_text() == "42"


def test_pixabay_is_asked_only_the_primary_query(tmp_path):
    """Pixabay's terms forbid 'lots of automated queries', so it never gets the
    shortened fallback chain that Pexels gets."""
    set_setting("image.pixabay_key", "b")
    set_setting("video.stock_sources", "pixabay")
    asked = []
    miss = mock.Mock()
    miss.raise_for_status = lambda: None
    miss.json.return_value = {"hits": []}

    def _get(url, **kw):
        if url.startswith("https://pixabay.com"):
            asked.append((kw.get("params") or {}).get("q"))
        return miss

    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        with pytest.raises(ProviderError):
            stock.fetch_stock_video("lion savanna pride", tmp_path / "v.mp4")
    assert asked == ["lion savanna pride"]      # never "lion savanna" / "lion"


def test_search_is_cached_so_a_repeat_costs_no_request(tmp_path):
    """Pixabay requires 24h caching; it also keeps our volume defensible."""
    set_setting("image.pexels_key", "p")
    search = mock.Mock()
    search.raise_for_status = lambda: None
    search.json.return_value = {"videos": [{
        "id": 9, "url": "https://pexels.com/video/ocean-wave-9",
        "title": "ocean wave",
        "video_files": [{"file_type": "video/mp4", "width": 1080,
                         "height": 1920, "link": "http://x/a.mp4"}]}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"CLIP"
    urls = []

    def _get(url, **kw):
        urls.append(url)
        return search if url.startswith("https://api.pexels.com") else dl

    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        stock.fetch_stock_video("ocean wave", tmp_path / "v1.mp4")
        stock.fetch_stock_video("ocean wave", tmp_path / "v2.mp4")

    searches = [u for u in urls if u.startswith("https://api.pexels.com")]
    assert len(searches) == 1                   # second fetch reused the cache
    assert (tmp_path / "v2.mp4").read_bytes() == b"CLIP"


def test_exclusion_is_provider_scoped(tmp_path):
    """Pexels id 5 and Pixabay id 5 are different videos: excluding one must
    not block the other."""
    set_setting("image.pexels_key", "p")
    set_setting("image.pixabay_key", "b")
    set_setting("video.stock_sources", "pexels,pixabay")
    px = mock.Mock()
    px.raise_for_status = lambda: None
    px.json.return_value = {"videos": []}
    pb = mock.Mock()
    pb.raise_for_status = lambda: None
    pb.json.return_value = {"hits": [{
        "id": 5, "tags": "ocean wave", "pageURL": "https://pixabay.com/videos/id-5/",
        "videos": {"medium": {"url": "http://x/pb.mp4", "width": 1080,
                              "height": 1920}}}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"PIXABAY"

    def _get(url, **kw):
        if url.startswith("https://api.pexels.com"):
            return px
        return pb if url.startswith("https://pixabay.com") else dl

    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        out = stock.fetch_stock_video("ocean wave", tmp_path / "v.mp4",
                                      exclude_ids={"pexels:5"})
    assert out.read_bytes() == b"PIXABAY"


def test_junk_only_prompt_yields_no_query():
    """Frame furniture must not become a search request (measured: 43 of 67
    scenes in project 290 produced 'framing text watermarks subtitles')."""
    assert stock.extract_stock_query_from_prompt(
        "Horizontal 1920x1080 framing text watermarks subtitles") == ""
    assert stock.extract_stock_query_from_prompt("") == ""
    assert stock.extract_stock_query_from_prompt(
        "Vertical 1080x1920 cinematic shot: a goby fish on coral") == "goby fish coral"


def _both_sources():
    """Enable both providers and return (hosts_list, fake_httpx_get)."""
    set_setting("image.pexels_key", "p")
    set_setting("image.pixabay_key", "b")
    set_setting("video.stock_sources", "pexels,pixabay")
    px = mock.Mock()
    px.raise_for_status = lambda: None
    px.json.return_value = {"videos": [{
        "id": 1, "url": "https://pexels.com/video/ocean-wave-1",
        "title": "ocean wave",
        "video_files": [{"file_type": "video/mp4", "width": 1080,
                         "height": 1920, "link": "http://x/px.mp4"}]}]}
    pb = mock.Mock()
    pb.raise_for_status = lambda: None
    pb.json.return_value = {"hits": [{
        "id": 2, "tags": "ocean wave", "pageURL": "https://pixabay.com/videos/id-2/",
        "videos": {"medium": {"url": "http://x/pb.mp4", "width": 1080,
                              "height": 1920}}}]}
    dl = mock.Mock()
    dl.raise_for_status = lambda: None
    dl.content = b"CLIP"
    hosts = []

    def _get(url, **kw):
        if url.startswith("https://api.pexels.com"):
            hosts.append("pexels")
            return px
        if url.startswith("https://pixabay.com"):
            hosts.append("pixabay")
            return pb
        return dl

    return hosts, _get


def test_both_sources_are_searched_even_when_the_first_has_a_hit(tmp_path):
    """Mode B: a second index only adds value if it is actually consulted, so
    a Pexels hit must not stop Pixabay from being asked."""
    hosts, _get = _both_sources()
    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        out = stock.fetch_stock_video("ocean wave", tmp_path / "v.mp4")
    assert "pexels" in hosts and "pixabay" in hosts
    assert out.read_bytes() == b"CLIP"


def test_pixabay_can_win_a_tie(tmp_path, monkeypatch):
    """Equal relevance must not always resolve to the first source, or the
    second index would be decorative."""
    _, _get = _both_sources()
    monkeypatch.setattr(stock.random, "choice", lambda seq: seq[-1])  # last = pixabay
    with mock.patch.object(stock.httpx, "get", side_effect=_get):
        out = stock.fetch_stock_video("ocean wave", tmp_path / "v.mp4")
    assert (tmp_path / "v.pixabay-id").read_text() == "2"
