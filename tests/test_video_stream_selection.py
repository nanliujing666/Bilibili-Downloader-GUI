"""视频流选择单元测试

覆盖 bilibili-api 17.4.1 `detect_best_streams` 的已知缺陷：
HEVC 流的 codecs 字段为 ``hvc1...``，库只匹配 ``hev``，会把编码识别为 None，
在多条同清晰度流排序时触发 ``'NoneType' object has no attribute 'value'``。
"""
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from bilibili_api.video import (  # noqa: E402
    VideoDownloadURLDataDetecter,
    VideoQuality,
)

from src.api.video_api import VideoApiClient  # noqa: E402


def _dash(videos, audios=None):
    return {
        "dash": {
            "video": videos,
            "audio": audios or [],
        }
    }


class TestSelectStreams(unittest.TestCase):
    def setUp(self) -> None:
        self.client = VideoApiClient()

    def test_hevc_unknown_codec_does_not_crash(self):
        """多编码同清晰度（含 hvc1）时不应崩溃，并按 AV1 > AVC > HEVC 选择"""
        data = _dash(
            videos=[
                {"id": 80, "baseUrl": "https://v/avc", "codecs": "avc1.640033"},
                {"id": 80, "baseUrl": "https://v/hevc", "codecs": "hvc1.1.6.L150.90"},
                {"id": 80, "baseUrl": "https://v/av1", "codecs": "av01.0.08M.08"},
            ],
            audios=[{"id": 30280, "baseUrl": "https://a/192"}],
        )
        detector = VideoDownloadURLDataDetecter(data)
        video, audio = self.client._select_streams(detector, VideoQuality._1080P)

        self.assertIsNotNone(video)
        self.assertEqual(video.url, "https://v/av1")
        self.assertIsNotNone(audio)
        self.assertEqual(audio.url, "https://a/192")

    def test_respects_max_quality(self):
        """请求 720P 时不应返回 1080P 流"""
        data = _dash(
            videos=[
                {"id": 80, "baseUrl": "https://v/1080", "codecs": "avc1.640033"},
                {"id": 64, "baseUrl": "https://v/720", "codecs": "avc1.640033"},
            ],
            audios=[{"id": 30232, "baseUrl": "https://a/132"}],
        )
        detector = VideoDownloadURLDataDetecter(data)
        video, _ = self.client._select_streams(detector, VideoQuality._720P)

        self.assertIsNotNone(video)
        self.assertEqual(video.url, "https://v/720")

    def test_prefers_avc_when_av1_missing(self):
        """没有 AV1 时优先 AVC，而不是编码未知的 HEVC"""
        data = _dash(
            videos=[
                {"id": 80, "baseUrl": "https://v/hevc", "codecs": "hvc1.1.6.L150.90"},
                {"id": 80, "baseUrl": "https://v/avc", "codecs": "avc1.640033"},
            ],
        )
        detector = VideoDownloadURLDataDetecter(data)
        video, _ = self.client._select_streams(detector, VideoQuality._1080P)

        self.assertIsNotNone(video)
        self.assertEqual(video.url, "https://v/avc")


if __name__ == "__main__":
    unittest.main()
