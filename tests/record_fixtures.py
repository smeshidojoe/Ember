"""Refresh the recorded responses in tests/fixtures/.

Run by hand when a service legitimately changes its format and the drift
tests start failing:

    python tests/record_fixtures.py

This is the ONLY part of the test suite that touches the network; the tests
themselves replay these files offline. Some sites may be unreachable from a
given IP — those fixtures are simply left untouched.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ember.http import make_context                       # noqa: E402
from ember.services import imgur, soundcloud, twitch      # noqa: E402

OUT = pathlib.Path(__file__).parent / "fixtures"
OUT.mkdir(parents=True, exist_ok=True)
ctx = make_context()

# что записываем: имя файла -> как получить (ленивые вызовы)
SOURCES = {
    "vimeo_config.json":
        lambda: ctx.get("https://player.vimeo.com/video/76979871/config").json(),
    "soundcloud_track.json":
        lambda: soundcloud._resolve(
            ctx, "https://soundcloud.com/eminemofficial/without-me-album-version"),
    "soundcloud_snip.json":
        lambda: soundcloud._resolve(
            ctx, "https://soundcloud.com/eminemofficial/the-way-i-am-album-version"),
    "imgur_single.json":
        lambda: ctx.get(f"{imgur._API}/media/dqOyj",
                        params={"client_id": imgur._CLIENT_ID,
                                "include": "media"}).json(),
    "twitch_clip.json":
        lambda: twitch._gql(ctx, {"query":
            '{ clip(slug: "GoodAlertBurritoTheTarFu") { title durationSeconds '
            'thumbnailURL viewCount createdAt broadcaster { displayName } '
            'videoQualities { quality sourceURL } } }'}),
    "twitch_vod.json":
        lambda: twitch._gql(ctx, {"query":
            '{ video(id: "2818023920") { title lengthSeconds '
            'previewThumbnailURL viewCount createdAt owner { displayName } } }'}),
}


def looks_broken(data) -> bool:
    """An error payload must never be saved over a good fixture."""
    if not data:
        return True
    if isinstance(data, dict):
        if data.get("errors") or data.get("error"):
            return True
        if not (data.get("data") or {}) and "data" in data:
            return True
    return False


def main() -> int:
    failed = []
    for name, fetch in SOURCES.items():
        try:
            data = fetch()
        except Exception as e:                      # сеть/блокировки — не падаем
            print(f"  {name:<26} пропущен ({type(e).__name__})")
            failed.append(name)
            continue
        if looks_broken(data):
            print(f"  {name:<26} ОТВЕТ С ОШИБКОЙ — файл не тронут")
            failed.append(name)
            continue
        (OUT / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {name:<26} обновлён ({(OUT / name).stat().st_size} байт)")

    print(f"\nобновлено: {len(SOURCES) - len(failed)} из {len(SOURCES)}")
    if failed:
        print("не обновлены:", ", ".join(failed))
    print("\nТеперь: python -m pytest tests/test_drift.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
